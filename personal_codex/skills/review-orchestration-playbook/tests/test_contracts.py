from __future__ import annotations

import pathlib
import subprocess
import sys
import tomllib
import unittest


SKILL_ROOT = pathlib.Path(__file__).resolve().parents[1]
OVERLAY_ROOT = SKILL_ROOT.parents[1]
REPO_ROOT = OVERLAY_ROOT.parent
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from review_runtime import common, providers  # noqa: E402


class RepositoryContractTest(unittest.TestCase):
    def test_only_canonical_review_skill_entrypoint_remains(self) -> None:
        self.assertTrue((SKILL_ROOT / "SKILL.md").is_file())
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("installs a readonly Git shim", skill)
        for relative in (
            "skills/external-review-playbook/SKILL.md",
            "skills/pr-readiness-review-workflow/SKILL.md",
            "skills/copilot-review-playbook/SKILL.md",
            "skills/review-orchestration-playbook/scripts/isolated_external_review",
            "skills/review-orchestration-playbook/scripts/isolated_copilot_review",
            "skills/review-orchestration-playbook/scripts/git_readonly_shim",
        ):
            self.assertFalse((OVERLAY_ROOT / relative).exists(), relative)

    def test_models_are_pinned_in_runtime_and_clean_context_agent(self) -> None:
        self.assertEqual(providers.CODEX_MODELS, ("gpt-5.6-sol", "gpt-5.5"))
        self.assertEqual(providers.CODEX_REASONING_EFFORT, "xhigh")
        self.assertEqual(
            providers.CLAUDE_MODELS,
            ("claude-opus-4-8", "claude-opus-4-7"),
        )
        self.assertEqual(providers.CLAUDE_SUPPORTED_VERSION, "2.1.202")
        self.assertEqual(
            providers.CLAUDE_TRUSTED_SHA256_BY_MACHINE,
            {
                "arm64": "7414f707861e2fe5afef33a466f888a8d2170e5028f5e9d2858f1d3ef45ffca5",
                "x86_64": "0dc578bb294094f5041e99a0444030ac6ae7236b387e56f00d4a5214816763bd",
            },
        )
        for candidate in (
            SKILL_ROOT / "SKILL.md",
            SKILL_ROOT / "references/helper-contract.md",
        ):
            self.assertNotIn(
                "claude-sonnet-5",
                candidate.read_text(encoding="utf-8"),
                str(candidate),
            )
        with (OVERLAY_ROOT / "agents/reviewer.toml").open("rb") as handle:
            reviewer = tomllib.load(handle)
        self.assertEqual(reviewer["model"], "gpt-5.6-sol")
        self.assertEqual(reviewer["model_reasoning_effort"], "xhigh")

    def test_claude_policy_defaults_to_local_login_in_safe_mode(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        helper_contract = (SKILL_ROOT / "references/helper-contract.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("ordinary local Claude login by default", skill)
        self.assertIn("Claude Code `2.1.202`", skill)
        self.assertIn("runs in safe mode", helper_contract)
        self.assertIn("`2.1.202 (Claude Code)`", helper_contract)
        self.assertNotIn("2.1.187", skill)
        self.assertNotIn("2.1.187", helper_contract)
        self.assertIn(
            "hardening-compatible `default` permission mode",
            helper_contract,
        )
        self.assertIn(
            "noninteractive process cannot approve any unmatched access",
            helper_contract,
        )
        self.assertNotIn("safe mode with `dontAsk` permissions", helper_contract)
        self.assertIn("complete SHA-256 digests", helper_contract)
        self.assertIn("downloads.claude.ai", helper_contract)
        self.assertIn("separate default-deny `sandbox-exec` profile", helper_contract)
        self.assertIn("ordinary macOS OAuth/keychain login", helper_contract)
        self.assertIn("localhost CONNECT proxy", helper_contract)
        self.assertIn("verifies unconditional roots", helper_contract)
        self.assertIn("Every non-empty non-deny array", helper_contract)
        self.assertIn("conservatively omitted rather than flattened", helper_contract)
        self.assertIn("excluded from additional roots", helper_contract)
        self.assertIn("fixed system baseline", helper_contract)
        self.assertIn("not exposed through `SSL_CERT_DIR`", helper_contract)
        self.assertIn("`keyUsage` with `keyCertSign`", helper_contract)
        self.assertIn(
            "`/usr/bin/openssl verify -x509_strict -check_ss_sig`", helper_contract
        )
        self.assertIn("trust-policy-unrepresentable", helper_contract)
        self.assertIn("Any explicit deny is a distinct hard stop", helper_contract)
        self.assertIn("checks later trust domains", helper_contract)
        self.assertIn("`claude-trust-policy.json`", helper_contract)
        self.assertIn("never retains fingerprints", helper_contract)
        self.assertIn("the default Keychain search list", helper_contract)
        self.assertIn("`/Library/Keychains/System.keychain`", helper_contract)
        self.assertIn(
            "`/System/Library/Keychains/SystemRootCertificates.keychain`",
            helper_contract,
        )
        self.assertIn("more than 4,096 entries", helper_contract)
        self.assertIn("At most 256 distinct additional roots", helper_contract)
        self.assertIn("30-second deadline", helper_contract)
        self.assertIn("recognizable exact Deny takes precedence", helper_contract)
        self.assertIn("anchored to one file descriptor", helper_contract)
        self.assertIn("fresh generation in `checking` state", helper_contract)
        self.assertIn("`complete`, `denied`, `blocked`", helper_contract)
        self.assertIn("one model attempt at a time", helper_contract)
        self.assertIn("`claude-auth-warmup.json`", helper_contract)
        self.assertIn("`SSL_CERT_FILE` and `NODE_EXTRA_CA_CERTS`", helper_contract)
        self.assertIn("one validated helper-owned bundle", helper_contract)
        self.assertIn("user, admin, and system trust domains", helper_contract)
        self.assertIn("fixed bounded SecurityTool commands", helper_contract)
        self.assertIn("explicit `file-read*` deny", helper_contract)
        self.assertIn("after all broad read allows", helper_contract)
        self.assertIn("missing `/usr/bin/security`", helper_contract)
        self.assertNotIn("requires `ANTHROPIC_API_KEY`", skill)

    def test_ci_targets_only_the_canonical_runtime_and_tests(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("review-orchestration-playbook/tests", workflow)
        self.assertNotIn("external-review-playbook", workflow)
        self.assertNotIn("copilot-review-playbook", workflow)

    def test_full_pr_readiness_retains_both_local_codex_gates(self) -> None:
        readiness = (SKILL_ROOT / "references/pr-readiness.md").read_text(
            encoding="utf-8"
        )
        contracts = (SKILL_ROOT / "references/review-lane-contracts.md").read_text(
            encoding="utf-8"
        )
        for value in (readiness, contracts):
            self.assertIn("independent-codex-pr-review", value)
            self.assertIn("offline-frozen-diff-review", value)
        self.assertIn("standalone double/triple-review", readiness)
        self.assertLess(
            readiness.index("3. Run `offline-frozen-diff-review` first"),
            readiness.index("4. After the helper preflight passes"),
        )
        self.assertIn("Require its retained `preflight.json`", readiness)

    def test_independent_codex_process_output_is_task_scoped_and_bounded(self) -> None:
        readiness = (SKILL_ROOT / "references/pr-readiness.md").read_text(
            encoding="utf-8"
        )
        contracts = (SKILL_ROOT / "references/review-lane-contracts.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("stdout and stderr in task-scoped bounded sinks", readiness)
        self.assertIn("--output-last-message <task-scoped-target>", readiness)
        self.assertIn(
            "byte limits for each process log and the final-message", readiness
        )
        self.assertIn("default 30-minute / 16-MiB / 64-KiB limits", readiness)
        self.assertIn("deadline expires or any output limit", readiness)
        self.assertIn("limit-terminated attempt is inconclusive", readiness)
        self.assertIn("bounded sinks", readiness)
        self.assertIn("bounded FIFO/pipe", readiness)
        self.assertIn("distinct fresh ordinary artifact", readiness)
        self.assertIn("only that ordinary artifact", readiness)
        self.assertIn("Enforce every cap while the reviewer runs", readiness)
        self.assertIn("OS-enforced job/cgroup/container", readiness)
        self.assertIn("survives `setsid` / `setpgid`", readiness)
        self.assertIn("verified kernel no-child-process policy", readiness)
        self.assertIn("fully self-contained artifact-only review", readiness)
        self.assertIn("complete diff and permitted neighboring evidence", readiness)
        self.assertIn("tool calls are forbidden", readiness)
        self.assertIn("report `blocked` and do not launch", readiness)
        self.assertIn("descendant polling is not a substitute", readiness)
        self.assertIn("separate 10-second deadline", readiness)
        self.assertIn("stat every ordinary output artifact again", readiness)
        self.assertIn("never use FIFO metadata", readiness)
        self.assertIn("even after exit zero", readiness)
        self.assertIn("quiescence or sink closure cannot be confirmed", readiness)
        self.assertIn("Poll only with bounded status probes", readiness)
        self.assertIn("Parent-Process Output Budget", readiness)
        self.assertIn("do not stream either process output", contracts)
        self.assertIn("--output-last-message <task-scoped-target>", contracts)
        self.assertIn("unique path that does not exist before the attempt", contracts)
        self.assertIn("freshly created before launch at one path", contracts)
        self.assertIn("different ordinary artifact path", contracts)
        self.assertIn("byte limit for the final-message artifact", contracts)
        self.assertIn("30-minute deadline, 16 MiB", contracts)
        self.assertIn("64 KiB for the final-message artifact", contracts)
        self.assertIn("send `TERM`", contracts)
        self.assertIn("send `KILL`", contracts)
        self.assertIn("when the deadline expires", contracts)
        self.assertIn("hard per-file quota or bounded sink", contracts)
        self.assertIn("bounded FIFO/pipe reader", contracts)
        self.assertIn(
            "Direct-path monitoring or a post-exit size check alone", contracts
        )
        self.assertIn("OS-enforced job, cgroup, or container", contracts)
        self.assertIn("survives `setsid` / `setpgid`", contracts)
        self.assertIn("kernel-enforced no-child-process policy", contracts)
        self.assertIn("fully self-contained artifact-only review", contracts)
        self.assertIn("complete diff and permitted neighboring evidence", contracts)
        self.assertIn("prompt forbids tool calls", contracts)
        self.assertIn("report `blocked` and do not launch", contracts)
        self.assertIn("descendant polling may provide diagnostics", contracts)
        self.assertIn("never substitute for containment", contracts)
        self.assertIn("separate 10-second close deadline", contracts)
        self.assertIn("waiting indefinitely", contracts)
        self.assertIn("Do not accept a final-message artifact", contracts)
        self.assertIn("file byte or line counts", contracts)
        self.assertIn("attempt exits zero", contracts)
        self.assertIn("creates it as a nonempty file", contracts)
        self.assertIn(
            "stat both process logs and the ordinary final-message artifact", contracts
        )
        self.assertIn("Never use a FIFO's `st_size`", contracts)
        self.assertIn("even when it exited zero", contracts)
        self.assertIn("reaches or exceeds", contracts)
        self.assertIn("record only the byte counts", contracts)
        self.assertIn("remove the oversized artifact", contracts)
        self.assertIn("reject any stale or partial result", contracts)
        self.assertIn("On a nonzero exit or a missing/empty file", contracts)
        self.assertIn("read at most the final 8 KiB of stderr", contracts)
        self.assertIn("byte-count-limited read", contracts)
        self.assertIn("truncates before inserting text", contracts)
        self.assertIn("line-count-only command", contracts)
        self.assertIn("single long JSON or trace line", contracts)
        self.assertIn("runtime-verification failure as `blocked`", contracts)
        self.assertIn("otherwise report `inconclusive`", contracts)
        self.assertIn("Never read the complete stderr", contracts)
        self.assertIn(
            "Remove task-scoped process logs and the final-message file",
            contracts,
        )
        self.assertIn("reported blocker or recovery handoff", contracts)
        self.assertIn("remove the oversized log", contracts)
        self.assertIn("read at most the final 8 KiB of stderr", readiness)
        self.assertIn("line-count-only tail is not bounded", readiness)

    def test_review_prompts_do_not_use_unbounded_only_matching_samples(self) -> None:
        forbidden = "rg -o --max-count 80"
        candidates = [
            SKILL_ROOT / "SKILL.md",
            SKILL_ROOT / "scripts/review_runtime/prompt.py",
        ]
        candidates.extend((SKILL_ROOT / "references").glob("*.md"))
        for candidate in candidates:
            self.assertNotIn(
                forbidden,
                candidate.read_text(encoding="utf-8"),
                str(candidate),
            )

    def test_cli_rejects_claude_lane_without_visible_consent(self) -> None:
        completed = subprocess.run(
            (
                str(SCRIPTS / "isolated_review"),
                "--reviewer",
                "claude",
                "--base-ref",
                "base",
                "--head-ref",
                "head",
            ),
            cwd=REPO_ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("--egress-consent", completed.stderr)

    def test_claude_lane_has_no_copilot_provider_or_credentials(self) -> None:
        consent = (SKILL_ROOT / "references/egress-consent.md").read_text(
            encoding="utf-8"
        )
        helper_contract = (SKILL_ROOT / "references/helper-contract.md").read_text(
            encoding="utf-8"
        )
        provider_source = (
            SKILL_ROOT / "scripts/review_runtime/providers.py"
        ).read_text(encoding="utf-8")
        common_source = (SKILL_ROOT / "scripts/review_runtime/common.py").read_text(
            encoding="utf-8"
        )
        readiness = (SKILL_ROOT / "references/pr-readiness.md").read_text(
            encoding="utf-8"
        )
        for content in (
            consent,
            helper_contract,
            provider_source,
            common_source,
            readiness,
        ):
            self.assertNotIn("copilot", content.lower())
        for credential in (
            "COPILOT_GITHUB_TOKEN",
            "GH_TOKEN",
            "GITHUB_TOKEN",
            "CODEX_REVIEW_COPILOT_PATH",
        ):
            self.assertNotIn(credential, provider_source)
            self.assertNotIn(credential, common_source)
        self.assertEqual(
            providers.CLAUDE_ENV_KEYS,
            ("ANTHROPIC_API_KEY", "NODE_EXTRA_CA_CERTS"),
        )
        self.assertFalse(hasattr(providers, "COPILOT_MODELS"))
        self.assertFalse(hasattr(providers, "_copilot_attempt"))
        with self.assertRaisesRegex(common.ReviewError, "unknown review executable"):
            common.resolve_reviewer_executable("copi" + "lot")
        self.assertIn("native runtime, authentication, or pinned models", consent)
        self.assertIn("explicit trust denies remain distinct hard stops", consent)
        self.assertIn("stop the lane without changing provider", helper_contract)

    def test_triple_review_consent_names_all_provider_organizations(self) -> None:
        candidates = [
            SKILL_ROOT / "SKILL.md",
            SKILL_ROOT / "references/egress-consent.md",
        ]
        repo_agents = REPO_ROOT / "AGENTS.md"
        if repo_agents.is_file():
            candidates.append(repo_agents)
        for candidate in candidates:
            content = candidate.read_text(encoding="utf-8")
            self.assertIn(
                "OpenAI and Anthropic",
                content,
                str(candidate),
            )
            self.assertIn("GitHub Codex review", content, str(candidate))


if __name__ == "__main__":
    unittest.main()
