from __future__ import annotations

import pytest

from cerno.db import connect_memory
from cerno.repositories import FileRepository, LinkRepository, SessionRepository
from cerno.services.reviews import (
    SKIP_REVIEW_NOTE,
    ReviewError,
    review_link,
    skip_review_for_session,
)


@pytest.fixture
def conn():
    c = connect_memory()
    yield c
    c.close()


def _seed_links(conn, n: int = 2):
    sessions = SessionRepository(conn)
    files = FileRepository(conn)
    links = LinkRepository(conn)
    s = sessions.create("s")
    f1 = files.create(session_id=s.id, filename="a.csv", parquet_path="/tmp/a", row_count=10)
    f2 = files.create(session_id=s.id, filename="b.csv", parquet_path="/tmp/b", row_count=10)
    created = [
        links.create(
            session_id=s.id,
            file_a=f1.id,
            col_a=f"ka_{i}",
            file_b=f2.id,
            col_b=f"kb_{i}",
            overlap=0.9,
            direction="many_to_one",
            score=0.9,
        )
        for i in range(n)
    ]
    return s.id, created


def test_review_link_records_action(conn) -> None:
    _, links = _seed_links(conn, n=1)
    repo = LinkRepository(conn)
    review = review_link(links_repo=repo, link_id=links[0].id, action="confirm", notes="looks right")
    assert review.action == "confirm"
    assert review.notes == "looks right"


def test_review_link_missing_raises(conn) -> None:
    repo = LinkRepository(conn)
    with pytest.raises(ReviewError):
        review_link(links_repo=repo, link_id="nonexistent", action="confirm")


def test_skip_review_auto_confirms_all(conn) -> None:
    session_id, _ = _seed_links(conn, n=3)
    repo = LinkRepository(conn)
    result = skip_review_for_session(links_repo=repo, session_id=session_id)
    assert len(result.auto_confirmed) == 3
    for review in result.auto_confirmed:
        assert review.action == "confirm"
        assert review.notes == SKIP_REVIEW_NOTE


def test_skip_review_is_idempotent(conn) -> None:
    session_id, _ = _seed_links(conn, n=2)
    repo = LinkRepository(conn)
    first = skip_review_for_session(links_repo=repo, session_id=session_id)
    second = skip_review_for_session(links_repo=repo, session_id=session_id)
    assert len(first.auto_confirmed) == 2
    assert len(second.auto_confirmed) == 0


def test_skip_review_does_not_overwrite_manual_confirm(conn) -> None:
    session_id, links = _seed_links(conn, n=2)
    repo = LinkRepository(conn)
    review_link(links_repo=repo, link_id=links[0].id, action="reject", notes="wrong")
    result = skip_review_for_session(links_repo=repo, session_id=session_id)
    assert len(result.auto_confirmed) == 1
    latest_manual = repo.latest_review(links[0].id)
    assert latest_manual is not None
    assert latest_manual.action == "reject"
