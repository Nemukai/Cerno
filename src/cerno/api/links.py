from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from cerno.api.deps import ConnDep, LLMDep, SettingsDep
from cerno.models import Link, LinkAction, LinkReview
from cerno.repositories import FileRepository, LinkRepository, SchemaRepository
from cerno.services.links import LinkDiscoveryError, discover_links
from cerno.services.reviews import ReviewError, review_link, skip_review_for_session

router = APIRouter(tags=["links"])


class ReviewLinkBody(BaseModel):
    action: LinkAction
    notes: str | None = None


class SkipReviewResponse(BaseModel):
    auto_confirmed_count: int


@router.post("/sessions/{session_id}/discover-links", response_model=list[Link])
async def post_discover_links(
    session_id: str,
    conn: ConnDep,
    settings: SettingsDep,
    llm_client: LLMDep,
) -> list[Link]:
    try:
        return await discover_links(
            session_id=session_id,
            settings=settings,
            files_repo=FileRepository(conn),
            schemas_repo=SchemaRepository(conn),
            links_repo=LinkRepository(conn),
            llm_client=llm_client,
        )
    except LinkDiscoveryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/sessions/{session_id}/links", response_model=list[Link])
def get_session_links(session_id: str, conn: ConnDep) -> list[Link]:
    return LinkRepository(conn).list_for_session(session_id)


@router.post("/links/{link_id}/review", response_model=LinkReview)
def post_review(link_id: str, body: ReviewLinkBody, conn: ConnDep) -> LinkReview:
    try:
        return review_link(
            links_repo=LinkRepository(conn),
            link_id=link_id,
            action=body.action,
            notes=body.notes,
        )
    except ReviewError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/sessions/{session_id}/skip-review", response_model=SkipReviewResponse
)
def post_skip_review(session_id: str, conn: ConnDep) -> SkipReviewResponse:
    result = skip_review_for_session(
        links_repo=LinkRepository(conn), session_id=session_id
    )
    return SkipReviewResponse(auto_confirmed_count=len(result.auto_confirmed))
