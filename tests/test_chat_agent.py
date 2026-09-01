from __future__ import annotations

import unittest
from boundary_agent.io import parse_json
from boundary_agent.llm.chat_service import handle_chat, parse_user_intent
from boundary_agent.paths import SAMPLE_DATA_FILE


class ChatAgentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.blocks = parse_json(SAMPLE_DATA_FILE.read_bytes())
        cls.disabled_model = {"enabled": False}

    def test_analyze_one_block(self):
        result = handle_chat(
            "请分析 WH2025_DEMO001",
            self.blocks,
            settings=self.disabled_model,
        )
        self.assertEqual(result.selected_block_ids, ["WH2025_DEMO001"])
        self.assertEqual(len(result.decisions), 1)

    def test_show_all_block_data_without_analysis(self):
        result = handle_chat(
            "查看全部街区数据",
            self.blocks,
            settings=self.disabled_model,
        )
        self.assertEqual(result.intent.action, "show_data")
        self.assertEqual(len(result.block_records), 3)
        self.assertFalse(result.decisions)

    def test_show_one_block_attributes(self):
        result = handle_chat(
            "查看 WH2025_DEMO002 的原始属性",
            self.blocks,
            settings=self.disabled_model,
        )
        self.assertEqual(result.selected_block_ids, ["WH2025_DEMO002"])
        self.assertEqual(result.block_records[0]["block_name"], "生态约束街区")

    def test_find_inclusion_candidates(self):
        result = handle_chat(
            "哪些街区适合调入开发边界？",
            self.blocks,
            settings=self.disabled_model,
        )
        self.assertTrue(result.decisions)
        self.assertTrue(
            all(item.decision in {"一级优先调入", "二级优先调入"} for item in result.decisions)
        )

    def test_compare_requires_two_blocks(self):
        intent, _ = parse_user_intent(
            "比较 WH2025_DEMO001",
            self.blocks,
            self.disabled_model,
        )
        self.assertTrue(intent.clarification_needed)

    def test_compare_two_blocks(self):
        result = handle_chat(
            "比较 WH2025_DEMO001 和 WH2025_DEMO003",
            self.blocks,
            settings=self.disabled_model,
        )
        self.assertEqual(len(result.selected_block_ids), 2)
        self.assertEqual(len(result.decisions), 2)

    def test_unknown_block(self):
        result = handle_chat(
            "分析 WH2025_NOT_FOUND",
            self.blocks,
            settings=self.disabled_model,
        )
        self.assertIn("请告诉", result.response)

    def test_public_process_events_are_emitted(self):
        events = []
        handle_chat(
            "分析 WH2025_DEMO001",
            self.blocks,
            settings=self.disabled_model,
            event_callback=events.append,
        )
        self.assertEqual(events[0].phase, "understand")
        self.assertIn("action", {event.phase for event in events})
        self.assertEqual(events[-1].phase, "answer")


if __name__ == "__main__":
    unittest.main()
