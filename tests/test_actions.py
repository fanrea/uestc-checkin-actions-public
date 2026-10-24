from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fixture_session import CODE, COOKIE, TICKET, encoded_state
from fixture_profile import make_profile, write_profile
import pure_checkin_flow as flow
import run_actions as actions


ROOT = Path(__file__).resolve().parents[1]


class ActionsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.environment = {
            "WECHAT_LOGIN_STATE_B64": encoded_state(), "CHECKIN_COOKIE": COOKIE,
            "RUNNER_TEMP": str(self.root),
        }
        self.args = SimpleNamespace(
            profile=write_profile(self.root), timeout=5, validate_only=True,
        )
        self.result = {
            "ok": False, "stage": "starting", "startedAt": flow.timestamp(),
            "requestCounts": {"code": 0, "ticket": 0, "checkin": 0},
        }

    def test_validation_sends_zero_requests_and_cleans_temporary_files(self):
        with patch.object(flow, "run_flow") as run:
            actions.execute(self.args, self.result, self.environment)
        run.assert_not_called()
        self.assertTrue(self.result["ok"])
        self.assertTrue(self.result["offlineCaptureVerified"])
        self.assertEqual(self.result["requestCounts"], {"code": 0, "ticket": 0, "checkin": 0})
        self.assertEqual(list(self.root.iterdir()), [self.args.profile])

    def test_private_secret_files_have_restricted_unix_permissions(self):
        paths, verified = actions.prepare_files(self.root, self.environment)
        self.assertTrue(verified)
        self.assertEqual(flow.read_cookie(paths["cookie_file"]), COOKIE)
        if os.name != "nt":
            self.assertEqual(paths["state"].stat().st_mode & 0o777, 0o600)

    def test_public_summary_excludes_credentials_and_server_echo(self):
        self.result.update({
            "stage": "checkin_rejected", "code": CODE, "ticket": TICKET,
            "checkinResponse": {
                "httpStatus": 200,
                "body": {"code": 4006, "msg": COOKIE + CODE + TICKET},
            },
        })
        summary = actions.make_public_summary(self.result)
        encoded = json.dumps(summary)
        for secret in (COOKIE, CODE, TICKET):
            self.assertNotIn(secret, encoded)
        self.assertEqual(summary["serverCode"], 4006)
        self.assertNotIn("serverMessage", summary)

    def test_timeout_after_submit_is_unknown_without_retry_or_secret_log(self):
        def interrupted(args, result):
            result["stage"] = "checkin_sending"
            result["requestCounts"]["checkin"] = 1
            print("private captured output: " + COOKIE)
            raise TimeoutError(COOKIE)

        argv = ["run_actions.py", "--profile", str(self.args.profile)]
        output = StringIO()
        with patch.dict(os.environ, self.environment, clear=True), \
                patch("sys.argv", argv), patch.object(actions, "ROOT", self.root), \
                patch.object(flow, "run_flow", side_effect=interrupted) as run, \
                redirect_stdout(output):
            exit_code = actions.main()
        self.assertEqual(exit_code, 1)
        self.assertEqual(run.call_count, 1)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["stage"], "checkin_result_unknown")
        self.assertEqual(summary["errorType"], "TimeoutError")
        self.assertNotIn(COOKIE, output.getvalue())
        self.assertFalse(any(path.name.startswith("uestc-checkin-") for path in self.root.iterdir()))

    def test_missing_secrets_fail_before_flow(self):
        with self.assertRaises(ValueError):
            actions.prepare_files(self.root, {})

    def test_full_flow_uses_private_profile_secret(self):
        self.args.validate_only = False
        profile = make_profile()
        profile["payload"]["latitude"] = 3.75
        profile["payload"]["longitude"] = 4.5
        self.environment["CHECKIN_PROFILE_JSON"] = json.dumps(profile)
        responses = [
            {"httpStatus": 200, "body": {"ok": True, "data": {"ticket": TICKET}}},
            {"httpStatus": 200, "body": {"code": 4005, "msg": "fixture location rejected"}},
        ]
        with patch.object(flow, "request_code", return_value=(CODE, b"")), \
                patch.object(flow, "post_json", side_effect=responses) as post:
            actions.execute(self.args, self.result, self.environment)
        payload = post.call_args_list[-1].args[1]
        self.assertEqual(payload["latitude"], 3.75)
        self.assertEqual(payload["longitude"], 4.5)
        self.assertEqual(flow.read_profile(self.args.profile)[0]["latitude"], 1.25)
        self.assertEqual(self.result["requestCounts"], {"code": 1, "ticket": 1, "checkin": 1})
        self.assertEqual(list(self.root.iterdir()), [self.args.profile])
