import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fixture_profile import ROOT, make_profile, write_profile
from fixture_session import COOKIE, encoded_state
from profile_config import select_profile, validate_profile
import pure_checkin_flow as flow
from tools.export_secrets import export


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def test_example_rejected_until_configured(self):
        with self.assertRaises(ValueError):
            validate_profile(ROOT / "checkin-request-profile.example.json")

    def test_placeholders_rejected_even_when_configured(self):
        profile = make_profile()
        profile["payload"]["userAgent"] = "REPLACE_WITH_YOUR_DEVICE_USER_AGENT"
        path = self.directory / "placeholder.json"
        path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaises(ValueError):
            validate_profile(path)

    def test_secret_profile_overrides_local_file_without_editing_it(self):
        local = write_profile(self.directory)
        original = local.read_bytes()
        secret = make_profile()
        secret["payload"]["latitude"] = 10
        selected = select_profile(
            local, self.directory, {"CHECKIN_PROFILE_JSON": json.dumps(secret)},
        )
        self.assertEqual(validate_profile(selected)["payload"]["latitude"], 10)
        self.assertEqual(local.read_bytes(), original)

    def test_large_profile_rejected(self):
        with self.assertRaises(ValueError):
            select_profile(
                self.directory / "missing.json", self.directory,
                {"CHECKIN_PROFILE_JSON": "x" * (16 * 1024 + 1)},
            )

    def test_export_three_secrets_offline(self):
        state = self.directory / "state.json"
        state.write_bytes(base64.b64decode(encoded_state()))
        cookie = self.directory / "cookie.txt"
        cookie.write_text(COOKIE, encoding="utf-8")
        profile = write_profile(self.directory)
        output = self.directory / "secrets"
        with patch.object(flow, "run_flow") as run:
            result = export(state, cookie, output, profile)
        run.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertEqual(
            {p.name for p in output.iterdir()},
            {"WECHAT_LOGIN_STATE_B64.txt", "CHECKIN_COOKIE.txt", "CHECKIN_PROFILE_JSON.txt"},
        )
        self.assertEqual(
            validate_profile(output / "CHECKIN_PROFILE_JSON.txt"), make_profile(),
        )
