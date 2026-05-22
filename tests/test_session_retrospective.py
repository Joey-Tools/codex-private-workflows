from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "personal_codex"
    / "skills"
    / "codex-session-retrospective"
    / "scripts"
    / "session_retrospective.py"
)
SPEC = importlib.util.spec_from_file_location("session_retrospective", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def message(role: str, text: str, timestamp: str) -> dict:
    return {
        "type": "response_item",
        "timestamp": timestamp,
        "payload": {
            "type": "message",
            "role": role,
            "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}],
        },
    }


def event_user_message(text: str, timestamp: str) -> dict:
    return {
        "type": "event_msg",
        "timestamp": timestamp,
        "payload": {
            "type": "user_message",
            "message": text,
            "images": [],
            "local_images": [],
            "text_elements": [],
        },
    }


class SessionRetrospectiveOverlayTests(unittest.TestCase):
    def test_filename_parsers_support_current_and_legacy_rollouts(self) -> None:
        current = Path("rollout-2026-05-07T13-24-44-019d-uuid.jsonl")
        legacy = Path("rollout-2025-05-26-legacy-uuid.jsonl")

        self.assertEqual(MODULE.session_id_from_path(current), "019d-uuid")
        self.assertEqual(MODULE.session_id_from_path(legacy), "legacy-uuid")
        self.assertEqual(MODULE.iso(MODULE.rollout_date_from_path(current)), "2026-05-07T00:00:00Z")
        self.assertEqual(MODULE.iso(MODULE.rollout_date_from_path(legacy)), "2025-05-26T00:00:00Z")

    def test_prompt_category_uses_word_boundary_for_pr(self) -> None:
        self.assertEqual(MODULE.prompt_category("Improve this prompt for the project."), "general")
        self.assertEqual(MODULE.prompt_category("Review this PR."), "review")

    def test_extract_rollout_reads_event_msg_user_messages_once(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / ".codex"
            rollout = root / "sessions" / "2026" / "05" / "22" / "rollout-2026-05-22T10-00-00-event.jsonl"
            write_jsonl(
                rollout,
                [
                    event_user_message("Review the PR.", "2026-05-22T10:01:00.001Z"),
                    event_user_message("Review the PR.", "2026-05-22T10:01:00.002Z"),
                    message("assistant", "Reviewed it.", "2026-05-22T10:02:00Z"),
                ],
            )

            turns = MODULE.extract_rollout(MODULE.Source("local", root), rollout, None, None)

        self.assertEqual(len(turns), 1)
        self.assertIn("category=review", turns[0].redacted_user_prompt_summary)
        self.assertIn("assistant_messages=1", turns[0].assistant_action_summary)

    def test_daily_first_run_uses_active_lookback_days(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / ".codex"
            rollout = root / "sessions" / "2026" / "05" / "12" / "rollout-2026-05-12T10-00-00-active.jsonl"
            write_jsonl(rollout, [message("user", "Continue active work.", "2026-05-12T10:00:00Z")])
            output = Path(raw) / "out"
            state = Path(raw) / "state.json"

            with mock.patch.object(MODULE, "utc_now", return_value=MODULE.parse_time("2026-05-22T10:00:00Z")):
                MODULE.main(
                    [
                        "scan-daily",
                        "--active-lookback-days",
                        "14",
                        "--state",
                        str(state),
                        "--source",
                        f"local={root}",
                        "--output",
                        str(output),
                    ]
                )
            trend = json.loads((output / "trend_report.json").read_text(encoding="utf-8"))

        self.assertEqual(trend["turn_count"], 1)
        self.assertEqual(trend["window"]["start"], "2026-05-08T10:00:00Z")

    def test_validate_output_rejects_invalid_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = Path(raw) / "run"
            run_dir.mkdir()
            (run_dir / "turn_summaries.jsonl").write_text("{bad json\n", encoding="utf-8")
            write_jsonl(run_dir / "episodes.jsonl", [])
            write_jsonl(run_dir / "turn_flags.jsonl", [])
            (run_dir / "trend_report.json").write_text("{}\n", encoding="utf-8")
            (run_dir / "shard_manifest.json").write_text("{}\n", encoding="utf-8")

            with self.assertRaisesRegex(SystemExit, "invalid JSON"):
                MODULE.main(["validate-output", "--run-dir", str(run_dir)])


if __name__ == "__main__":
    unittest.main()
