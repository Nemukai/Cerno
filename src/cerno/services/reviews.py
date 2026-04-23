from __future__ import annotations

from dataclasses import dataclass

from cerno.models import LinkAction, LinkReview
from cerno.repositories import LinkRepository

SKIP_REVIEW_NOTE = "auto: user skipped review"


class ReviewError(RuntimeError):
    pass


@dataclass
class SkipReviewResult:
    auto_confirmed: list[LinkReview]


def review_link(
    *,
    links_repo: LinkRepository,
    link_id: str,
    action: LinkAction,
    notes: str | None = None,
) -> LinkReview:
    link = links_repo.get(link_id)
    if link is None:
        raise ReviewError(f"link not found: {link_id}")
    return links_repo.add_review(link_id=link_id, action=action, notes=notes)


def skip_review_for_session(
    *,
    links_repo: LinkRepository,
    session_id: str,
) -> SkipReviewResult:
    links = links_repo.list_for_session(session_id)
    reviews: list[LinkReview] = []
    for link in links:
        if links_repo.latest_review(link.id) is not None:
            continue
        reviews.append(
            links_repo.add_review(
                link_id=link.id, action="confirm", notes=SKIP_REVIEW_NOTE
            )
        )
    return SkipReviewResult(auto_confirmed=reviews)
