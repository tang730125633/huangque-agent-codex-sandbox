import unittest

import agent
import subagents


class VoiceChangeTest(unittest.TestCase):
    def test_keeps_copy_that_starts_with_change_character(self):
        history = [
            {"role": "user", "content": "换季肌肤容易干燥敏感，补水锁水是关键。"},
            {"role": "user", "content": "换个音色，这个太像女人了"},
            {"role": "user", "content": "用第5个试试"},
        ]
        self.assertEqual(agent._find_prev_voice_text(history), history[0]["content"])

    def test_voice_number_fallback_quotes_same_copy_without_network(self):
        def fake_hq(capability, params, confirm=False, quote_token=None):
            if capability == "voices":
                return {"result": {"items": [
                    {"voice_key": f"vip_voice_{i}", "display_name": str(i)}
                    for i in range(1, 6)
                ]}}
            if capability == "video-avatars":
                return {"result": {"items": [{"id": 525, "status": "ready"}]}}
            if capability == "digital-ip-text-generate" and not confirm:
                return {"result": {
                    "quote_token": "test-quote",
                    "cost": 30,
                    "points": 999,
                }}
            raise AssertionError(f"unexpected external capability: {capability}")

        original_agent_hq = agent.hq.run
        original_subagent_hq = subagents.hq.run
        original_chat = agent.llm.chat
        try:
            agent.hq.run = fake_hq
            subagents.hq.run = fake_hq
            agent.llm.chat = lambda message, history=None: {"type": "text", "text": ""}
            copy = "换季肌肤容易干燥敏感，补水锁水是关键。"
            result = agent.run_turn(
                "用第5个试试",
                history=[{"role": "user", "content": copy}],
            )
        finally:
            agent.hq.run = original_agent_hq
            subagents.hq.run = original_subagent_hq
            agent.llm.chat = original_chat

        self.assertEqual(result["type"], "quote")
        self.assertEqual(result["quote_token"], "test-quote")
        self.assertEqual(result["params"]["params"], {
            "text": copy,
            "voice": "vip_voice_5",
            "avatar_id": 525,
        })

    def test_resource_queries_are_not_reused_as_spoken_copy(self):
        history = [
            {"role": "user", "content": "欢迎来到黄雀"},
            {"role": "user", "content": "查看一下我的形象"},
            {"role": "user", "content": "查看我的克隆音色"},
        ]
        self.assertEqual(agent._find_prev_voice_text(history), "欢迎来到黄雀")

    def test_avatar_number_resolves_to_ready_list_item(self):
        original = agent.hq.run
        try:
            agent.hq.run = lambda capability, params, confirm=False, quote_token=None: {
                "result": {"items": [
                    {"id": 529, "status": "ready"},
                    {"id": 525, "status": "ready"},
                ]}
            }
            self.assertEqual(agent._resolve_avatar(1), 529)
            self.assertEqual(agent._resolve_avatar("形象2"), 525)
            self.assertEqual(agent._resolve_avatar(525), 525)
        finally:
            agent.hq.run = original


if __name__ == "__main__":
    unittest.main()
