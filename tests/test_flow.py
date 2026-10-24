from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fixture_session import CODE, COOKIE, TICKET, make_state
from fixture_profile import write_profile
import pure_checkin_flow as flow


ROOT = Path(__file__).resolve().parents[1]


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        state, cookie = root / "state.json", root / "cookie.txt"
        state.write_text(json.dumps(make_state()), encoding="utf-8")
        cookie.write_text(COOKIE, encoding="utf-8")
        self.args = SimpleNamespace(
            state=state, cookie_file=cookie, token_file=None, timeout=5,
            profile=write_profile(root), output=root / "result.json",
        )
        self.result = {
            "ok": False, "stage": "starting",
            "requestCounts": {"code": 0, "ticket": 0, "checkin": 0},
        }

    def run_mocked(self, responses):
        with patch.object(flow, "request_code", return_value=(CODE, b"")), \
                patch.object(flow, "post_json", side_effect=responses) as post, \
                redirect_stdout(StringIO()):
            flow.run_flow(self.args, self.result)
        return post

    def test_full_flow_headers_payload_and_counts(self):
        post = self.run_mocked([
            {"httpStatus": 200, "body": {"ok": True, "data": {"ticket": TICKET}}},
            {"httpStatus": 200, "body": {"code": 0}},
        ])
        self.assertTrue(self.result["ok"])
        self.assertEqual(self.result["requestCounts"], {"code": 1, "ticket": 1, "checkin": 1})
        path, payload, headers, _ = post.call_args_list[1].args
        self.assertEqual(path, flow.CHECKIN_PATH)
        self.assertEqual(headers["X-Checkin-Ticket"], TICKET)
        self.assertEqual(headers["platform"], "h5")
        self.assertEqual(headers["Cookie"], COOKIE)
        self.assertEqual(len(payload), 13)
        self.assertNotIn("description", payload)
        self.assertRegex(payload["clientTime"], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")

    def test_ticket_rejection_stops_before_checkin(self):
        post = self.run_mocked([{"httpStatus": 401, "body": {"ok": False}}])
        self.assertEqual(post.call_count, 1)
        self.assertEqual(self.result["requestCounts"]["checkin"], 0)
        self.assertEqual(self.result["stage"], "ticket_rejected")

    def test_http_200_with_business_error_is_failure(self):
        self.run_mocked([
            {"httpStatus": 200, "body": {"ok": True, "data": {"ticket": TICKET}}},
            {"httpStatus": 200, "body": {"code": 4006}},
        ])
        self.assertFalse(self.result["ok"])
        self.assertEqual(self.result["stage"], "checkin_rejected")

    def test_success_detection_requires_business_success(self):
        self.assertFalse(flow.checkin_accepted({"httpStatus": 200, "body": {"code": False}}))
        self.assertFalse(flow.checkin_accepted({"httpStatus": 401, "body": {"code": 0}}))
        self.assertTrue(flow.checkin_accepted({"httpStatus": 200, "body": {"status": True}}))

    def test_cookie_header_injection_rejected(self):
        self.args.cookie_file.write_text(COOKIE + "\nInjected: value", encoding="utf-8")
        with self.assertRaises(ValueError):
            flow.read_cookie(self.args.cookie_file)

    def test_boolean_accuracy_rejected(self):
        profile = json.loads(self.args.profile.read_text(encoding="utf-8"))
        profile["payload"]["accuracy"] = True
        path = Path(self.directory.name) / "profile.json"
        path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaises(ValueError):
            flow.read_profile(path)
