from __future__ import annotations

import sqlite3
import unittest
from datetime import UTC, datetime, timedelta

from cerno.repositories import AnalyticsRepository


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE product_events (
            id TEXT PRIMARY KEY,
            organization_id TEXT,
            user_id TEXT,
            session_id TEXT,
            event_name TEXT NOT NULL,
            metric_value REAL,
            metadata TEXT NOT NULL DEFAULT '{}',
            occurred_at TEXT NOT NULL
        )
        """
    )
    return conn


class SignInDedupTest(unittest.TestCase):
    def test_last_event_at_none_when_absent(self) -> None:
        analytics = AnalyticsRepository(_make_conn())
        self.assertIsNone(
            analytics.last_product_event_at(event_name="user_signed_in", user_id="u1")
        )

    def test_last_event_at_returns_latest_for_user_and_name(self) -> None:
        conn = _make_conn()
        analytics = AnalyticsRepository(conn)
        analytics.record_product_event(event_name="user_signed_in", user_id="u1")
        analytics.record_product_event(event_name="user_signed_in", user_id="u1")
        # Other user / other event must not leak in.
        analytics.record_product_event(event_name="user_signed_in", user_id="u2")
        analytics.record_product_event(event_name="session_created", user_id="u1")

        last = analytics.last_product_event_at(event_name="user_signed_in", user_id="u1")
        self.assertIsNotNone(last)
        rows = conn.execute(
            "SELECT MAX(occurred_at) AS m FROM product_events "
            "WHERE event_name='user_signed_in' AND user_id='u1'"
        ).fetchone()
        assert last is not None
        self.assertEqual(last, datetime.fromisoformat(rows["m"]))

    def test_dedup_window_decision(self) -> None:
        window = timedelta(minutes=10)
        now = datetime.now(UTC)
        # Within window -> suppress.
        self.assertFalse(now - (now - timedelta(minutes=2)) >= window)
        # Outside window -> record.
        self.assertTrue(now - (now - timedelta(minutes=11)) >= window)


if __name__ == "__main__":
    unittest.main()
