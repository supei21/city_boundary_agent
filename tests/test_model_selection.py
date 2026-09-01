from __future__ import annotations

import unittest

from boundary_agent.llm.client import (
    get_default_model_id,
    get_model_settings,
    list_model_options,
)


class ModelSelectionTest(unittest.TestCase):
    def test_local_rules_are_always_available(self):
        option = next(item for item in list_model_options() if item["id"] == "local-rules")
        self.assertTrue(option["ready"])
        self.assertEqual(option["kind"], "local")
        self.assertFalse(get_model_settings("local-rules")["enabled"])

    def test_default_model_id_is_selectable(self):
        options = list_model_options()
        default_id = get_default_model_id()
        self.assertTrue(any(item["id"] == default_id and item["ready"] for item in options))

    def test_unknown_model_is_not_resolved(self):
        with self.assertRaises(KeyError):
            get_model_settings("not-configured")


if __name__ == "__main__":
    unittest.main()
