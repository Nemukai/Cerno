from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cerno.config import Settings
from cerno.providers import (
    available_embedding_providers,
    available_ocr_engines,
    resolve_embedding_provider,
    resolve_ocr_engine,
)


class ProviderRegistryTests(unittest.TestCase):
    def test_resolves_default_openai_providers(self) -> None:
        settings = Settings(_env_file=None)

        ocr = resolve_ocr_engine(settings)
        embeddings = resolve_embedding_provider(settings)

        self.assertEqual(ocr.name, "openai")
        self.assertEqual(ocr.model, "gpt-5.4-mini")
        self.assertEqual(embeddings.name, "openai")
        self.assertEqual(embeddings.model, "text-embedding-3-small")
        self.assertEqual(available_ocr_engines(), ("openai",))
        self.assertEqual(available_embedding_providers(), ("openai",))

    def test_resolved_defaults_use_runtime_config_models(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.toml").write_text(
                "\n".join(
                    [
                        "[processing.ocr]",
                        'model = "gpt-5.5"',
                        "",
                        "[processing.embedding]",
                        'model = "text-embedding-3-large"',
                    ]
                ),
                encoding="utf-8",
            )
            settings = Settings(_env_file=None, data_root=root)

            ocr = resolve_ocr_engine(settings)
            embeddings = resolve_embedding_provider(settings)

        self.assertEqual(ocr.model, "gpt-5.5")
        self.assertEqual(embeddings.model, "text-embedding-3-large")


if __name__ == "__main__":
    unittest.main()
