import unittest

import agent
import orchestration
import subagents


class StubAgent:
    def __init__(self, decision):
        self.decision = decision
        self.calls = []

    def plan(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.decision


class OrchestrationTest(unittest.TestCase):
    def setUp(self):
        self.original_agent_hq = agent.hq.run
        self.original_subagent_hq = subagents.hq.run

    def tearDown(self):
        agent.hq.run = self.original_agent_hq
        subagents.hq.run = self.original_subagent_hq

    def install_fake_hq(self):
        calls = []

        def fake_hq(capability, params, confirm=False, quote_token=None):
            calls.append((capability, params, confirm, quote_token))
            if capability == "voices":
                return {"result": {"items": [
                    {"voice_key": "vip_voice_5", "display_name": "音色5"},
                ]}}
            if capability == "video-avatars":
                return {"result": {"items": [{"id": 525, "status": "ready"}]}}
            if capability == "digital-ip-text-generate" and not confirm:
                return {"result": {
                    "quote_token": "quote-1", "cost": 30, "points": 999,
                }}
            if capability == "digital-ip-text-generate" and confirm:
                self.assertEqual(quote_token, "quote-1")
                return {"result": {"job_id": 123}}
            raise AssertionError(f"unexpected capability: {capability}")

        agent.hq.run = fake_hq
        subagents.hq.run = fake_hq
        return calls

    def test_coordinator_delegates_to_independent_production_quote(self):
        calls = self.install_fake_hq()
        coordinator = StubAgent({
            "type": "tool", "tool": "delegate_production",
            "params": {"goal": "生成数字人口播"},
        })
        production = StubAgent({
            "type": "tool", "tool": "delegate_digital_human",
            "params": {
                "intent": "数字人口播",
                "params": {"text": "大家好", "voice": "音色5"},
                "confirmed": True,
                "quote_token": "model-must-not-control-this",
            },
        })

        result = orchestration.run_turn(
            "用音色5生成数字人口播，文案：大家好",
            coordinator=coordinator,
            production=production,
        )

        self.assertEqual(result["type"], "quote")
        self.assertEqual(result["quote_token"], "quote-1")
        self.assertEqual(len(coordinator.calls), 1)
        self.assertEqual(len(production.calls), 1)
        self.assertTrue(all(not confirm for _, _, confirm, _ in calls))

    def test_coordinator_cannot_call_business_tool_directly(self):
        coordinator = StubAgent({
            "type": "tool", "tool": "generate_audio", "params": {"text": "hi"},
        })
        production = StubAgent({"type": "text", "text": "must not run"})
        result = orchestration.run_turn(
            "生成配音", coordinator=coordinator, production=production)
        self.assertEqual(result["type"], "error")
        self.assertIn("未授权", result["message"])
        self.assertEqual(production.calls, [])

    def test_approval_uses_original_quote_without_new_agent_calls(self):
        calls = self.install_fake_hq()
        coordinator = StubAgent({"type": "text", "text": "must not run"})
        production = StubAgent({"type": "text", "text": "must not run"})
        pending = {
            "tool": "delegate_digital_human",
            "params": {
                "intent": "数字人口播",
                "params": {"avatar_id": 525, "text": "大家好", "voice": "vip_voice_5"},
            },
            "quote_token": "quote-1",
        }
        result = orchestration.run_turn(
            "确认执行",
            pending_quote=pending,
            coordinator=coordinator,
            production=production,
        )
        self.assertEqual(result["type"], "running")
        self.assertEqual(result["job_id"], "123")
        self.assertEqual(coordinator.calls, [])
        self.assertEqual(production.calls, [])
        confirms = [c for c in calls if c[2]]
        self.assertEqual(len(confirms), 1)
        self.assertEqual(confirms[0][3], "quote-1")


if __name__ == "__main__":
    unittest.main()
