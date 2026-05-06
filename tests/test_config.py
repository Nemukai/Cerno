from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cerno.config import CernoConfigError, Settings, load_processing_config


class ProcessingConfigTests(unittest.TestCase):
    def test_missing_config_returns_processing_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_processing_config(Path(tmp) / "config.toml")

        self.assertEqual(config.discovery.model, "gpt-5.5")
        self.assertEqual(config.discovery.reasoning_effort, "medium")
        self.assertEqual(config.discovery.reasoning_summary, "auto")

    def test_valid_config_overrides_discovery_processing_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(
                "\n".join(
                    [
                        "[processing.discovery]",
                        'model = "gpt-5.5"',
                        'reasoning_effort = "xhigh"',
                        'reasoning_summary = "detailed"',
                    ]
                ),
                encoding="utf-8",
            )
            config = load_processing_config(path)

        self.assertEqual(config.discovery.model, "gpt-5.5")
        self.assertEqual(config.discovery.reasoning_effort, "xhigh")
        self.assertEqual(config.discovery.reasoning_summary, "detailed")

    def test_invalid_reasoning_effort_raises_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(
                "\n".join(
                    [
                        "[processing.discovery]",
                        'reasoning_effort = "maximum"',
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                CernoConfigError,
                "processing.discovery.reasoning_effort",
            ):
                load_processing_config(path)

    def test_settings_processing_reads_data_root_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.toml").write_text(
                "\n".join(
                    [
                        "[processing.discovery]",
                        'model = "gpt-5.4-mini"',
                    ]
                ),
                encoding="utf-8",
            )
            settings = Settings(data_root=root)

            self.assertEqual(settings.processing.discovery.model, "gpt-5.4-mini")


if __name__ == "__main__":
    unittest.main()
