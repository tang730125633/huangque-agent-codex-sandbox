import unittest
from unittest.mock import patch

import app


class AppAuthTest(unittest.TestCase):
    def test_info_reports_when_auth_is_disabled(self):
        with patch.object(app.config, "ACCESS_TOKEN", ""):
            self.assertFalse(app.index()["auth_required"])

    def test_info_reports_when_auth_is_required(self):
        with patch.object(app.config, "ACCESS_TOKEN", "secret"):
            self.assertTrue(app.index()["auth_required"])


if __name__ == "__main__":
    unittest.main()
