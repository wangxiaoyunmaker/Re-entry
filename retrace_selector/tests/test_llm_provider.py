from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from retrace_selector.llm_provider import ProviderConfig
from retrace_selector.models import ValidationError


class ProviderConfigTests(unittest.TestCase):
    def test_key_is_required_from_environment(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValidationError, "RETRACE_LLM_API_KEY"):
                ProviderConfig.from_env()

    def test_provider_defaults_are_configurable_without_key_logging(self):
        with patch.dict(
            os.environ,
            {
                "RETRACE_LLM_API_KEY": "test-only-key",
                "RETRACE_LLM_MODEL": "test-model",
                "RETRACE_LLM_BASE_URL": "https://example.test/",
            },
            clear=True,
        ):
            config = ProviderConfig.from_env()
        self.assertEqual(config.model, "test-model")
        self.assertEqual(config.base_url, "https://example.test")
        self.assertEqual(config.api_key, "test-only-key")
        self.assertEqual(config.thinking_type, "disabled")


if __name__ == "__main__":
    unittest.main()
