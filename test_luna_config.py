import unittest
from unittest.mock import patch

import config
import llm


class LunaConfigTest(unittest.TestCase):
    def test_luna_is_default_model(self):
        self.assertEqual(config.current_model(), "luna")
        luna = config.llm_config()
        self.assertEqual(luna["provider"], "openai")
        self.assertEqual(luna["model"], "gpt-5.6-luna")
        self.assertEqual(luna["reasoning_effort"], "low")

    def test_responses_request_includes_reasoning_effort(self):
        captured = {}

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"output": [{
                    "type": "message",
                    "content": [{"type": "output_text", "text": "LUNA_OK"}],
                }]}

        def fake_post(url, headers, json, timeout, proxies):
            captured.update({"url": url, "headers": headers, "body": json})
            return Response()

        with patch.object(llm.requests, "post", fake_post):
            result = llm._responses(
                [{"role": "system", "content": "test"},
                 {"role": "user", "content": "hello"}],
                tools=[],
                max_tokens=64,
            )

        self.assertEqual(result["content"], "LUNA_OK")
        self.assertTrue(captured["url"].endswith("/responses"))
        self.assertEqual(captured["body"]["model"], "gpt-5.6-luna")
        self.assertEqual(captured["body"]["reasoning"], {"effort": "low"})


if __name__ == "__main__":
    unittest.main()
