from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from cerno.config import DiscoveryProcessingConfig
from cerno.llm import LLMClient
from cerno.services.reingest import ApprovalPayload, ColumnSpec, FileSpec, LinkSpec

CorrectionClassification = Literal["minor", "structural"]
CorrectionTargetType = Literal["file", "column", "link", "data_doc"]

MINOR_OPS = {
    "set_friendly_name",
    "set_file_description",
    "set_column_description",
    "set_usage_note",
    "set_glossary_term",
    "set_starter_question",
}
STRUCTURAL_OPS = {
    "set_dtype",
    "set_header_row",
    "scale_column",
    "rename_column",
    "add_column",
    "remove_column",
    "add_link",
    "remove_link",
    "edit_link",
}

SCALE_FACTORS = {
    "thousand": 1_000.0,
    "thousands": 1_000.0,
    "k": 1_000.0,
    "lakh": 100_000.0,
    "lakhs": 100_000.0,
    "lac": 100_000.0,
    "lacs": 100_000.0,
    "crore": 10_000_000.0,
    "crores": 10_000_000.0,
    "million": 1_000_000.0,
    "millions": 1_000_000.0,
    "billion": 1_000_000_000.0,
    "billions": 1_000_000_000.0,
}


class CorrectionOperation(BaseModel):
    op_id: str
    target_type: CorrectionTargetType
    target: dict[str, str] = Field(default_factory=dict)
    op_type: str
    before_value: Any = None
    after_value: Any = None
    description: str
    classification: CorrectionClassification
    transform: dict[str, Any] | None = None


class SchemaCorrectionPatch(BaseModel):
    instruction: str = ""
    operations: list[CorrectionOperation] = Field(default_factory=list)


CORRECTION_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["operations"],
    "properties": {
        "operations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "op_id",
                    "target_type",
                    "target",
                    "op_type",
                    "before_value",
                    "after_value",
                    "description",
                    "classification",
                    "transform",
                ],
                "properties": {
                    "op_id": {"type": "string"},
                    "target_type": {
                        "type": "string",
                        "enum": ["file", "column", "link", "data_doc"],
                    },
                    "target": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                    },
                    "op_type": {"type": "string"},
                    "before_value": {},
                    "after_value": {},
                    "description": {"type": "string"},
                    "classification": {
                        "type": "string",
                        "enum": ["minor", "structural"],
                    },
                    "transform": {
                        "anyOf": [
                            {"type": "null"},
                            {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["kind", "factor"],
                                "properties": {
                                    "kind": {"type": "string", "enum": ["scale"]},
                                    "factor": {"type": "number"},
                                },
                            },
                        ]
                    },
                },
            },
        },
    },
}

CORRECTION_INTERPRETER_PROMPT = """You translate a user's natural-language Cerno schema correction into a structured patch.
Classify operations as minor only when they change metadata without re-casting or re-ingesting data: file friendly_name, file/column descriptions, glossary text, usage notes, or starter questions.
Classify all dtype changes, header row changes, unit/scale reinterpretations, column additions/removals/renames, and relationship changes as structural.
For scale/unit corrections such as values in thousands, lakhs, crores, millions, or billions, return op_type "scale_column" with transform {"kind":"scale","factor":...}.
Do not apply changes. Return only operations that target existing files/columns/links from the supplied context."""


class SchemaCorrectionError(RuntimeError):
    pass


async def interpret_schema_correction(
    *,
    instruction: str,
    schema_context: dict[str, Any],
    llm_client: LLMClient,
    config: DiscoveryProcessingConfig,
) -> SchemaCorrectionPatch:
    payload = await _respond_json_schema_with_retry(
        llm_client=llm_client,
        input_payload=[
            {
                "role": "user",
                "content": "Instruction:\n"
                f"{instruction}\n\nCurrent schema context:\n"
                f"{json.dumps(schema_context, default=str, indent=2)}",
            }
        ],
        instructions=CORRECTION_INTERPRETER_PROMPT,
        model=config.analysis_model,
        reasoning_effort=config.analysis_reasoning_effort,
        reasoning_summary=config.reasoning_summary,
        response_format={
            "type": "json_schema",
            "name": "schema_correction_patch",
            "schema": CORRECTION_RESPONSE_SCHEMA,
            "strict": True,
        },
    )
    patch = SchemaCorrectionPatch(instruction=instruction, operations=payload.get("operations", []))
    return normalize_patch(patch, instruction=instruction)


def normalize_patch(
    patch: SchemaCorrectionPatch, *, instruction: str | None = None
) -> SchemaCorrectionPatch:
    operations: list[CorrectionOperation] = []
    for index, operation in enumerate(patch.operations):
        op_type = operation.op_type.strip()
        classification: CorrectionClassification = (
            "minor" if op_type in MINOR_OPS else "structural"
        )
        transform = operation.transform
        if op_type == "scale_column":
            factor = _scale_factor_from_operation(operation, instruction or patch.instruction)
            transform = {"kind": "scale", "factor": factor}
            classification = "structural"
        elif op_type in STRUCTURAL_OPS:
            classification = "structural"
        operations.append(
            operation.model_copy(
                update={
                    "op_id": operation.op_id or f"op_{index + 1}",
                    "op_type": op_type,
                    "classification": classification,
                    "transform": transform,
                }
            )
        )
    return SchemaCorrectionPatch(instruction=instruction or patch.instruction, operations=operations)


