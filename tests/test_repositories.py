from __future__ import annotations

import pytest

from cerno.db import connect_memory
from cerno.models import Anomaly, FileSchema, NotebookCell, SchemaColumn
from cerno.repositories import (
    AnomalyRepository,
    AuditRepository,
    ChatRepository,
    DashboardRepository,
    FileRepository,
    LinkRepository,
    NotebookRepository,
    SchemaRepository,
    SessionRepository,
    new_id,
)


@pytest.fixture
def conn():
    c = connect_memory()
    yield c
    c.close()


def test_session_create_get_list(conn) -> None:
    repo = SessionRepository(conn)
    s = repo.create("test session")
    fetched = repo.get(s.id)
    assert fetched is not None
    assert fetched.name == "test session"
    assert fetched.status == "new"

    repo.set_status(s.id, "ready")
    assert repo.get(s.id).status == "ready"  # type: ignore[union-attr]

    sessions = repo.list()
    assert len(sessions) == 1


def test_file_create_schema_version_bump(conn) -> None:
    sessions = SessionRepository(conn)
    files = FileRepository(conn)
    s = sessions.create("s")
    f = files.create(session_id=s.id, filename="a.csv", parquet_path="/tmp/a", row_count=100)
    assert f.schema_version == 1
    new_version = files.bump_schema_version(f.id)
    assert new_version == 2
    refetched = files.get(f.id)
    assert refetched is not None
    assert refetched.schema_version == 2


def test_schema_replace_round_trip(conn) -> None:
    sessions = SessionRepository(conn)
    files = FileRepository(conn)
    schemas = SchemaRepository(conn)
    s = sessions.create("s")
    f = files.create(session_id=s.id, filename="a.csv", parquet_path="/tmp/a", row_count=100)

    schema = FileSchema(
        file_id=f.id,
        schema_version=1,
        columns=[
            SchemaColumn(
                file_id=f.id,
                schema_version=1,
                name="a",
                dtype="Int64",
                inferred_kind="int",
                confidence=0.95,
                position=0,
            ),
            SchemaColumn(
                file_id=f.id,
                schema_version=1,
                name="b",
                dtype="String",
                inferred_kind="category",
                confidence=0.8,
                position=1,
            ),
        ],
    )
    schemas.replace(schema)
    fetched = schemas.get(f.id, 1)
    assert fetched is not None
    assert [c.name for c in fetched.columns] == ["a", "b"]


def test_link_create_and_review(conn) -> None:
    sessions = SessionRepository(conn)
    files = FileRepository(conn)
    links = LinkRepository(conn)
    s = sessions.create("s")
    f1 = files.create(session_id=s.id, filename="a.csv", parquet_path="/tmp/a", row_count=100)
    f2 = files.create(session_id=s.id, filename="b.csv", parquet_path="/tmp/b", row_count=50)

    link = links.create(
        session_id=s.id,
        file_a=f1.id,
        col_a="customer_id",
        file_b=f2.id,
        col_b="cust_ref",
        overlap=0.92,
        direction="many_to_one",
        score=0.88,
    )
    review = links.add_review(link_id=link.id, action="confirm", notes="looks right")
    assert review.id is not None

    listed = links.list_for_session(s.id)
    assert len(listed) == 1
    assert listed[0].overlap == 0.92


def test_anomaly_top_excludes_reviewed(conn) -> None:
    sessions = SessionRepository(conn)
    files = FileRepository(conn)
    anomalies = AnomalyRepository(conn)
    s = sessions.create("s")
    f = files.create(session_id=s.id, filename="a.csv", parquet_path="/tmp/a", row_count=100)

    from datetime import UTC, datetime

    for i in range(3):
        anomalies.create(
            Anomaly(
                id=new_id(),
                session_id=s.id,
                file_id=f.id,
                row_id=i,
                detector="robust_z",
                reason_plain=f"row {i} is weird",
                reason_technical=f"|z|=4.{i}",
                score_normalized=90 - i,
                score_raw=4.0 + i,
                source_code="SELECT * FROM foo",
                created_at=datetime.now(UTC),
            )
        )
    top = anomalies.top_for_session(s.id)
    assert len(top) == 3
    assert top[0].score_normalized > top[-1].score_normalized

    anomalies.set_review(
        anomaly_id=top[0].id, status="dismissed", reviewed_by="tester", notes=None
    )
    top_after = anomalies.top_for_session(s.id)
    assert len(top_after) == 2


def test_dashboard_pages_and_notebook_cells(conn) -> None:
    sessions = SessionRepository(conn)
    dashboards = DashboardRepository(conn)
    notebook = NotebookRepository(conn)
    s = sessions.create("s")

    d = dashboards.create(s.id)
    page = dashboards.add_page(dashboard_id=d.id, title="Overview", kind="overview", position=0)

    from datetime import UTC, datetime

    cell = NotebookCell(
        id=new_id(),
        page_id=page.id,
        order_index=0,
        kind="python",
        code="df = tables['customers']; df.head()",
        output={"stdout": "", "result": "head"},
        bound_file_ids=["f1"],
        bound_schema_versions={"f1": 1},
        threshold_snapshot={"mad_z": 3.5},
        created_at=datetime.now(UTC),
    )
    notebook.add_cell(cell)
    cells = notebook.list_for_page(page.id)
    assert len(cells) == 1
    assert cells[0].threshold_snapshot == {"mad_z": 3.5}
    assert cells[0].output == {"stdout": "", "result": "head"}

    notebook.set_output(cell_id=cell.id, output={"stdout": "done"}, status="ok")
    updated = notebook.list_for_page(page.id)[0]
    assert updated.last_run_status == "ok"
    assert updated.output == {"stdout": "done"}


def test_chat_turn_lifecycle(conn) -> None:
    sessions = SessionRepository(conn)
    chat = ChatRepository(conn)
    s = sessions.create("s")

    turn = chat.create_turn(session_id=s.id, user_message="how many customers?")
    chat.append_message(turn_id=turn.id, role="user", content="how many customers?")
    chat.set_turn_state(turn.id, "tool_running")
    chat.append_message(
        turn_id=turn.id,
        role="tool",
        content="",
        tool_name="run_sql",
        tool_args={"query": "SELECT COUNT(*) FROM customers"},
        tool_result={"rows": [[42]]},
    )
    chat.complete_turn(turn_id=turn.id, assistant_message="42 customers")

    turns = chat.list_turns(s.id)
    assert len(turns) == 1
    assert turns[0].state == "complete"
    assert turns[0].assistant_message == "42 customers"

    messages = chat.list_messages(turn.id)
    assert len(messages) == 2
    assert messages[1].tool_name == "run_sql"


def test_audit_log(conn) -> None:
    audit = AuditRepository(conn)
    event = audit.log(kind="session.created", session_id="s1", details={"name": "x"})
    assert event.id is not None
    assert event.details == {"name": "x"}
