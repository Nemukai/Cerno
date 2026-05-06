from __future__ import annotations

import unittest

from cerno.llm import conversation_context_options


class LLMContextOptionsTests(unittest.TestCase):
    def test_prompt_cache_key_stays_within_openai_limit(self) -> None:
        options = conversation_context_options("session_" + ("x" * 80), "turn_" + ("y" * 80))

        prompt_cache_key = options["prompt_cache_key"]
        self.assertIsInstance(prompt_cache_key, str)
        self.assertLessEqual(len(prompt_cache_key), 64)
        self.assertTrue(prompt_cache_key.startswith("cerno:"))


if __name__ == "__main__":
    unittest.main()
