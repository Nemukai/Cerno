from __future__ import annotations

import polars as pl

from cerno.services.reingest import (
    ApprovalPayload,
    CastedColumn,
    ColumnSpec,
    FileSpec,
    _scale_casted_column,
)
from cerno.services.schema_corrections import (
    CorrectionOperation,
    SchemaCorrectionPatch,
    apply_operations_to_payload,
    minor_operations,
    normalize_patch,
    selected_structural_operations,
)
from cerno.services.tools import ToolContext, build_tool_registry


def test_rename_describe_instruction_classifies_as_minor() -> None:
    patch = normalize_patch(
        SchemaCorrectionPatch(
            instruction="rename this file to Orders and improve the description",
            operations=[
                CorrectionOperation(
                    op_id="op1",
                    target_type="file",
                    target={"file_id": "file-1"},
                    op_type="set_friendly_name",
                    before_value="Sheet1",
                    after_value="Orders",
                    description="Rename the file display name.",
                    classification="structural",
                ),
                CorrectionOperation(
                    op_id="op2",
                    target_type="column",
                    target={"file_id": "file-1", "column_id": "amount"},
                    op_type="set_column_description",
                    before_value="",
                    after_value="Order amount.",
                    description="Describe the amount column.",
                    classification="structural",
                ),
            ],
        )
    )

    assert [operation.classification for operation in minor_operations(patch)] == ["minor", "minor"]


def test_date_and_lakhs_corrections_are_structural_until_confirmed() -> None:
    patch = normalize_patch(
        SchemaCorrectionPatch(
            instruction="file A's date is DD/MM and amounts are in lakhs",
            operations=[
                CorrectionOperation(
                    op_id="date",
                    target_type="column",
                    target={"file_id": "file-1", "column_id": "for_date"},
                    op_type="set_dtype",
                    before_value="string",
                    after_value="date",
                    description="Recast FOR DATE as a date column.",
                    classification="minor",
                ),
                CorrectionOperation(
                    op_id="scale",
                    target_type="column",
                    target={"file_id": "file-1", "column_id": "amount"},
                    op_type="scale_column",
                    before_value="lakhs",
                    after_value="rupees",
                    description="Amounts are stored in lakhs.",
                    classification="minor",
                    transform=None,
                ),
            ],
        )
    )

    assert [operation.op_id for operation in minor_operations(patch)] == []
    assert selected_structural_operations(patch, set()) == []
    confirmed = selected_structural_operations(patch, {"scale"})
    assert confirmed[0].classification == "structural"
    assert confirmed[0].transform == {"kind": "scale", "factor": 100000.0}


def test_structural_scale_operation_updates_payload_and_recasts_values() -> None:
    payload = ApprovalPayload(
        files=[
            FileSpec(
                file_id="file-1",
                header_row=0,
                friendly_name="Orders",
                description="Orders.",
                columns=[
                    ColumnSpec(
                        column_id="amount",
                        name="Amount",
                        dtype="float",
                        description="Amount.",
                    )
                ],
            )
        ],
        links=[],
        overview="Orders.",
    )
    operation = CorrectionOperation(
        op_id="scale",
        target_type="column",
        target={"file_id": "file-1", "column_id": "amount"},
        op_type="scale_column",
        before_value="lakhs",
        after_value="rupees",
        description="Amounts are in lakhs.",
        classification="structural",
        transform={"kind": "scale", "factor": 100000.0},
    )

    updated = apply_operations_to_payload(payload, [operation])
    assert updated.files[0].columns[0].scale_factor == 100000.0

    scaled = _scale_casted_column(
        CastedColumn(pl.Series("Amount", [1.5, 2.0], dtype=pl.Float64), "float", 1.0),
        updated.files[0].columns[0].scale_factor,
    )
    assert scaled.series.to_list() == [150000.0, 200000.0]


def test_chat_tools_do_not_expose_schema_correction_endpoints() -> None:
    registry = build_tool_registry(ToolContext(session_id="session-1", tables={}))

    names = set(registry.names())
    assert "interpret_schema_correction" not in names
    assert "apply_schema_correction" not in names
    assert "run_python" in names
    assert "search_regulations" in names
