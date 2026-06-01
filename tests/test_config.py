from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cerno.config import (
    CernoConfigError,
    Settings,
    load_client_modules_config,
    load_processing_config,
)


class ProcessingConfigTests(unittest.TestCase):
    def test_missing_config_returns_processing_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_processing_config(Path(tmp) / "config.toml")

        self.assertEqual(config.discovery.model, "gpt-5.5")
        self.assertEqual(Settings(_env_file=None).chat_model, "gpt-5.4-mini")
        self.assertEqual(config.discovery.reasoning_effort, "medium")
        self.assertEqual(config.discovery.reasoning_summary, "auto")
        self.assertEqual(config.ocr.engine, "openai")
        self.assertEqual(config.embedding.provider, "openai")
        self.assertEqual(config.embedding.model, "text-embedding-3-small")
        self.assertEqual(config.embedding.dimension, 1536)
        self.assertEqual(config.embedding.batch_size, 64)
        self.assertEqual(config.embedding.chunk_max_chars, 1800)
        self.assertEqual(config.embedding.chunk_min_chars, 120)
        settings = Settings(_env_file=None)
        self.assertEqual(settings.link_containment_threshold, 0.85)
        self.assertEqual(settings.link_parent_uniqueness_threshold, 0.95)
        self.assertEqual(settings.link_candidate_score_threshold, 0.72)
        self.assertEqual(settings.discovery_reask_max_fields, 20)

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
                        "",
                        "[processing.ocr]",
                        'engine = "openai"',
                        'model = "gpt-5.4-mini"',
                        "",
                        "[processing.embedding]",
                        'provider = "openai"',
                        'model = "text-embedding-3-large"',
                        "dimension = 3072",
                        "batch_size = 32",
                        "chunk_max_chars = 2400",
                        "chunk_min_chars = 180",
                    ]
                ),
                encoding="utf-8",
            )
            config = load_processing_config(path)

        self.assertEqual(config.discovery.model, "gpt-5.5")
        self.assertEqual(config.discovery.reasoning_effort, "xhigh")
        self.assertEqual(config.discovery.reasoning_summary, "detailed")
        self.assertEqual(config.ocr.engine, "openai")
        self.assertEqual(config.ocr.model, "gpt-5.4-mini")
        self.assertEqual(config.embedding.provider, "openai")
        self.assertEqual(config.embedding.model, "text-embedding-3-large")
        self.assertEqual(config.embedding.dimension, 3072)
        self.assertEqual(config.embedding.batch_size, 32)
        self.assertEqual(config.embedding.chunk_max_chars, 2400)
        self.assertEqual(config.embedding.chunk_min_chars, 180)

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

    def test_client_modules_config_reads_enabled_specs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(
                "\n".join(
                    [
                        "[modules]",
                        'enabled = ["client_package.cerno_module:register"]',
                        'entry_point_group = "custom.cerno_modules"',
                    ]
                ),
                encoding="utf-8",
            )
            config = load_client_modules_config(path)

        self.assertEqual(config.enabled, ("client_package.cerno_module:register",))
        self.assertEqual(config.entry_point_group, "custom.cerno_modules")


if __name__ == "__main__":
    unittest.main()
