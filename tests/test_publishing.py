"""Exercise the real publication guards without credentials or registry writes."""

import os
from pathlib import Path
import subprocess
import textwrap
import unittest


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/reusable-build-bootc.yaml"


def step_script(name):
    step = WORKFLOW.read_text().split(f"      - name: {name}\n", 1)[1]
    step = step.split("\n      - name:", 1)[0]
    return textwrap.dedent(step.split("        run: |\n", 1)[1])


class PublishingTests(unittest.TestCase):
    def setUp(self):
        self.env = os.environ | {
            "PUBLISH_IMAGE": "true", "CANDIDATE_IMAGE": "true",
            "IMAGE_TAG": "candidate-123-1", "DEFAULT_TAG": "candidate-123-1",
            "EVENT_NAME": "workflow_dispatch", "SOURCE_REF": "refs/heads/feat/fedora-45",
            "DEFAULT_BRANCH": "main", "RUN_ID": "123", "RUN_ATTEMPT": "1",
            "GITHUB_SHA": "a" * 40,
            "SIGNING_SECRET": "unused-fixture-only",
        }

    def check_script(self, script, changes=None, succeeds=True):
        result = subprocess.run(
            ["bash", "-c", script], env=self.env | (changes or {}),
            text=True, capture_output=True,
        )
        if succeeds:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)

    def test_non_publishing_pr_needs_no_credentials(self):
        self.check_script(step_script("Validate publishing context"), {
            "PUBLISH_IMAGE": "false", "EVENT_NAME": "pull_request",
            "SOURCE_REF": "refs/pull/12/merge", "SIGNING_SECRET": "",
        })

    def test_manual_candidate_branch_is_allowed(self):
        self.check_script(step_script("Validate publishing context"))

    def test_candidate_cannot_publish_on_a_pr_or_tag(self):
        for changes in (
            {"EVENT_NAME": "pull_request"},
            {"EVENT_NAME": "pull_request_target"},
            {"EVENT_NAME": "push"},
            {"SOURCE_REF": "refs/tags/candidate"},
        ):
            with self.subTest(changes=changes):
                self.check_script(step_script("Validate publishing context"), changes, succeeds=False)

    def test_candidate_tag_must_belong_to_this_run_attempt(self):
        for tag in ("latest", "45", "candidate-124-1", "candidate-123-2", ""):
            with self.subTest(tag=tag):
                self.check_script(step_script("Validate publishing context"), {"IMAGE_TAG": tag}, succeeds=False)

    def test_production_is_limited_to_main_and_trusted_events(self):
        env = {"CANDIDATE_IMAGE": "false", "IMAGE_TAG": "latest", "SOURCE_REF": "refs/heads/main"}
        for event in ("push", "schedule", "workflow_dispatch"):
            self.check_script(step_script("Validate publishing context"), env | {"EVENT_NAME": event})
        for changes in (
            {"SOURCE_REF": "refs/heads/feat/fedora-45"},
            {"EVENT_NAME": "pull_request"},
            {"EVENT_NAME": "pull_request_target"},
        ):
            self.check_script(step_script("Validate publishing context"), env | changes, succeeds=False)

    def test_candidate_manifest_allows_only_its_own_tags(self):
        # Stop before the first privileged operation. Run the actual guard,
        # without creating a key file or executing a publishing command.
        script = step_script("Publish manifest with unified architecture and sign")
        script = script.split("sudo install -d", 1)[0]
        self.check_script(script, {"TAGS": "candidate-123-1\ncandidate-123-1-" + "a" * 40})
        for tag in ("latest", "45", "20261105", "candidate-124-1", ""):
            with self.subTest(tag=tag):
                self.check_script(script, {"TAGS": "candidate-123-1\n" + tag}, succeeds=False)


if __name__ == "__main__":
    unittest.main()
