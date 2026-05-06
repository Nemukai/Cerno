from __future__ import annotations

import unittest

from cerno.services.discovery import (
    DiscoveredColumn,
    DiscoveredFile,
    _schema_from_discovered_file,
)


class DiscoverySchemaTests(unittest.TestCase):
    def test_discovery_columns_are_persistable_as_schema_columns(self) -> None:
        discovered = DiscoveredFile(
            file_id="file-1",
            friendly_name="Toll Transactions",
            description="Toll booth transaction rows.",
            header_row=2,
            columns=[
                DiscoveredColumn(
                    column_id="for_date",
                    name="FOR DATE",
                    description="Transaction date.",
                    dtype="date",
                ),
                DiscoveredColumn(
                    column_id="rate",
                    name="Rate",
                    description="Toll amount charged.",
                    dtype="float",
                ),
            ],
        )

        schema = _schema_from_discovered_file(discovered, schema_version=3)

        self.assertEqual(schema.file_id, "file-1")
        self.assertEqual(schema.schema_version, 3)
        self.assertEqual([column.name for column in schema.columns], ["FOR DATE", "Rate"])
        self.assertEqual([column.inferred_kind for column in schema.columns], ["date", "float"])


if __name__ == "__main__":
    unittest.main()
