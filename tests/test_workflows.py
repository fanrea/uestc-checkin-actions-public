from pathlib import Path
import re
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class WorkflowTests(unittest.TestCase):
    def load(self, name):
        path = ROOT / ".github/workflows" / name
        return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)

    def test_schedule_and_manual_validation_default(self):
        document = self.load("checkin.yml")
        self.assertEqual(
            document["on"]["schedule"],
            [{"cron": "17 21 * * *", "timezone": "Asia/Shanghai"}],
        )
        mode = document["on"]["workflow_dispatch"]["inputs"]["mode"]
        self.assertEqual(mode["default"], "validate")
        self.assertEqual(mode["options"], ["validate", "submit"])
        environment = document["jobs"]["checkin"]["steps"][-1]["env"]
        self.assertEqual(environment["CHECKIN_PROFILE_JSON"], "${{ secrets.CHECKIN_PROFILE_JSON }}")
        guard = document["jobs"]["checkin"]["if"]
        self.assertIn("vars.CHECKIN_ENABLED == 'true'", guard)
        self.assertIn("inputs.mode == 'validate'", guard)

    def test_pinned_actions_and_read_only_permissions(self):
        for name in ("checkin.yml", "tests.yml"):
            document = self.load(name)
            self.assertEqual(document["permissions"], {"contents": "read"})
            for job in document["jobs"].values():
                for step in job["steps"]:
                    if "uses" in step:
                        self.assertRegex(step["uses"], r"^actions/[\w-]+@[0-9a-f]{40}$")
                        self.assertNotIn("upload-artifact", step["uses"])

    def test_only_runtime_step_receives_secrets(self):
        document = self.load("checkin.yml")
        job = document["jobs"]["checkin"]
        secret_steps = [step for step in job["steps"] if "secrets." in str(step)]
        self.assertEqual(len(secret_steps), 1)
        self.assertEqual(secret_steps[0]["run"], "python run_actions.py")
        self.assertNotIn("secrets.", str(self.load("tests.yml")))

    def test_workflow_cannot_overlap_or_cancel_an_active_submit(self):
        document = self.load("checkin.yml")
        self.assertEqual(document["concurrency"]["cancel-in-progress"], "false")
        self.assertEqual(document["jobs"]["checkin"]["timeout-minutes"], "5")
        self.assertIn("default_branch", document["jobs"]["checkin"]["if"])
