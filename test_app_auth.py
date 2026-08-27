import unittest
from unittest.mock import patch
from pathlib import Path

import app


class AppAuthTest(unittest.TestCase):
    def test_info_reports_when_auth_is_disabled(self):
        with patch.object(app.config, "ACCESS_TOKEN", ""):
            self.assertFalse(app.index()["auth_required"])

    def test_info_reports_when_auth_is_required(self):
        with patch.object(app.config, "ACCESS_TOKEN", "secret"):
            self.assertTrue(app.index()["auth_required"])

    def test_audio_player_has_responsive_controls_and_fallback_links(self):
        html = Path(__file__).with_name("static").joinpath("index.html").read_text()
        self.assertIn('class="result-audio"', html)
        self.assertIn('aria-label="播放生成音频"', html)
        self.assertIn('class="result-audio-actions"', html)
        self.assertIn('href="${src}" download', html)
        self.assertIn('@media (max-width:600px)', html)
        self.assertIn('async function restoreSession()', html)
        self.assertIn("if (!await restoreSession()) renderWelcome();", html)
        self.assertIn("async function fetchJson", html)
        self.assertIn("tool === 'get_account'", html)
        self.assertIn('class="account-card"', html)


if __name__ == "__main__":
    unittest.main()