def apply_operations_to_payload(
    payload: ApprovalPayload,
    operations: list[CorrectionOperation],
) -> ApprovalPayload:
    files = [
        FileSpec(
            file_id=file.file_id,
            header_row=file.header_row,
            friendly_name=file.friendly_name,
            description=file.description,
            columns=[
                ColumnSpec(
                    column_id=column.column_id,
                    name=column.name,
                    dtype=column.dtype,
                    description=column.description,
                    scale_factor=column.scale_factor,
                )
                for column in file.columns
            ],
        )
        for file in payload.files
    ]
    links = [
        LinkSpec(
            file_a_id=link.file_a_id,
            col_a=link.col_a,
            file_b_id=link.file_b_id,
            col_b=link.col_b,
            direction=link.direction,
            summary=link.summary,
        )
        for link in payload.links
    ]
    result = ApprovalPayload(files=files, links=links, overview=payload.overview)

    for operation in operations:
        if operation.classification != "structural":
            continue
        _apply_structural_operation(result, operation)
    return result


def selected_structural_operations(
    patch: SchemaCorrectionPatch, approved_op_ids: set[str]
) -> list[CorrectionOperation]:
    return [
        operation
        for operation in patch.operations
        if operation.classification == "structural" and operation.op_id in approved_op_ids
    ]


def minor_operations(patch: SchemaCorrectionPatch) -> list[CorrectionOperation]:
    return [operation for operation in patch.operations if operation.classification == "minor"]


def _apply_structural_operation(
    payload: ApprovalPayload, operation: CorrectionOperation
) -> None:
    if operation.target_type == "column":
        file_id = operation.target.get("file_id")
        column_id = operation.target.get("column_id")
        column = _find_column(payload, file_id, column_id)
        if column is None:
            return
        if operation.op_type == "set_dtype":
            column.dtype = str(operation.after_value or column.dtype)
        elif operation.op_type == "rename_column":
            column.name = str(operation.after_value or column.name)
        elif operation.op_type == "scale_column":
            factor = float((operation.transform or {}).get("factor") or operation.after_value or 1.0)
            column.scale_factor *= factor
            if column.dtype not in {"int", "float"}:
                column.dtype = "float"
    elif operation.target_type == "file":
        file = _find_file(payload, operation.target.get("file_id"))
        if file is None:
            return
        if operation.op_type == "set_header_row":
            file.header_row = int(operation.after_value)
    elif operation.target_type == "link":
        _apply_link_operation(payload, operation)


def _apply_link_operation(payload: ApprovalPayload, operation: CorrectionOperation) -> None:
    target = operation.target
    if operation.op_type == "remove_link":
        payload.links = [
            link
            for link in payload.links
            if not (
                link.file_a_id == target.get("file_a_id")
                and link.col_a == target.get("col_a")
                and link.file_b_id == target.get("file_b_id")
                and link.col_b == target.get("col_b")
            )
        ]
        return
    if operation.op_type not in {"add_link", "edit_link"}:
        return
    after = operation.after_value if isinstance(operation.after_value, dict) else {}
    payload.links.append(
        LinkSpec(
            file_a_id=str(after.get("file_a_id") or target.get("file_a_id") or ""),
            col_a=str(after.get("col_a") or target.get("col_a") or ""),
            file_b_id=str(after.get("file_b_id") or target.get("file_b_id") or ""),
            col_b=str(after.get("col_b") or target.get("col_b") or ""),
            direction=str(after.get("direction") or "many_to_many"),
            summary=str(after.get("summary") or operation.description),
        )
    )


def _find_file(payload: ApprovalPayload, file_id: str | None) -> FileSpec | None:
    return next((file for file in payload.files if file.file_id == file_id), None)


def _find_column(
    payload: ApprovalPayload, file_id: str | None, column_id: str | None
) -> ColumnSpec | None:
    file = _find_file(payload, file_id)
    if file is None:
        return None
    return next(
        (
            column
            for column in file.columns
            if column.column_id == column_id or column.name == column_id
        ),
        None,
    )


def _scale_factor_from_operation(operation: CorrectionOperation, instruction: str) -> float:
    if operation.transform and operation.transform.get("factor") is not None:
        return float(operation.transform["factor"])
    if isinstance(operation.after_value, int | float):
        return float(operation.after_value)
    text = f"{instruction} {operation.description} {operation.after_value}".lower()
    for token, factor in SCALE_FACTORS.items():
        if re.search(rf"\b{re.escape(token)}\b", text):
            return factor
    return 1.0


async def _respond_json_schema_with_retry(
    *,
    llm_client: LLMClient,
    input_payload: list[dict[str, Any]],
    instructions: str,
    model: str,
    reasoning_effort: str,
    reasoning_summary: str,
    response_format: dict[str, Any],
    attempts: int = 2,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(attempts):
        response = await llm_client.respond(
            input=input_payload,
            instructions=instructions,
            model=model,
            reasoning_effort=reasoning_effort,
            reasoning_summary=reasoning_summary,
            response_format=response_format,
        )
        try:
            _raise_for_refusal(response.raw, response.status)
            return _parse_json(response.content)
        except SchemaCorrectionError as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                break
            await asyncio.sleep(0.5 * (2**attempt))
    assert last_error is not None
    raise last_error


def _raise_for_refusal(raw: dict[str, Any], status: str) -> None:
    if status != "completed":
        raise SchemaCorrectionError(f"LLM response did not complete: status={status}")
    for item in raw.get("output", []) or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content", []) or []:
            if isinstance(part, dict) and (part.get("type") == "refusal" or part.get("refusal")):
                raise SchemaCorrectionError("LLM refused the schema correction request")


def _parse_json(content: str) -> dict[str, Any]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise SchemaCorrectionError(f"LLM returned invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SchemaCorrectionError("LLM returned a non-object schema correction")
    return parsed
