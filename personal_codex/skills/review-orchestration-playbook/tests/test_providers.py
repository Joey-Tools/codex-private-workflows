from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
from dataclasses import replace
import hashlib
import json
import os
import pathlib
import plistlib
import signal
import shutil
import socket
import socketserver
import ssl
import sys
import tempfile
import threading
import time
import tomllib
import unittest
from unittest import mock


SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(SCRIPTS))

from review_runtime import common, providers  # noqa: E402
from review_runtime.common import Completed, ReviewError  # noqa: E402
from review_runtime.workspace import ReviewWorkspace  # noqa: E402


def oauth_credential_fixture(*, expires_in_seconds: float = 7200) -> bytes:
    payload: dict[str, object] = {
        "claudeAiOauth": {
            "access" + "Token": "fixture-" + "access-value",
            "refresh" + "Token": "fixture-" + "refresh-value",
            "expiresAt": (time.time() + expires_in_seconds) * 1000,
        }
    }
    return json.dumps(payload).encode()


class ProviderPolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temporary.name)
        source_root = root / "source"
        container = source_root / ".codex-tmp" / "isolated-review-test"
        workspace = container / "workspace"
        control = workspace / ".codex-review"
        control.mkdir(parents=True)
        diff_file = control / "review.diff"
        diff_file.write_text("diff --git a/a b/a\n", encoding="utf-8")
        (control / "changed-paths.z").write_bytes(b"")
        (control / "changed-blob-findings.z").write_bytes(b"")
        (control / "synthetic-secret-exemptions.json").write_text(
            json.dumps({"version": 1, "requested": [], "applied": []}) + "\n",
            encoding="utf-8",
        )
        prompt_file = control / "review.prompt"
        prompt_file.write_text("Review this diff.\n", encoding="utf-8")
        self.review = ReviewWorkspace(
            source_root=source_root,
            container_dir=container,
            workspace_root=workspace,
            base_ref="a" * 40,
            head_ref="b" * 40,
            diff_file=diff_file,
            prompt_file=prompt_file,
        )
        self.claude_broker = (
            container / "claude-runtime" / "keychain-broker" / "security"
        )
        self.claude_broker.parent.mkdir(parents=True)
        self.claude_broker.write_bytes(b"\xcf\xfa\xed\xfe" + b"\x00" * 32)
        self.claude_broker.chmod(0o700)
        self.claude_keychain_client = root / "host-tools" / "security"
        self.claude_ripgrep = root / "host-tools" / "rg"
        for fixture in (self.claude_keychain_client, self.claude_ripgrep):
            fixture.parent.mkdir(parents=True, exist_ok=True)
            fixture.write_bytes(b"fixture")
            fixture.chmod(0o700)
        self.claude_system_ca = root / "host-tools" / "cert.pem"
        self.claude_system_ca.write_bytes(self.sample_ca_certificate())
        self.host_dependency_patchers = (
            mock.patch.object(
                providers,
                "CLAUDE_KEYCHAIN_CLIENT",
                self.claude_keychain_client,
            ),
            mock.patch.object(
                providers,
                "CLAUDE_SYSTEM_CA_FILE",
                self.claude_system_ca,
            ),
            mock.patch.object(
                providers,
                "CLAUDE_REVIEW_TOOL_EXECUTABLE_CANDIDATES",
                (self.claude_ripgrep,),
            ),
        )
        for patcher in self.host_dependency_patchers:
            patcher.start()
        preflight_claude_trust_policy = providers._preflight_claude_trust_policy

        def run_preflight_claude_trust_policy(
            review: ReviewWorkspace,
            *,
            bundled_root_sha256_fingerprints: frozenset[bytes] = frozenset(),
        ) -> providers.ClaudeTrustMaterial:
            return preflight_claude_trust_policy(
                review,
                bundled_root_sha256_fingerprints=(bundled_root_sha256_fingerprints),
            )

        self.preflight_claude_trust_policy = run_preflight_claude_trust_policy
        self.trust_preflight_patcher = mock.patch.object(
            providers,
            "_preflight_claude_trust_policy",
        )
        self.trust_preflight = self.trust_preflight_patcher.start()
        self.trust_preflight.return_value = providers.ClaudeTrustMaterial(
            certificates=b"",
            excluded_sha1_fingerprints=frozenset(),
            bundled_root_sha256_fingerprints=frozenset(),
        )
        read_claude_trust_certificates = providers._read_claude_trust_certificates
        self.read_claude_trust_certificates = lambda *args, **kwargs: replace(
            read_claude_trust_certificates(*args, **kwargs),
            bundled_root_sha256_fingerprints=frozenset(),
        )
        self.trust_patcher = mock.patch.object(
            providers,
            "_read_claude_trust_certificates",
            return_value=providers.ClaudeTrustMaterial(
                certificates=b"",
                excluded_sha1_fingerprints=frozenset(),
                bundled_root_sha256_fingerprints=frozenset(),
            ),
        )
        self.trust = self.trust_patcher.start()
        self.native_macho_dependencies = providers._native_macho_dependencies
        self.native_dependency_patcher = mock.patch.object(
            providers,
            "_native_macho_dependencies",
            side_effect=lambda path, *, label: tuple(
                dict.fromkeys((path.absolute(), path.resolve()))
            ),
        )
        self.native_dependency_patcher.start()
        self.require_trusted_claude_digest = providers._require_trusted_claude_digest
        self.trusted_digest_patcher = mock.patch.object(
            providers,
            "_require_trusted_claude_digest",
        )
        self.trusted_digest = self.trusted_digest_patcher.start()
        self.trusted_digest.return_value = frozenset()
        self.prepare_claude_keychain_broker = providers._prepare_claude_keychain_broker
        self.keychain_broker_patcher = mock.patch.object(
            providers,
            "_prepare_claude_keychain_broker",
            side_effect=self.fake_prepare_claude_keychain_broker,
        )
        self.keychain_broker_patcher.start()
        self.claude_keychain_runtime = providers._claude_keychain_runtime
        self.keychain_runtime_patcher = mock.patch.object(
            providers,
            "_claude_keychain_runtime",
            side_effect=self.fake_claude_keychain_runtime,
        )
        self.keychain_runtime_patcher.start()
        self.require_fresh_claude_keychain_credential = (
            providers._require_fresh_claude_keychain_credential
        )
        self.warm_claude_local_login = providers._warm_claude_local_login
        self.warmup_patcher = mock.patch.object(
            providers,
            "_warm_claude_local_login",
        )
        self.warmup = self.warmup_patcher.start()

    def tearDown(self) -> None:
        self.warmup_patcher.stop()
        self.keychain_runtime_patcher.stop()
        self.keychain_broker_patcher.stop()
        self.trusted_digest_patcher.stop()
        self.native_dependency_patcher.stop()
        self.trust_patcher.stop()
        self.trust_preflight_patcher.stop()
        for patcher in reversed(self.host_dependency_patchers):
            patcher.stop()
        self.temporary.cleanup()

    def fake_prepare_claude_keychain_broker(
        self,
        _review: ReviewWorkspace,
        env: dict[str, str],
    ) -> dict[str, str]:
        result = dict(env)
        if not result.get("ANTHROPIC_API_KEY"):
            result["PATH"] = os.pathsep.join(
                value
                for value in (str(self.claude_broker.parent), result.get("PATH"))
                if value
            )
        return result

    @contextlib.contextmanager
    def fake_claude_keychain_runtime(
        self,
        _review: ReviewWorkspace,
        env: dict[str, str],
    ):
        result = dict(env)
        if not result.get("ANTHROPIC_API_KEY"):
            result[providers.CLAUDE_KEYCHAIN_BROKER_PORT_ENV] = "43211"
            result[providers.CLAUDE_KEYCHAIN_BROKER_CAPABILITY_ENV] = "00" * 32
        yield result

    def attempt(
        self,
        runtime: str,
        model: str,
        category: str,
        *,
        final_text: str | None = None,
    ) -> providers.Attempt:
        effort = "xhigh" if runtime == "codex" else "max"
        return providers.Attempt(
            runtime=runtime,
            requested_model=model,
            effective_model=model if final_text else None,
            requested_effort=effort,
            effective_effort=effort if final_text else None,
            returncode=0 if final_text else 1,
            category=category,
            final_text=final_text,
            stdout_path=str(self.review.container_dir / "stdout"),
            stderr_path=str(self.review.container_dir / "stderr"),
        )

    def logged_run_side_effect(
        self,
        *responses: Completed,
    ) -> Callable[..., Completed]:
        pending = iter(responses)

        def run_with_logs(
            _argv: tuple[str, ...] | list[str],
            **kwargs: object,
        ) -> Completed:
            completed = next(pending)
            stdout_path = kwargs.get("stdout_path")
            stderr_path = kwargs.get("stderr_path")
            if isinstance(stdout_path, pathlib.Path):
                stdout_path.parent.mkdir(parents=True, exist_ok=True)
                stdout_path.write_bytes(completed.stdout)
            if isinstance(stderr_path, pathlib.Path):
                stderr_path.parent.mkdir(parents=True, exist_ok=True)
                stderr_path.write_bytes(completed.stderr)
            return completed

        return run_with_logs

    def trust_export_help_capture(
        self,
        argv: tuple[str, ...],
    ) -> common.BoundedCapture:
        return common.BoundedCapture(
            argv=argv,
            returncode=0,
            stdout=bytearray(
                ("\n".join(providers.CLAUDE_TRUST_EXPORT_HELP_LINES) + "\n").encode()
            ),
            stderr=bytearray(),
        )

    def sample_ca_certificates(self, count: int) -> tuple[bytes, ...]:
        defaults = ssl.get_default_verify_paths()
        certificates: list[bytes] = []
        seen: set[bytes] = set()
        for raw in (
            defaults.cafile,
            "/etc/ssl/cert.pem",
            "/etc/ssl/certs/ca-certificates.crt",
        ):
            if not raw:
                continue
            path = pathlib.Path(raw)
            if not path.is_file():
                continue
            blocks = providers.CLAUDE_CERTIFICATE_BLOCK.findall(path.read_bytes())
            for block in blocks:
                certificate = block + b"\n"
                if certificate in seen:
                    continue
                seen.add(certificate)
                certificates.append(certificate)
                if len(certificates) == count:
                    return tuple(certificates)
        self.skipTest(f"fewer than {count} system PEM CA certificates are available")

    def sample_ca_certificate(self) -> bytes:
        return self.sample_ca_certificates(1)[0]

    @staticmethod
    def ca_sha256_fingerprint(certificate: bytes) -> bytes:
        der, _ = providers._canonical_ca_certificate(
            certificate,
            source="test fixture",
        )
        return hashlib.sha256(der).digest()

    @staticmethod
    def synthetic_private_key_pem(payload: bytes = b"fixture") -> bytes:
        label = b"PRIVATE" + b" KEY"
        return (
            b"-----BEGIN "
            + label
            + b"-----\n"
            + payload
            + b"\n-----END "
            + label
            + b"-----\n"
        )

    def pending_claude_trust_material(self) -> providers.ClaudeTrustMaterial:
        evidence = providers._new_claude_trust_policy_evidence()
        providers._write_claude_trust_policy_evidence(self.review, evidence)
        return providers.ClaudeTrustMaterial(
            certificates=b"",
            excluded_sha1_fingerprints=frozenset(),
            bundled_root_sha256_fingerprints=frozenset(),
            system_certificates=self.sample_ca_certificate(),
            evidence=evidence,
        )

    def strict_root_certificate(self) -> bytes:
        return (FIXTURES / "trust-root-valid.pem").read_bytes()

    def test_capacity_wins_over_unavailable_wording(self) -> None:
        category = providers.classify_failure(
            "",
            "Selected model is temporarily unavailable because it is at capacity",
        )
        self.assertEqual(category, "transient")

    def test_native_macho_dependencies_rejects_interpreter_wrapper(self) -> None:
        wrapper = self.review.source_root / "rg-wrapper"
        wrapper.write_text('#!/bin/sh\nexec /usr/bin/rg "$@"\n', encoding="utf-8")
        wrapper.chmod(0o755)

        with self.assertRaisesRegex(
            providers.InvalidReviewerExecutable,
            "native Mach-O executable",
        ):
            self.native_macho_dependencies(wrapper, label="ripgrep")

    def test_native_macho_dependencies_accepts_native_magic(self) -> None:
        executable = self.review.source_root / "native-rg"
        executable.write_bytes(b"\xcf\xfa\xed\xfe" + b"\x00" * 32)
        executable.chmod(0o755)

        dependencies = self.native_macho_dependencies(executable, label="ripgrep")

        self.assertEqual(
            dependencies,
            tuple(dict.fromkeys((executable.absolute(), executable.resolve()))),
        )

    def test_claude_digest_accepts_pinned_release_across_helper_architectures(
        self,
    ) -> None:
        executable = self.review.source_root / "claude"
        certificate = self.sample_ca_certificate()
        der, _ = providers._canonical_ca_certificate(
            certificate,
            source="bundled fixture",
        )
        fingerprint = hashlib.sha256(der).digest()
        root_set_digest = hashlib.sha256(fingerprint).hexdigest()
        payload = (
            b"ignored -----BEGIN CERTIFICATE----- fragment"
            + certificate
            + b"ignored -----END CERTIFICATE----- fragment"
        )
        executable.write_bytes(payload)
        expected = hashlib.sha256(payload).hexdigest()

        with (
            mock.patch.object(
                providers,
                "CLAUDE_BUNDLED_ROOT_METADATA_BY_DIGEST",
                {
                    "00" * 32: (1, "11" * 32),
                    expected: (1, root_set_digest),
                },
            ),
            mock.patch.object(providers, "CLAUDE_TRUSTED_HASH_CHUNK_BYTES", 17),
        ):
            actual = self.require_trusted_claude_digest(executable)

        self.assertEqual(actual, frozenset({fingerprint}))

    def test_claude_digest_rejects_unpinned_native_binary(self) -> None:
        executable = self.review.source_root / "claude"
        executable.write_bytes(b"untrusted native fixture")

        with (
            mock.patch.object(
                providers,
                "CLAUDE_BUNDLED_ROOT_METADATA_BY_DIGEST",
                {"00" * 32: (1, "11" * 32)},
            ),
            self.assertRaisesRegex(
                providers.InvalidReviewerExecutable,
                "trusted macOS release digests",
            ),
        ):
            self.require_trusted_claude_digest(executable)

    def test_claude_digest_rejects_mismatched_bundled_root_set(self) -> None:
        executable = self.review.source_root / "claude"
        certificate = self.sample_ca_certificate()
        executable.write_bytes(certificate)
        expected = hashlib.sha256(certificate).hexdigest()

        with (
            mock.patch.object(
                providers,
                "CLAUDE_BUNDLED_ROOT_METADATA_BY_DIGEST",
                {expected: (2, "00" * 32)},
            ),
            self.assertRaisesRegex(
                providers.InvalidReviewerExecutable,
                "bundled root evidence",
            ),
        ):
            self.require_trusted_claude_digest(executable)

    def test_native_executable_inspection_race_is_inconclusive(self) -> None:
        missing = self.review.source_root / "disappeared-claude"

        with self.assertRaisesRegex(
            providers.ClaudeExecutableInspectionInconclusive,
            "cannot inspect Claude Code executable",
        ):
            self.native_macho_dependencies(missing, label="Claude Code")

    def test_claude_keychain_broker_compiles_and_rejects_other_queries(self) -> None:
        if (
            sys.platform != "darwin"
            or not providers.CLAUDE_KEYCHAIN_BROKER_COMPILER.is_file()
        ):
            self.skipTest("the native Claude Keychain broker requires macOS clang")

        prepared = self.prepare_claude_keychain_broker(
            self.review,
            {
                "HOME": str(self.review.container_dir / "claude-home"),
                "PATH": "/usr/bin",
            },
        )
        broker_dir = pathlib.Path(prepared["PATH"].split(providers.os.pathsep)[0])
        broker = broker_dir / "security"

        self.native_macho_dependencies(broker, label="Claude Keychain broker")
        rejected = providers.run((str(broker), "show-keychain-info"))
        self.assertEqual(rejected.returncode, 64)

    @mock.patch.object(
        providers,
        "_ClaudeKeychainCredentialServer",
        side_effect=PermissionError("bind denied"),
    )
    def test_keychain_broker_bind_failure_is_runtime_unavailable(
        self,
        _server: mock.Mock,
    ) -> None:
        with self.assertRaisesRegex(
            providers.ClaudeLoopbackUnavailable,
            "Keychain broker cannot bind loopback",
        ):
            with providers._claude_keychain_credential_server(
                None,
                bytes.fromhex("01" * 32),
            ):
                self.fail("unavailable broker unexpectedly started")

    @mock.patch.object(providers, "_read_claude_keychain_credential")
    @mock.patch.object(
        providers,
        "_claude_keychain_credential_server",
        side_effect=providers.ClaudeLoopbackUnavailable("bind denied"),
    )
    def test_keychain_runtime_zeroes_credential_when_broker_bind_fails(
        self,
        _server: mock.Mock,
        read_credential: mock.Mock,
    ) -> None:
        credential = bytearray(oauth_credential_fixture())
        read_credential.return_value = credential

        with self.assertRaisesRegex(
            providers.ClaudeLoopbackUnavailable,
            "bind denied",
        ):
            with self.claude_keychain_runtime(self.review, {}):
                self.fail("unavailable broker unexpectedly started")

        self.assertEqual(credential, bytearray(len(credential)))

    def test_keychain_broker_thread_failure_closes_server_and_zeroes_credential(
        self,
    ) -> None:
        credential = bytearray(b"fixture-value")
        server = mock.Mock()
        thread = mock.Mock()
        thread.start.side_effect = RuntimeError("thread unavailable")

        with (
            mock.patch.object(
                providers,
                "_ClaudeKeychainCredentialServer",
                return_value=server,
            ),
            mock.patch.object(providers.threading, "Thread", return_value=thread),
            self.assertRaisesRegex(
                providers.ClaudeLoopbackUnavailable,
                "cannot start",
            ),
        ):
            with providers._claude_keychain_credential_server(
                credential,
                bytes.fromhex("01" * 32),
            ):
                self.fail("unavailable broker unexpectedly started")

        server.shutdown.assert_not_called()
        server.server_close.assert_called_once_with()
        thread.join.assert_not_called()
        self.assertEqual(credential, bytearray(len(credential)))

    def test_claude_keychain_broker_serves_one_in_memory_value(self) -> None:
        if (
            sys.platform != "darwin"
            or not providers.CLAUDE_KEYCHAIN_BROKER_COMPILER.is_file()
        ):
            self.skipTest("the native Claude Keychain broker requires macOS clang")
        prepared = self.prepare_claude_keychain_broker(
            self.review,
            {
                "HOME": str(self.review.container_dir / "claude-home"),
                "PATH": "/usr/bin",
            },
        )
        broker_dir = pathlib.Path(prepared["PATH"].split(os.pathsep)[0])
        broker = broker_dir / "security"
        credential = bytearray(b"fixture-value")
        capability = bytes.fromhex("01" * 32)

        try:
            context = providers._claude_keychain_credential_server(
                credential,
                capability,
            )
            with context as port:
                prepared["TMPDIR"] = str(self.review.container_dir / "tmp")
                prepared[providers.CLAUDE_KEYCHAIN_BROKER_PORT_ENV] = str(port)
                prepared[providers.CLAUDE_KEYCHAIN_BROKER_CAPABILITY_ENV] = (
                    capability.hex()
                )
                profile = providers._claude_review_sandbox_profile(
                    pathlib.Path("/bin/true"),
                    self.review,
                    prepared,
                    proxy_port=43210,
                )
                query = (
                    str(providers.CLAUDE_PROBE_SANDBOX),
                    "-p",
                    profile,
                    str(broker),
                    "find-generic-password",
                    "-a",
                    prepared["USER"],
                    "-w",
                    "-s",
                    providers.CLAUDE_KEYCHAIN_SERVICE,
                )
                with socket.create_connection(("127.0.0.1", port)) as unauthorized:
                    unauthorized.sendall(bytes.fromhex("02" * 32))
                    self.assertEqual(unauthorized.recv(1), b"")
                first = providers.run(query, env=prepared)
                second = providers.run(query, env=prepared)
                stdin_update = providers.run(
                    (
                        str(providers.CLAUDE_PROBE_SANDBOX),
                        "-p",
                        profile,
                        str(broker),
                        "-i",
                    ),
                    env=prepared,
                    stdin=b"add-generic-password\n",
                )
                direct_update = providers.run(
                    (
                        str(providers.CLAUDE_PROBE_SANDBOX),
                        "-p",
                        profile,
                        str(broker),
                        "add-generic-password",
                        "-U",
                        "-a",
                        prepared["USER"],
                        "-s",
                        providers.CLAUDE_KEYCHAIN_SERVICE,
                        "-X",
                        "00",
                    ),
                    env=prepared,
                )
        except (PermissionError, providers.ClaudeLoopbackUnavailable):
            self.skipTest("loopback bind is unavailable in the current sandbox")

        self.assertEqual(first.returncode, 0)
        self.assertEqual(first.stdout, b"fixture-value\n")
        self.assertEqual(second.returncode, 44)
        self.assertEqual(stdin_update.returncode, 64)
        self.assertEqual(direct_update.returncode, 64)
        self.assertEqual(credential, bytearray(len(credential)))
        self.assertFalse(providers._ClaudeKeychainCredentialServer.daemon_threads)

    @mock.patch.object(providers, "run_bounded_capture")
    def test_keychain_prefetch_uses_fixed_service_and_account(
        self,
        run_command: mock.Mock,
    ) -> None:
        payload = oauth_credential_fixture()
        completed = common.BoundedCapture(
            argv=(),
            returncode=0,
            stdout=bytearray(payload),
            stderr=bytearray(),
        )
        run_command.return_value = completed

        credential = providers._read_claude_keychain_credential(self.review)

        self.assertEqual(credential, bytearray(payload))
        argv = run_command.call_args.args[0]
        self.assertEqual(argv[0], str(self.claude_keychain_client))
        self.assertEqual(
            argv[1:],
            (
                "find-generic-password",
                "-a",
                providers._claude_keychain_account(),
                "-w",
                "-s",
                "Claude Code-credentials",
            ),
        )
        self.assertEqual(completed.stdout, bytearray(len(payload)))
        self.assertEqual(
            run_command.call_args.kwargs["stdout_limit_bytes"],
            providers.CLAUDE_KEYCHAIN_CREDENTIAL_LIMIT_BYTES,
        )
        self.assertEqual(
            run_command.call_args.kwargs["stderr_limit_bytes"],
            providers.CLAUDE_KEYCHAIN_BROKER_OUTPUT_LIMIT_BYTES,
        )

    @mock.patch.object(providers, "_read_claude_keychain_credential")
    def test_keychain_preflight_rejects_stale_access_token(
        self,
        read_credential: mock.Mock,
    ) -> None:
        credential = bytearray(oauth_credential_fixture(expires_in_seconds=60))
        read_credential.return_value = credential

        with self.assertRaisesRegex(
            providers.ClaudeKeychainCredentialUnavailable,
            "cannot cover the isolated review window",
        ):
            self.require_fresh_claude_keychain_credential(self.review)

        self.assertEqual(credential, bytearray(len(credential)))

    @mock.patch.object(providers, "_read_claude_keychain_credential")
    def test_keychain_preflight_accepts_fresh_access_token(
        self,
        read_credential: mock.Mock,
    ) -> None:
        credential = bytearray(oauth_credential_fixture())
        read_credential.return_value = credential

        self.require_fresh_claude_keychain_credential(self.review)

        self.assertEqual(credential, bytearray(len(credential)))

    @mock.patch.object(providers, "_read_claude_keychain_credential")
    def test_keychain_preflight_budgets_only_the_next_model_attempt(
        self,
        read_credential: mock.Mock,
    ) -> None:
        single_attempt_lifetime = (
            providers.REVIEW_ATTEMPT_TIMEOUT_SECONDS
            + providers.CLAUDE_AUTH_EXPIRY_MARGIN_SECONDS
            + 30
        )
        credential = bytearray(
            oauth_credential_fixture(expires_in_seconds=single_attempt_lifetime)
        )
        read_credential.return_value = credential

        self.require_fresh_claude_keychain_credential(self.review)

        self.assertEqual(credential, bytearray(len(credential)))

        chain_credential = bytearray(
            oauth_credential_fixture(expires_in_seconds=single_attempt_lifetime)
        )
        with self.assertRaisesRegex(
            providers.ClaudeKeychainCredentialUnavailable,
            "cannot cover the isolated review window",
        ):
            providers._validate_fresh_claude_keychain_credential(
                chain_credential,
                attempt_count=len(providers.CLAUDE_MODELS),
            )

    def test_keychain_preflight_rejects_unbounded_integer_expiry(self) -> None:
        credential = bytearray(
            json.dumps(
                {
                    "claudeAiOauth": {
                        "access" + "Token": "fixture-" + "access-value",
                        "refresh" + "Token": "fixture-" + "refresh-value",
                        "expiresAt": 10**1000,
                    }
                }
            ).encode()
        )

        with self.assertRaisesRegex(
            providers.ClaudeKeychainCredentialUnavailable,
            "cannot cover the isolated review window",
        ):
            providers._validate_fresh_claude_keychain_credential(credential)

    @mock.patch.object(providers, "_require_fresh_claude_keychain_credential")
    @mock.patch.object(
        providers,
        "_trusted_claude_ripgrep",
        return_value=pathlib.Path("/bin/echo"),
    )
    @mock.patch.object(
        providers,
        "_claude_review_sandbox_profile",
        return_value="(version 1)(deny default)",
    )
    @mock.patch.object(
        providers,
        "_claude_connect_proxy",
        return_value=contextlib.nullcontext(43210),
    )
    @mock.patch.object(providers, "run")
    def test_stale_local_login_uses_fixed_safe_mode_warmup(
        self,
        run_command: mock.Mock,
        proxy: mock.Mock,
        sandbox_profile: mock.Mock,
        _rg: mock.Mock,
        require_fresh: mock.Mock,
    ) -> None:
        require_fresh.side_effect = (
            providers.ClaudeKeychainCredentialUnavailable("stale"),
            None,
        )
        run_command.return_value = Completed(
            argv=("claude",),
            returncode=0,
            stdout=b"OK",
            stderr=b"",
        )
        home = self.review.container_dir / "claude-home"
        temporary = self.review.container_dir / "tmp"
        home.mkdir(exist_ok=True)
        temporary.mkdir(exist_ok=True)

        self.warm_claude_local_login(
            self.review,
            pathlib.Path("/bin/claude"),
            {
                "HOME": str(home),
                "TMPDIR": str(temporary),
                "PATH": "/untrusted",
            },
        )

        argv = run_command.call_args.args[0]
        self.assertIn("--safe-mode", argv)
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "default")
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertEqual(
            argv[argv.index("--allowedTools") + 1],
            "Read(./__claude_auth_warmup_no_files__)",
        )
        self.assertEqual(
            run_command.call_args.kwargs["stdin"], b"Reply with exactly OK."
        )
        self.assertEqual(
            run_command.call_args.kwargs["timeout_seconds"],
            providers.CLAUDE_AUTH_WARMUP_TIMEOUT_SECONDS,
        )
        self.assertEqual(require_fresh.call_count, 2)
        self.assertEqual(
            proxy.call_args.kwargs["allowed_targets"],
            providers.CLAUDE_AUTH_PROXY_TARGETS,
        )
        self.assertTrue(sandbox_profile.call_args.kwargs["allow_direct_keychain"])
        self.assertFalse(sandbox_profile.call_args.kwargs["allow_workspace_read"])
        self.assertEqual(
            json.loads(
                (self.review.container_dir / "claude-auth-warmup.json").read_text(
                    encoding="utf-8"
                )
            ),
            {
                "category": "other",
                "output_shape": {"json_shape": "invalid-or-non-object"},
                "returncode": 0,
                "stderr_bytes": 0,
                "stdout_bytes": 2,
            },
        )

    @mock.patch.object(providers, "_require_fresh_claude_keychain_credential")
    @mock.patch.object(providers, "_run_claude_auth_warmup")
    def test_transient_login_warmup_failure_is_inconclusive(
        self,
        warmup: mock.Mock,
        require_fresh: mock.Mock,
    ) -> None:
        require_fresh.side_effect = (
            providers.ClaudeKeychainCredentialUnavailable("stale"),
            providers.ClaudeKeychainCredentialUnavailable("still stale"),
        )
        warmup.return_value = Completed(
            argv=("claude",),
            returncode=1,
            stdout=json.dumps(
                {
                    "type": "result",
                    "subtype": "error_during_execution",
                    "is_error": True,
                    "api_error_status": 429,
                }
            ).encode(),
            stderr=b"",
        )

        with self.assertRaisesRegex(
            providers.ClaudeAuthWarmupInconclusive,
            "transient",
        ):
            self.warm_claude_local_login(
                self.review,
                pathlib.Path("/bin/claude"),
                {},
            )

    @mock.patch.object(providers, "_require_fresh_claude_keychain_credential")
    @mock.patch.object(providers, "_run_claude_auth_warmup")
    def test_auth_login_warmup_failure_remains_unavailable(
        self,
        warmup: mock.Mock,
        require_fresh: mock.Mock,
    ) -> None:
        require_fresh.side_effect = (
            providers.ClaudeKeychainCredentialUnavailable("stale"),
            providers.ClaudeKeychainCredentialUnavailable("still stale"),
        )
        warmup.return_value = Completed(
            argv=("claude",),
            returncode=1,
            stdout=json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": True,
                    "result": "Not logged in - Please run /login",
                }
            ).encode(),
            stderr=b"",
        )

        with self.assertRaisesRegex(
            providers.ClaudeKeychainCredentialUnavailable,
            "returncode=1, category=auth",
        ):
            self.warm_claude_local_login(
                self.review,
                pathlib.Path("/bin/claude"),
                {},
            )
        diagnostic = (self.review.container_dir / "claude-auth-warmup.json").read_text(
            encoding="utf-8"
        )
        self.assertEqual(json.loads(diagnostic)["category"], "auth")
        self.assertTrue(
            json.loads(diagnostic)["output_shape"]["result_matches_known_auth_message"]
        )
        self.assertEqual(
            json.loads(diagnostic)["output_shape"]["result_signal_categories"],
            ["auth"],
        )
        self.assertNotIn("Not logged in", diagnostic)

    @mock.patch.object(providers, "_require_fresh_claude_keychain_credential")
    @mock.patch.object(providers, "_run_claude_auth_warmup")
    def test_structural_auth_warmup_signal_is_a_deterministic_auth_block(
        self,
        warmup: mock.Mock,
        require_fresh: mock.Mock,
    ) -> None:
        require_fresh.side_effect = (
            providers.ClaudeKeychainCredentialUnavailable("stale"),
            providers.ClaudeKeychainCredentialUnavailable("still stale"),
        )
        raw_message = "OAuth credential is unavailable; sign in again"
        warmup.return_value = Completed(
            argv=("claude",),
            returncode=1,
            stdout=json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": True,
                    "api_error_status": None,
                    "result": raw_message,
                    "modelUsage": {},
                }
            ).encode(),
            stderr=b"",
        )

        with self.assertRaisesRegex(
            providers.ClaudeKeychainCredentialUnavailable,
            "returncode=1, category=auth",
        ):
            self.warm_claude_local_login(
                self.review,
                pathlib.Path("/bin/claude"),
                {},
            )

        diagnostic = (self.review.container_dir / "claude-auth-warmup.json").read_text(
            encoding="utf-8"
        )
        self.assertEqual(json.loads(diagnostic)["category"], "auth")
        self.assertNotIn(raw_message, diagnostic)

    @mock.patch.object(providers, "_require_fresh_claude_keychain_credential")
    @mock.patch.object(providers, "_run_claude_auth_warmup")
    def test_auth_warmup_omitted_model_usage_is_no_observed_execution(
        self,
        warmup: mock.Mock,
        require_fresh: mock.Mock,
    ) -> None:
        require_fresh.side_effect = (
            providers.ClaudeKeychainCredentialUnavailable("stale"),
            providers.ClaudeKeychainCredentialUnavailable("still stale"),
        )
        warmup.return_value = Completed(
            argv=("claude",),
            returncode=1,
            stdout=json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": True,
                    "result": "OAuth credential is unavailable; sign in again",
                }
            ).encode(),
            stderr=b"",
        )

        with self.assertRaisesRegex(
            providers.ClaudeKeychainCredentialUnavailable,
            "returncode=1, category=auth",
        ):
            self.warm_claude_local_login(
                self.review,
                pathlib.Path("/bin/claude"),
                {},
            )

        diagnostic = json.loads(
            (self.review.container_dir / "claude-auth-warmup.json").read_text(
                encoding="utf-8"
            )
        )
        shape = diagnostic["output_shape"]
        self.assertEqual(diagnostic["category"], "auth")
        self.assertFalse(shape["model_usage_present"])
        self.assertEqual(shape["model_usage_shape"], "missing")
        self.assertIsNone(shape["model_usage_entry_count"])
        self.assertFalse(shape["api_error_status_present"])
        self.assertEqual(shape["api_error_status_shape"], "missing")

    @mock.patch.object(providers, "_require_fresh_claude_keychain_credential")
    @mock.patch.object(providers, "_run_claude_auth_warmup")
    def test_auth_warmup_malformed_fields_remain_inconclusive(
        self,
        warmup: mock.Mock,
        require_fresh: mock.Mock,
    ) -> None:
        for model_usage, api_error_status in (([], None), (None, "invalid")):
            with self.subTest(
                model_usage=model_usage,
                api_error_status=api_error_status,
            ):
                require_fresh.side_effect = (
                    providers.ClaudeKeychainCredentialUnavailable("stale"),
                    providers.ClaudeKeychainCredentialUnavailable("still stale"),
                )
                warmup.return_value = Completed(
                    argv=("claude",),
                    returncode=1,
                    stdout=json.dumps(
                        {
                            "type": "result",
                            "subtype": "success",
                            "is_error": True,
                            "api_error_status": api_error_status,
                            "result": (
                                "OAuth credential is unavailable; sign in again"
                            ),
                            "modelUsage": model_usage,
                        }
                    ).encode(),
                    stderr=b"",
                )

                with self.assertRaisesRegex(
                    providers.ClaudeAuthWarmupInconclusive,
                    "returncode=1, category=other",
                ):
                    self.warm_claude_local_login(
                        self.review,
                        pathlib.Path("/bin/claude"),
                        {},
                    )

                diagnostic = json.loads(
                    (self.review.container_dir / "claude-auth-warmup.json").read_text(
                        encoding="utf-8"
                    )
                )
                shape = diagnostic["output_shape"]
                self.assertEqual(diagnostic["category"], "other")
                self.assertTrue(shape["model_usage_present"])
                self.assertEqual(shape["model_usage_shape"], "invalid")
                self.assertIsNone(shape["model_usage_entry_count"])
                self.assertTrue(shape["api_error_status_present"])
                self.assertEqual(
                    shape["api_error_status_shape"],
                    "null" if api_error_status is None else "invalid",
                )

    @mock.patch.object(providers, "_require_fresh_claude_keychain_credential")
    @mock.patch.object(providers, "_run_claude_auth_warmup")
    def test_mixed_auth_transient_warmup_signal_remains_inconclusive(
        self,
        warmup: mock.Mock,
        require_fresh: mock.Mock,
    ) -> None:
        require_fresh.side_effect = (
            providers.ClaudeKeychainCredentialUnavailable("stale"),
            providers.ClaudeKeychainCredentialUnavailable("still stale"),
        )
        raw_message = "OAuth credential timed out; try again"
        warmup.return_value = Completed(
            argv=("claude",),
            returncode=1,
            stdout=json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": True,
                    "api_error_status": None,
                    "result": raw_message,
                    "modelUsage": {},
                }
            ).encode(),
            stderr=b"",
        )

        with self.assertRaisesRegex(
            providers.ClaudeAuthWarmupInconclusive,
            "returncode=1, category=other",
        ):
            self.warm_claude_local_login(
                self.review,
                pathlib.Path("/bin/claude"),
                {},
            )

        diagnostic = (self.review.container_dir / "claude-auth-warmup.json").read_text(
            encoding="utf-8"
        )
        payload = json.loads(diagnostic)
        self.assertEqual(payload["category"], "other")
        self.assertEqual(
            payload["output_shape"]["result_signal_categories"],
            ["auth", "transient"],
        )
        self.assertNotIn(raw_message, diagnostic)

    @mock.patch.object(providers, "_require_fresh_claude_keychain_credential")
    @mock.patch.object(providers, "_run_claude_auth_warmup")
    def test_mixed_auth_entitlement_warmup_preserves_entitlement(
        self,
        warmup: mock.Mock,
        require_fresh: mock.Mock,
    ) -> None:
        require_fresh.side_effect = (
            providers.ClaudeKeychainCredentialUnavailable("stale"),
            providers.ClaudeKeychainCredentialUnavailable("still stale"),
        )
        raw_message = "OAuth credential is unavailable for this account plan"
        warmup.return_value = Completed(
            argv=("claude",),
            returncode=1,
            stdout=json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": True,
                    "api_error_status": None,
                    "error": {"code": "model_not_enabled"},
                    "result": raw_message,
                    "modelUsage": {},
                }
            ).encode(),
            stderr=b"",
        )

        with self.assertRaisesRegex(
            providers.ClaudeAuthWarmupInconclusive,
            "returncode=1, category=entitlement",
        ):
            self.warm_claude_local_login(
                self.review,
                pathlib.Path("/bin/claude"),
                {},
            )

        diagnostic = (self.review.container_dir / "claude-auth-warmup.json").read_text(
            encoding="utf-8"
        )
        payload = json.loads(diagnostic)
        self.assertEqual(payload["category"], "entitlement")
        self.assertEqual(
            payload["output_shape"]["result_signal_categories"],
            ["auth", "entitlement"],
        )
        self.assertNotIn(raw_message, diagnostic)

    def test_auth_warmup_shape_retains_only_bounded_structural_fields(self) -> None:
        secret = "customer-secret-value"
        shape = providers._claude_auth_warmup_output_shape(
            json.dumps(
                {
                    "type": f"unknown-{secret}",
                    "subtype": f"unknown-{secret}",
                    "is_error": True,
                    "api_error_status": "401 secret",
                    "error": {"message": secret},
                    "result": secret,
                    "modelUsage": {f"model-{secret}": {}},
                    "session_id": secret,
                }
            ).encode()
        )

        retained = json.dumps(shape, sort_keys=True)
        self.assertEqual(shape["json_shape"], "object")
        self.assertEqual(shape["type"], "other")
        self.assertEqual(shape["subtype"], "other")
        self.assertIsNone(shape["api_error_status"])
        self.assertTrue(shape["api_error_status_present"])
        self.assertEqual(
            shape["known_error_fields_present"],
            ["api_error_status", "error"],
        )
        self.assertEqual(shape["model_usage_entry_count"], 1)
        self.assertEqual(shape["result_signal_categories"], [])
        self.assertNotIn(secret, retained)
        self.assertNotIn("session_id", retained)

    @mock.patch.object(
        providers,
        "CLAUDE_KEYCHAIN_BROKER_COMPILER",
        pathlib.Path("/missing/clang"),
    )
    def test_missing_keychain_broker_compiler_is_unavailable(self) -> None:
        with self.assertRaisesRegex(
            providers.ClaudeKeychainBrokerUnavailable,
            "requires /usr/bin/clang",
        ):
            self.prepare_claude_keychain_broker(
                self.review,
                {
                    "HOME": str(self.review.container_dir / "claude-home"),
                    "PATH": "/usr/bin",
                },
            )

    @mock.patch.object(
        providers,
        "CLAUDE_KEYCHAIN_BROKER_COMPILER",
        pathlib.Path("/usr/bin/true"),
    )
    @mock.patch.object(providers, "run")
    def test_keychain_broker_compile_failure_is_unavailable(
        self,
        run_command: mock.Mock,
    ) -> None:
        run_command.return_value = Completed(
            argv=("clang",),
            returncode=1,
            stdout=b"",
            stderr=b"toolchain unavailable",
        )

        with self.assertRaisesRegex(
            providers.ClaudeKeychainBrokerUnavailable,
            "toolchain unavailable",
        ):
            self.prepare_claude_keychain_broker(
                self.review,
                {
                    "HOME": str(self.review.container_dir / "claude-home"),
                    "PATH": "/usr/bin",
                },
            )

    @mock.patch.object(providers, "run")
    def test_claude_api_key_skips_keychain_broker(self, run_command: mock.Mock) -> None:
        env = {
            "ANTHROPIC_API_KEY": "test-api-key",
            "HOME": str(self.review.container_dir / "claude-home"),
            "PATH": "/usr/bin",
        }

        self.assertEqual(
            self.prepare_claude_keychain_broker(self.review, env),
            env,
        )
        run_command.assert_not_called()

    def test_model_match_is_normalized_but_not_prefix_based(self) -> None:
        self.assertTrue(providers._model_matches("claude-opus-4-8", "claude-opus-4.8"))
        self.assertFalse(providers._model_matches("gpt-5.5", "gpt-5.5-mini"))
        self.assertFalse(providers._model_matches("gpt-5.5", "gpt-5.5-codex"))

    def test_entitlement_is_fallback_eligible(self) -> None:
        self.assertEqual(
            providers.classify_failure("", "Model is not available for your account"),
            "entitlement",
        )
        self.assertEqual(
            providers.classify_failure(
                "",
                "Your account does not have access to this model",
            ),
            "entitlement",
        )

    def test_structured_model_access_code_is_fallback_eligible(self) -> None:
        stdout = json.dumps(
            {
                "type": "error",
                "error": {
                    "code": "model_access_denied",
                    "message": "request rejected",
                },
            }
        )
        self.assertEqual(providers.classify_failure(stdout, ""), "entitlement")

    def test_ambiguous_model_not_found_without_access_context_does_not_fallback(
        self,
    ) -> None:
        stdout = json.dumps(
            {
                "type": "error",
                "error": {
                    "type": "model_not_found",
                    "message": "requested model identifier does not exist",
                },
            }
        )
        self.assertEqual(providers.classify_failure(stdout, ""), "other")
        self.assertEqual(
            providers.classify_failure(
                "",
                "This model is not supported with your ChatGPT account",
            ),
            "entitlement",
        )

    def test_auth_is_not_entitlement(self) -> None:
        self.assertEqual(
            providers.classify_failure("", "Authentication failed: invalid token"),
            "auth",
        )

    def test_claude_error_result_can_report_auth_without_error_payload(self) -> None:
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "result": "Not logged in - Please run /login",
            }
        )

        self.assertEqual(providers.classify_failure(stdout, ""), "auth")

    def test_claude_error_result_ignores_empty_error_payloads_for_auth(self) -> None:
        for key, value in (
            ("error", None),
            ("errors", []),
            ("message", ""),
        ):
            with self.subTest(key=key):
                stdout = json.dumps(
                    {
                        "type": "result",
                        "subtype": "error_during_execution",
                        "is_error": True,
                        "result": "Not logged in - Please run /login",
                        key: value,
                    }
                )

                self.assertEqual(providers.classify_failure(stdout, ""), "auth")

    def test_claude_partial_error_result_cannot_trigger_model_fallback(self) -> None:
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "result": (
                    "Partial review text says a model is not available for your "
                    "account and mentions a timeout."
                ),
                "modelUsage": {"claude-opus-4-8": {}},
            }
        )

        self.assertEqual(providers.classify_failure(stdout, ""), "other")

    def test_claude_partial_error_result_cannot_trigger_auth_block(self) -> None:
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "result": (
                    "Partial findings discuss unauthorized users and invalid token "
                    "handling."
                ),
                "modelUsage": {"claude-opus-4-8": {}},
            }
        )

        self.assertEqual(providers.classify_failure(stdout, ""), "other")

    def test_auth_wins_over_entitlement_wording(self) -> None:
        self.assertEqual(
            providers.classify_failure(
                "",
                "Unauthorized: model is not available for your account",
            ),
            "auth",
        )

    def test_repository_text_in_structured_tool_output_cannot_trigger_fallback(
        self,
    ) -> None:
        stdout = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "aggregated_output": "not available for your account; timeout",
                },
            }
        )
        self.assertEqual(providers.classify_failure(stdout, "review failed"), "other")

    def test_nested_tool_error_data_cannot_trigger_fallback(self) -> None:
        stdout = json.dumps(
            {
                "type": "item.completed",
                "data": {
                    "error": {
                        "message": "Model is not available for your account; timeout"
                    }
                },
            }
        )
        self.assertEqual(providers.classify_failure(stdout, "review failed"), "other")

    def test_structured_error_event_can_trigger_entitlement_fallback(self) -> None:
        stdout = json.dumps(
            {
                "type": "turn.failed",
                "error": {"message": "Model is not available for your account"},
            }
        )
        self.assertEqual(providers.classify_failure(stdout, ""), "entitlement")

    def test_strict_jsonl_error_event_can_trigger_entitlement_fallback(self) -> None:
        stdout = "\n".join(
            (
                json.dumps({"type": "thread.started", "thread_id": "fixture"}),
                json.dumps(
                    {
                        "type": "turn.failed",
                        "error": {"message": "Model is not available for your account"},
                    }
                ),
            )
        )

        self.assertEqual(providers.classify_failure(stdout, ""), "entitlement")

    def test_structured_api_error_event_can_trigger_entitlement_fallback(self) -> None:
        stdout = json.dumps(
            {
                "type": "api_error",
                "message": "Model is not available for your account",
            }
        )
        self.assertEqual(providers.classify_failure(stdout, ""), "entitlement")

    def test_claude_errors_field_can_trigger_entitlement_fallback(self) -> None:
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "errors": ["Model is not available for your account"],
            }
        )
        self.assertEqual(providers.classify_failure(stdout, ""), "entitlement")

    def test_claude_api_error_status_can_trigger_transient_classification(self) -> None:
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "api_error_status": 429,
            }
        )
        self.assertEqual(providers.classify_failure(stdout, ""), "transient")

    def test_claude_partial_result_cannot_override_entitlement_error(self) -> None:
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "errors": ["Model is not available for your account"],
                "result": "partial review text mentioning timeout",
            }
        )
        self.assertEqual(providers.classify_failure(stdout, ""), "entitlement")

    def test_claude_partial_result_cannot_override_transient_error(self) -> None:
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "api_error_status": 429,
                "result": "model is not available for your account",
            }
        )
        self.assertEqual(providers.classify_failure(stdout, ""), "transient")

    def test_structured_error_result_cannot_be_accepted_as_final_text(self) -> None:
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "result": "partial findings",
                "modelUsage": {"claude-opus-4-8": {}},
            }
        ).encode()
        final_text, effective_model = providers._parse_claude_output(stdout)
        self.assertIsNone(final_text)
        self.assertEqual(effective_model, "claude-opus-4-8")

    def test_requested_model_wins_over_auxiliary_claude_model_usage(self) -> None:
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "No findings.",
                "modelUsage": {
                    "claude-haiku-4-5-20251001": {},
                    "claude-opus-4-8": {},
                },
            }
        ).encode()
        final_text, effective_model = providers._parse_claude_output(
            stdout, requested_model="claude-opus-4-8"
        )
        self.assertEqual(final_text, "No findings.")
        self.assertEqual(effective_model, "claude-opus-4-8")

    def test_claude_retains_success_shape_with_malformed_model_usage_entry(
        self,
    ) -> None:
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "No findings.",
                "modelUsage": {"claude-opus-4-8": None},
            }
        ).encode()

        self.assertEqual(
            providers._parse_claude_output(stdout),
            ("No findings.", None),
        )

    def test_claude_attempt_blocks_unverified_success_model_usage(self) -> None:
        cases = (
            ("missing", {}),
            ("malformed", {"modelUsage": {"claude-opus-4-8": None}}),
        )
        with (
            mock.patch.object(
                providers,
                "_resolve_validated_claude_executable",
                side_effect=lambda *, review, env: (
                    pathlib.Path("/fixture/claude"),
                    dict(env),
                    frozenset(),
                ),
            ),
            mock.patch.object(
                providers,
                "_with_claude_review_tool_path",
                side_effect=lambda _review, env: dict(env),
            ),
            mock.patch.object(
                providers,
                "_prepare_claude_tls_environment",
                side_effect=lambda _review, env, **_kwargs: dict(env),
            ),
            mock.patch.object(
                providers,
                "_claude_connect_proxy",
                return_value=contextlib.nullcontext(43210),
            ),
            mock.patch.object(
                providers,
                "_claude_review_sandbox_profile",
                return_value="(version 1)(deny default)",
            ),
        ):
            for index, (label, extra) in enumerate(cases, start=1):
                with self.subTest(label=label):
                    payload = {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": "No findings.",
                        **extra,
                    }
                    with mock.patch.object(
                        providers,
                        "run",
                        return_value=Completed(
                            argv=("claude",),
                            returncode=0,
                            stdout=json.dumps(payload).encode(),
                            stderr=b"",
                        ),
                    ):
                        attempt = providers._claude_attempt(
                            review=self.review,
                            model="claude-opus-4-8",
                            index=index,
                            env={"ANTHROPIC_API_KEY": "fixture-key"},
                        )

                    outcome = providers._finish(self.review, [attempt], None)
                    self.assertEqual(attempt.category, "runtime-unverified")
                    self.assertEqual(attempt.returncode, 65)
                    self.assertIsNone(attempt.effective_model)
                    self.assertIsNone(attempt.final_text)
                    self.assertEqual(outcome.returncode, 1)
                    self.assertNotEqual(outcome.returncode, 75)
                    self.assertFalse((self.review.container_dir / "final.txt").exists())

    def test_claude_rejects_success_with_nonempty_errors(self) -> None:
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "No findings.",
                "errors": [{"message": "contradictory failure"}],
                "modelUsage": {"claude-opus-4-8": {}},
            }
        ).encode()

        self.assertEqual(
            providers._parse_claude_output(stdout),
            (None, "claude-opus-4-8"),
        )

    def test_claude_rejects_unknown_or_malformed_error_payloads(self) -> None:
        for field, value in (
            ("errors", [{"exception": "failed"}]),
            ("api_error_status", {"code": 500}),
        ):
            with self.subTest(field=field):
                payload = {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "No findings.",
                    "modelUsage": {"claude-opus-4-8": {}},
                    field: value,
                }

                self.assertEqual(
                    providers._parse_claude_output(json.dumps(payload).encode()),
                    (None, "claude-opus-4-8"),
                )

    def test_nonterminal_claude_payload_cannot_supply_final_text(self) -> None:
        stdout = json.dumps(
            {
                "type": "progress",
                "data": {
                    "message": "LGTM",
                    "model": "claude-opus-4-8",
                },
            }
        ).encode()

        self.assertEqual(providers._parse_claude_output(stdout), (None, None))

    def test_claude_rejects_non_json_prefix_before_success_object(self) -> None:
        stdout = (
            b"warning: degraded output\n"
            + json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "No findings.",
                    "modelUsage": {"claude-opus-4-8": {}},
                }
            ).encode()
        )

        self.assertEqual(providers._parse_claude_output(stdout), (None, None))

    def test_claude_rejects_unicode_separator_prefix_before_success(self) -> None:
        stdout = (
            "\u2028"
            + json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "No findings.",
                    "modelUsage": {"claude-opus-4-8": {}},
                }
            )
        ).encode()

        self.assertEqual(providers._parse_claude_output(stdout), (None, None))

    def test_claude_rejects_nonstandard_json_constant(self) -> None:
        stdout = (
            b'{"type":"result","subtype":"success","is_error":false,'
            b'"result":"No findings.","modelUsage":{"claude-opus-4-8":{}},'
            b'"metric":NaN}'
        )

        self.assertEqual(providers._parse_claude_output(stdout), (None, None))

    def test_claude_rejects_duplicate_json_object_key(self) -> None:
        stdout = (
            b'{"type":"result","subtype":"success","is_error":true,'
            b'"is_error":false,"result":"No findings.",'
            b'"modelUsage":{"claude-opus-4-8":{}}}'
        )

        self.assertEqual(providers._parse_claude_output(stdout), (None, None))

    def test_duplicate_error_json_cannot_trigger_model_fallback(self) -> None:
        stdout = (
            b'{"type":"result","subtype":"error_during_execution",'
            b'"is_error":true,"error":{"message":"request failed"},'
            b'"error":{"message":"Model is not available for your account"}}'
        )

        self.assertEqual(providers._parse_claude_output(stdout), (None, None))
        self.assertEqual(providers.classify_failure(stdout, ""), "other")

    def test_claude_preserves_unicode_separator_at_result_edges(self) -> None:
        result = "\u2028No findings.\u2029"
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": result,
                "modelUsage": {"claude-opus-4-8": {}},
            },
            ensure_ascii=False,
        ).encode()

        self.assertEqual(
            providers._parse_claude_output(stdout),
            (result, "claude-opus-4-8"),
        )

    @mock.patch.object(providers, "child_environment", return_value={})
    @mock.patch.object(providers, "_codex_attempt")
    def test_codex_falls_back_from_56_to_55_only_on_entitlement(
        self,
        codex_attempt: mock.Mock,
        _environment: mock.Mock,
    ) -> None:
        codex_attempt.side_effect = (
            self.attempt("codex", "gpt-5.6-sol", "entitlement"),
            self.attempt("codex", "gpt-5.5", "success", final_text="No findings."),
        )
        outcome = providers.run_review(
            review=self.review,
            reviewer="codex",
        )
        self.assertEqual(outcome.returncode, 0)
        self.assertEqual(
            [item.requested_model for item in outcome.attempts],
            list(providers.CODEX_MODELS),
        )
        self.assertEqual(
            _environment.call_args.kwargs["passthrough_keys"],
            providers.CODEX_ENV_KEYS,
        )

    def test_model_chain_persists_each_completed_attempt(self) -> None:
        first = self.attempt("codex", "gpt-5.6-sol", "entitlement")
        runner = mock.Mock(side_effect=(first, RuntimeError("interrupted fallback")))
        attempts: list[providers.Attempt] = []
        with self.assertRaisesRegex(RuntimeError, "interrupted fallback"):
            providers._run_model_chain(
                review=self.review,
                models=providers.CODEX_MODELS,
                runner=runner,
                runtime="codex",
                requested_effort=providers.CODEX_REASONING_EFFORT,
                env={},
                attempts=attempts,
            )

        persisted = json.loads(
            (self.review.container_dir / "attempts.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(persisted), 1)
        self.assertEqual(persisted[0]["requested_model"], "gpt-5.6-sol")
        self.assertEqual(persisted[0]["category"], "entitlement")
        self.assertNotIn("final_text", persisted[0])
        self.assertFalse(persisted[0]["final_available"])

    def test_model_chain_does_not_persist_successful_final_text(self) -> None:
        final_text = "sensitive terminal artifact"
        runner = mock.Mock(
            return_value=self.attempt(
                "codex",
                "gpt-5.6-sol",
                "success",
                final_text=final_text,
            )
        )
        attempts: list[providers.Attempt] = []

        category, returned_text = providers._run_model_chain(
            review=self.review,
            models=("gpt-5.6-sol",),
            runner=runner,
            runtime="codex",
            requested_effort=providers.CODEX_REASONING_EFFORT,
            env={},
            attempts=attempts,
        )

        self.assertEqual(category, "success")
        self.assertEqual(returned_text, final_text)
        persisted = json.loads(
            (self.review.container_dir / "attempts.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("final_text", persisted[0])
        self.assertTrue(persisted[0]["final_available"])
        self.assertNotIn(
            final_text,
            (self.review.container_dir / "attempts.json").read_text(encoding="utf-8"),
        )

    def test_finish_preserves_unicode_separator_at_result_edges(self) -> None:
        final_text = "\u2028No findings.\u2029"

        outcome = providers._finish(self.review, [], final_text)

        self.assertEqual(outcome.final_text, final_text)
        self.assertEqual(
            (self.review.container_dir / "final.txt").read_text(encoding="utf-8"),
            final_text + "\n",
        )

    def test_finish_marks_missing_unclassified_artifact_inconclusive(self) -> None:
        attempt = self.attempt(
            "claude",
            providers.CLAUDE_MODELS[0],
            "other",
        )

        outcome = providers._finish(self.review, [attempt], None)

        self.assertEqual(outcome.returncode, 75)
        self.assertIsNone(outcome.final_text)

    @mock.patch.object(providers, "child_environment", return_value={})
    @mock.patch.object(providers, "_codex_attempt")
    def test_codex_capacity_does_not_downgrade(
        self,
        codex_attempt: mock.Mock,
        _environment: mock.Mock,
    ) -> None:
        codex_attempt.return_value = self.attempt("codex", "gpt-5.6-sol", "transient")
        outcome = providers.run_review(
            review=self.review,
            reviewer="codex",
        )
        self.assertEqual(outcome.returncode, 75)
        self.assertEqual(codex_attempt.call_count, 1)

    @mock.patch.object(providers, "child_environment", return_value={})
    @mock.patch.object(
        providers,
        "_codex_attempt",
        side_effect=providers.ReviewTimeoutError("review timed out"),
    )
    def test_codex_attempt_timeout_is_inconclusive(
        self,
        codex_attempt: mock.Mock,
        _environment: mock.Mock,
    ) -> None:
        outcome = providers.run_review(review=self.review, reviewer="codex")

        self.assertEqual(outcome.returncode, 75)
        codex_attempt.assert_called_once()
        self.assertEqual(len(outcome.attempts), 1)
        self.assertEqual(outcome.attempts[0].runtime, "codex")
        self.assertEqual(outcome.attempts[0].requested_model, "gpt-5.6-sol")
        self.assertEqual(outcome.attempts[0].category, "inconclusive")
        self.assertTrue(pathlib.Path(outcome.attempts[0].stderr_path).is_file())
        self.assertIn(
            "inconclusive",
            (self.review.container_dir / "runner-error.txt").read_text(
                encoding="utf-8"
            ),
        )

    def test_missing_native_claude_runtime_stops_the_lane(self) -> None:
        with (
            mock.patch.object(
                providers,
                "child_environment",
                return_value={"ANTHROPIC_API_KEY": "fixture-key"},
            ),
            mock.patch.object(
                providers,
                "_resolve_validated_claude_executable",
                side_effect=providers.ClaudeExecutableUnavailable(
                    "native runtime unavailable"
                ),
            ),
            mock.patch.object(providers, "_claude_attempt") as claude_attempt,
        ):
            outcome = providers.run_review(
                review=self.review,
                reviewer="claude",
                egress_consent="explicit-claude-review",
            )

        self.assertEqual(outcome.returncode, 2)
        self.assertEqual(outcome.attempts, ())
        claude_attempt.assert_not_called()
        self.assertIn(
            "no alternate provider is configured",
            (self.review.container_dir / "runner-error.txt").read_text(
                encoding="utf-8"
            ),
        )

    def test_malformed_trust_policy_uses_blocked_runner_artifacts(self) -> None:
        claude_env = {"ANTHROPIC_API_KEY": "fixture-key"}
        malformed = plistlib.dumps(
            {
                "trustVersion": 1,
                "trustList": {"A" * 40: {"trustSettings": "invalid"}},
            }
        )

        def reject_malformed(
            _review: ReviewWorkspace,
            **_kwargs: object,
        ) -> bytes:
            providers._classify_trust_fingerprints(malformed, domain="user")
            self.fail("malformed trust settings unexpectedly passed")

        with (
            mock.patch.object(
                providers,
                "child_environment",
                return_value=claude_env,
            ),
            mock.patch.object(
                providers,
                "_resolve_validated_claude_executable",
                return_value=(
                    pathlib.Path("/fixture/claude"),
                    claude_env,
                    frozenset(),
                ),
            ),
            mock.patch.object(
                providers,
                "_with_claude_review_tool_path",
                return_value=claude_env,
            ),
            mock.patch.object(
                providers,
                "_preflight_claude_trust_policy",
                side_effect=reject_malformed,
            ),
        ):
            outcome = providers.run_review(
                review=self.review,
                reviewer="claude",
                egress_consent="triple-review",
            )

        blocked = json.loads(
            (self.review.container_dir / "claude-blocked.json").read_text(
                encoding="utf-8"
            )
        )
        runner_error = (self.review.container_dir / "runner-error.txt").read_text(
            encoding="utf-8"
        )
        self.assertEqual(outcome.returncode, 2)
        self.assertEqual(blocked["reason_category"], "trust-policy-unrepresentable")
        self.assertEqual(blocked["status"], "blocked")
        self.assertFalse(
            (self.review.container_dir / "claude-unavailable.json").exists()
        )
        self.assertFalse((self.review.container_dir / "claude-skip.txt").exists())
        self.assertIn("malformed or unsupported trust settings", runner_error)
        self.assertNotIn("executable validation failed", runner_error)

    def test_trust_tool_failure_uses_secure_runtime_artifacts(self) -> None:
        claude_env = {"ANTHROPIC_API_KEY": "fixture-key"}
        with (
            mock.patch.object(
                providers,
                "child_environment",
                return_value=claude_env,
            ),
            mock.patch.object(
                providers,
                "_resolve_validated_claude_executable",
                return_value=(
                    pathlib.Path("/fixture/claude"),
                    claude_env,
                    frozenset(),
                ),
            ),
            mock.patch.object(
                providers,
                "_with_claude_review_tool_path",
                return_value=claude_env,
            ),
            mock.patch.object(
                providers,
                "_preflight_claude_trust_policy",
                side_effect=providers.ClaudeTrustToolUnavailable(
                    "fixture trust tool failure"
                ),
            ),
        ):
            outcome = providers.run_review(
                review=self.review,
                reviewer="claude",
                egress_consent="triple-review",
            )

        unavailable = json.loads(
            (self.review.container_dir / "claude-unavailable.json").read_text(
                encoding="utf-8"
            )
        )
        runner_error = (self.review.container_dir / "runner-error.txt").read_text(
            encoding="utf-8"
        )
        self.assertEqual(outcome.returncode, 2)
        self.assertEqual(unavailable["reason_category"], "secure-runtime-unavailable")
        self.assertIn("no alternate provider is configured", runner_error)
        self.assertNotIn("executable validation failed", runner_error)

    def test_certificate_export_status_failure_uses_secure_runtime_artifacts(
        self,
    ) -> None:
        claude_env = {"ANTHROPIC_API_KEY": "fixture-key"}
        settings = plistlib.dumps(
            {
                "trustVersion": 1,
                "trustList": {"A" * 40: {"trustSettings": []}},
            }
        )
        empty_settings = plistlib.dumps({"trustVersion": 1, "trustList": {}})

        def fail_certificate_status(
            argv: tuple[str, ...],
            **_kwargs: object,
        ) -> common.BoundedCapture:
            if argv[1:3] == ("help", "trust-settings-export"):
                return self.trust_export_help_capture(argv)
            if argv[1] == "trust-settings-export":
                pathlib.Path(argv[-1]).write_bytes(
                    settings
                    if "-d" not in argv and "-s" not in argv
                    else empty_settings
                )
                return common.BoundedCapture(
                    argv=argv,
                    returncode=0,
                    stdout=bytearray(),
                    stderr=bytearray(),
                )
            return common.BoundedCapture(
                argv=argv,
                returncode=1,
                stdout=bytearray(),
                stderr=bytearray(b"private certificate export detail"),
            )

        with (
            mock.patch.object(
                providers,
                "child_environment",
                return_value=claude_env,
            ),
            mock.patch.object(
                providers,
                "_resolve_validated_claude_executable",
                return_value=(
                    pathlib.Path("/fixture/claude"),
                    claude_env,
                    frozenset(),
                ),
            ),
            mock.patch.object(
                providers,
                "_with_claude_review_tool_path",
                return_value=claude_env,
            ),
            mock.patch.object(
                providers,
                "_preflight_claude_trust_policy",
                side_effect=self.preflight_claude_trust_policy,
            ),
            mock.patch.object(
                providers,
                "_read_claude_trust_certificates",
                side_effect=self.read_claude_trust_certificates,
            ),
            mock.patch.object(
                providers,
                "run_bounded_capture",
                side_effect=fail_certificate_status,
            ),
        ):
            outcome = providers.run_review(
                review=self.review,
                reviewer="claude",
                egress_consent="triple-review",
            )

        evidence_text = (
            self.review.container_dir / providers.CLAUDE_TRUST_POLICY_EVIDENCE_NAME
        ).read_text(encoding="utf-8")
        evidence = json.loads(evidence_text)
        unavailable = json.loads(
            (self.review.container_dir / "claude-unavailable.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(outcome.returncode, 2)
        self.assertEqual(evidence["status"], "unavailable")
        self.assertEqual(evidence["additional_root_resolution"], "unavailable")
        self.assertEqual(unavailable["reason_category"], "secure-runtime-unavailable")
        self.assertNotIn("private certificate export detail", evidence_text)
        self.assertNotIn(
            "private certificate export detail",
            (self.review.container_dir / "runner-error.txt").read_text(
                encoding="utf-8"
            ),
        )

    def test_native_claude_auth_failure_does_not_change_provider(self) -> None:
        claude_env = {"ANTHROPIC_API_KEY": "fixture-key"}
        with (
            mock.patch.object(
                providers,
                "child_environment",
                return_value=claude_env,
            ),
            mock.patch.object(
                providers,
                "_resolve_validated_claude_executable",
                return_value=(
                    pathlib.Path("/fixture/claude"),
                    claude_env,
                    frozenset(),
                ),
            ),
            mock.patch.object(
                providers,
                "_with_claude_review_tool_path",
                return_value=claude_env,
            ),
            mock.patch.object(
                providers,
                "_prepare_claude_tls_environment",
                return_value=claude_env,
            ),
            mock.patch.object(
                providers,
                "_claude_attempt",
                return_value=self.attempt(
                    "claude",
                    providers.CLAUDE_MODELS[0],
                    "auth",
                ),
            ) as claude_attempt,
        ):
            outcome = providers.run_review(
                review=self.review,
                reviewer="claude",
                egress_consent="explicit-claude-review",
            )

        self.assertEqual(outcome.returncode, 1)
        self.assertEqual(len(outcome.attempts), 1)
        self.assertEqual(outcome.attempts[0].runtime, "claude")
        claude_attempt.assert_called_once()
        self.assertEqual(
            json.loads(
                (self.review.container_dir / "claude-blocked.json").read_text(
                    encoding="utf-8"
                )
            ),
            {
                "reason_category": "authentication",
                "runtime": "claude",
                "status": "blocked",
            },
        )
        self.assertFalse((self.review.container_dir / "claude-skip.txt").exists())
        self.assertFalse(
            (self.review.container_dir / "claude-unavailable.json").exists()
        )

    def test_initial_warmup_credential_failure_writes_authentication_block(
        self,
    ) -> None:
        self.warmup.side_effect = providers.ClaudeKeychainCredentialUnavailable(
            "fixture credential failure"
        )
        with (
            mock.patch.object(providers, "child_environment", return_value={}),
            mock.patch.object(
                providers,
                "_resolve_validated_claude_executable",
                side_effect=lambda *, review, env: (
                    pathlib.Path("/fixture/claude"),
                    dict(env),
                    frozenset(),
                ),
            ),
            mock.patch.object(
                providers,
                "_with_claude_review_tool_path",
                side_effect=lambda _review, env: dict(env),
            ),
            mock.patch.object(
                providers,
                "_prepare_claude_tls_environment",
                side_effect=lambda _review, env, **_kwargs: dict(env),
            ) as prepare_tls,
            mock.patch.object(providers, "_claude_attempt") as claude_attempt,
        ):
            outcome = providers.run_review(
                review=self.review,
                reviewer="claude",
                egress_consent="triple-review",
            )

        blocked = json.loads(
            (self.review.container_dir / "claude-blocked.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(outcome.returncode, 2)
        self.assertEqual(outcome.attempts, ())
        self.assertEqual(blocked["reason_category"], "authentication")
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(
            json.loads(
                (self.review.container_dir / "attempts.json").read_text(
                    encoding="utf-8"
                )
            ),
            [],
        )
        self.assertIn(
            "authentication is unavailable",
            (self.review.container_dir / "runner-error.txt").read_text(
                encoding="utf-8"
            ),
        )
        self.assertFalse((self.review.container_dir / "claude-skip.txt").exists())
        self.assertFalse(
            (self.review.container_dir / "claude-unavailable.json").exists()
        )
        self.trust_preflight.assert_called_once_with(
            self.review,
            bundled_root_sha256_fingerprints=frozenset(),
        )
        prepare_tls.assert_called_once()
        claude_attempt.assert_not_called()

    def test_entitlement_fallback_credential_failure_writes_authentication_block(
        self,
    ) -> None:
        trust_materials: list[providers.ClaudeTrustMaterial] = []
        runtime_calls: list[str] = []

        def fresh_trust_material(
            _review: ReviewWorkspace,
            **_kwargs: object,
        ) -> providers.ClaudeTrustMaterial:
            material = self.pending_claude_trust_material()
            trust_materials.append(material)
            return material

        @contextlib.contextmanager
        def keychain_runtime(
            _review: ReviewWorkspace,
            env: dict[str, str],
        ):
            runtime_calls.append("enter")
            if len(runtime_calls) == 2:
                raise providers.ClaudeKeychainCredentialUnavailable(
                    "fixture fallback credential failure"
                )
            yield dict(env)

        entitlement = Completed(
            argv=("claude",),
            returncode=1,
            stdout=json.dumps(
                {
                    "type": "result",
                    "subtype": "error_during_execution",
                    "is_error": True,
                    "error": {"code": "model_not_enabled"},
                }
            ).encode(),
            stderr=b"",
        )
        self.trust_preflight.side_effect = fresh_trust_material
        with (
            mock.patch.object(providers, "child_environment", return_value={}),
            mock.patch.object(
                providers,
                "_resolve_validated_claude_executable",
                side_effect=lambda *, review, env: (
                    pathlib.Path("/fixture/claude"),
                    dict(env),
                    frozenset(),
                ),
            ),
            mock.patch.object(
                providers,
                "_with_claude_review_tool_path",
                side_effect=lambda _review, env: dict(env),
            ),
            mock.patch.object(
                providers,
                "_prepare_claude_tls_environment",
                wraps=providers._prepare_claude_tls_environment,
            ) as prepare_tls,
            mock.patch.object(
                providers,
                "_claude_keychain_runtime",
                side_effect=keychain_runtime,
            ),
            mock.patch.object(
                providers,
                "_claude_connect_proxy",
                return_value=contextlib.nullcontext(43210),
            ),
            mock.patch.object(
                providers,
                "_claude_review_sandbox_profile",
                return_value="(version 1)(deny default)",
            ),
            mock.patch.object(
                providers,
                "run",
                side_effect=self.logged_run_side_effect(entitlement),
            ) as run_command,
        ):
            outcome = providers.run_review(
                review=self.review,
                reviewer="claude",
                egress_consent="triple-review",
            )

        blocked = json.loads(
            (self.review.container_dir / "claude-blocked.json").read_text(
                encoding="utf-8"
            )
        )
        persisted_attempts = json.loads(
            (self.review.container_dir / "attempts.json").read_text(encoding="utf-8")
        )
        self.assertEqual(outcome.returncode, 2)
        self.assertEqual(len(outcome.attempts), 1)
        self.assertEqual(outcome.attempts[0].category, "entitlement")
        self.assertIsNone(outcome.attempts[0].effective_model)
        self.assertEqual(persisted_attempts[0]["category"], "entitlement")
        self.assertEqual(blocked["reason_category"], "authentication")
        self.assertEqual(blocked["status"], "blocked")
        self.assertFalse((self.review.container_dir / "claude-skip.txt").exists())
        self.assertFalse(
            (self.review.container_dir / "claude-unavailable.json").exists()
        )
        self.assertEqual(runtime_calls, ["enter", "enter"])
        self.assertEqual(self.trust_preflight.call_count, 3)
        self.assertEqual(prepare_tls.call_count, 3)
        self.assertEqual(run_command.call_count, 1)
        self.assertEqual(len(trust_materials), 3)
        self.assertEqual(
            len({str(material.evidence["generation"]) for material in trust_materials}),
            3,
        )

    def test_entitlement_fallback_probe_sandbox_unavailable_writes_skip(
        self,
    ) -> None:
        claude_env = {"ANTHROPIC_API_KEY": "fixture-key"}
        resolve_calls: list[str] = []

        def resolve_claude(
            *,
            review: ReviewWorkspace,
            env: dict[str, str],
        ) -> tuple[pathlib.Path, dict[str, str], frozenset[bytes]]:
            resolve_calls.append("resolve")
            if len(resolve_calls) == 3:
                raise providers.ClaudeProbeSandboxUnavailable(
                    "fixture fallback probe sandbox unavailable"
                )
            return pathlib.Path("/fixture/claude"), dict(env), frozenset()

        entitlement = Completed(
            argv=("claude",),
            returncode=1,
            stdout=json.dumps(
                {
                    "type": "result",
                    "subtype": "error_during_execution",
                    "is_error": True,
                    "error": {"code": "model_not_enabled"},
                }
            ).encode(),
            stderr=b"",
        )
        with (
            mock.patch.object(
                providers,
                "child_environment",
                return_value=claude_env,
            ),
            mock.patch.object(
                providers,
                "_resolve_validated_claude_executable",
                side_effect=resolve_claude,
            ),
            mock.patch.object(
                providers,
                "_with_claude_review_tool_path",
                side_effect=lambda _review, env: dict(env),
            ),
            mock.patch.object(
                providers,
                "_prepare_claude_tls_environment",
                side_effect=lambda _review, env, **_kwargs: dict(env),
            ),
            mock.patch.object(
                providers,
                "_claude_connect_proxy",
                return_value=contextlib.nullcontext(43210),
            ),
            mock.patch.object(
                providers,
                "_claude_review_sandbox_profile",
                return_value="(version 1)(deny default)",
            ),
            mock.patch.object(
                providers,
                "run",
                side_effect=self.logged_run_side_effect(entitlement),
            ) as run_command,
        ):
            outcome = providers.run_review(
                review=self.review,
                reviewer="claude",
                egress_consent="triple-review",
            )

        self.assertEqual(outcome.returncode, 2)
        self.assertEqual(len(outcome.attempts), 1)
        self.assertEqual(outcome.attempts[0].category, "entitlement")
        self.assertEqual(resolve_calls, ["resolve"] * 3)
        self.assertEqual(run_command.call_count, 1)
        self.assertTrue((self.review.container_dir / "claude-skip.txt").is_file())
        self.assertIn(
            "secure runtime became unavailable",
            (self.review.container_dir / "claude-skip.txt").read_text(encoding="utf-8"),
        )
        self.assertFalse((self.review.container_dir / "claude-blocked.json").exists())
        self.assertFalse(
            (self.review.container_dir / "claude-unavailable.json").exists()
        )
        self.assertIn(
            "Native Claude review became unavailable",
            (self.review.container_dir / "runner-error.txt").read_text(
                encoding="utf-8"
            ),
        )
        persisted_attempts = json.loads(
            (self.review.container_dir / "attempts.json").read_text(encoding="utf-8")
        )
        self.assertEqual(persisted_attempts[0]["category"], "entitlement")

    def test_entitlement_fallback_keychain_broker_unavailable_writes_skip(
        self,
    ) -> None:
        entitlement = Completed(
            argv=("claude",),
            returncode=1,
            stdout=json.dumps(
                {
                    "type": "result",
                    "subtype": "error_during_execution",
                    "is_error": True,
                    "error": {"code": "model_not_enabled"},
                }
            ).encode(),
            stderr=b"",
        )
        credential = bytearray(oauth_credential_fixture())
        with (
            mock.patch.object(providers, "child_environment", return_value={}),
            mock.patch.object(
                providers,
                "_resolve_validated_claude_executable",
                side_effect=lambda *, review, env: (
                    pathlib.Path("/fixture/claude"),
                    dict(env),
                    frozenset(),
                ),
            ),
            mock.patch.object(
                providers,
                "_with_claude_review_tool_path",
                side_effect=lambda _review, env: dict(env),
            ),
            mock.patch.object(
                providers,
                "_prepare_claude_tls_environment",
                side_effect=lambda _review, env, **_kwargs: dict(env),
            ) as prepare_tls,
            mock.patch.object(
                providers,
                "_claude_keychain_runtime",
                side_effect=self.claude_keychain_runtime,
            ),
            mock.patch.object(
                providers,
                "_read_claude_keychain_credential",
                side_effect=(
                    credential,
                    providers.ClaudeKeychainBrokerUnavailable(
                        "fixture fallback keychain broker unavailable"
                    ),
                ),
            ) as read_credential,
            mock.patch.object(
                providers,
                "_claude_keychain_credential_server",
                return_value=contextlib.nullcontext(43211),
            ),
            mock.patch.object(
                providers,
                "_claude_connect_proxy",
                return_value=contextlib.nullcontext(43210),
            ),
            mock.patch.object(
                providers,
                "_claude_review_sandbox_profile",
                return_value="(version 1)(deny default)",
            ),
            mock.patch.object(
                providers,
                "run",
                side_effect=self.logged_run_side_effect(entitlement),
            ) as run_command,
        ):
            outcome = providers.run_review(
                review=self.review,
                reviewer="claude",
                egress_consent="triple-review",
            )

        self.assertEqual(outcome.returncode, 2)
        self.assertEqual(len(outcome.attempts), 1)
        self.assertEqual(outcome.attempts[0].category, "entitlement")
        self.assertEqual(read_credential.call_count, 2)
        self.assertEqual(run_command.call_count, 1)
        self.assertEqual(self.trust_preflight.call_count, 3)
        self.assertEqual(prepare_tls.call_count, 3)
        self.assertTrue((self.review.container_dir / "claude-skip.txt").is_file())
        self.assertIn(
            "secure runtime became unavailable",
            (self.review.container_dir / "claude-skip.txt").read_text(encoding="utf-8"),
        )
        self.assertFalse((self.review.container_dir / "claude-blocked.json").exists())
        self.assertFalse(
            (self.review.container_dir / "claude-unavailable.json").exists()
        )
        self.assertIn(
            "Native Claude review became unavailable",
            (self.review.container_dir / "runner-error.txt").read_text(
                encoding="utf-8"
            ),
        )
        persisted_attempts = json.loads(
            (self.review.container_dir / "attempts.json").read_text(encoding="utf-8")
        )
        self.assertEqual(persisted_attempts[0]["category"], "entitlement")

    def test_run_review_defers_api_key_trust_preflight_to_each_attempt(self) -> None:
        claude_env = {"ANTHROPIC_API_KEY": "fixture-key"}
        with (
            mock.patch.object(
                providers,
                "child_environment",
                return_value=claude_env,
            ),
            mock.patch.object(
                providers,
                "_resolve_validated_claude_executable",
                return_value=(
                    pathlib.Path("/fixture/claude"),
                    claude_env,
                    frozenset(),
                ),
            ),
            mock.patch.object(
                providers,
                "_with_claude_review_tool_path",
                return_value=claude_env,
            ),
            mock.patch.object(
                providers,
                "_prepare_claude_tls_environment",
                return_value=claude_env,
            ) as prepare_tls,
            mock.patch.object(
                providers,
                "_claude_attempt",
                return_value=self.attempt(
                    "claude",
                    providers.CLAUDE_MODELS[0],
                    "success",
                    final_text="No findings.",
                ),
            ),
        ):
            outcome = providers.run_review(
                review=self.review,
                reviewer="claude",
                egress_consent="triple-review",
            )

        self.assertEqual(outcome.returncode, 0)
        self.trust_preflight.assert_not_called()
        prepare_tls.assert_not_called()

    def test_run_review_uses_dedicated_trust_material_for_local_login_warmup(
        self,
    ) -> None:
        claude_env: dict[str, str] = {}
        warmup_material = providers.ClaudeTrustMaterial(
            certificates=self.sample_ca_certificate(),
            excluded_sha1_fingerprints=frozenset(),
            bundled_root_sha256_fingerprints=frozenset(),
        )
        warmup_env = {"TLS_PHASE": "warmup"}
        self.trust_preflight.return_value = warmup_material
        with (
            mock.patch.object(
                providers,
                "child_environment",
                return_value=claude_env,
            ),
            mock.patch.object(
                providers,
                "_resolve_validated_claude_executable",
                return_value=(
                    pathlib.Path("/fixture/claude"),
                    claude_env,
                    frozenset(),
                ),
            ),
            mock.patch.object(
                providers,
                "_with_claude_review_tool_path",
                return_value=claude_env,
            ),
            mock.patch.object(
                providers,
                "_prepare_claude_tls_environment",
                return_value=warmup_env,
            ) as prepare_tls,
            mock.patch.object(
                providers,
                "_claude_attempt",
                return_value=self.attempt(
                    "claude",
                    providers.CLAUDE_MODELS[0],
                    "success",
                    final_text="No findings.",
                ),
            ) as claude_attempt,
        ):
            outcome = providers.run_review(
                review=self.review,
                reviewer="claude",
                egress_consent="triple-review",
            )

        self.assertEqual(outcome.returncode, 0)
        self.trust_preflight.assert_called_once_with(
            self.review,
            bundled_root_sha256_fingerprints=frozenset(),
        )
        self.warmup.assert_called_once_with(
            self.review,
            pathlib.Path("/fixture/claude"),
            warmup_env,
        )
        self.assertEqual(
            prepare_tls.call_args_list,
            [
                mock.call(
                    self.review,
                    claude_env,
                    trust_material=warmup_material,
                ),
            ],
        )
        self.assertEqual(claude_attempt.call_args.kwargs["env"], claude_env)

    def test_real_claude_model_chain_rebuilds_tls_before_each_attempt(self) -> None:
        claude_env = {"ANTHROPIC_API_KEY": "fixture-key"}
        trust_materials: list[providers.ClaudeTrustMaterial] = []

        def fresh_trust_material(
            _review: ReviewWorkspace,
            **_kwargs: object,
        ) -> providers.ClaudeTrustMaterial:
            material = self.pending_claude_trust_material()
            trust_materials.append(material)
            return material

        first = Completed(
            argv=("claude",),
            returncode=1,
            stdout=json.dumps(
                {
                    "type": "result",
                    "subtype": "error_during_execution",
                    "is_error": True,
                    "error": {"code": "model_not_enabled"},
                }
            ).encode(),
            stderr=b"",
        )
        second = Completed(
            argv=("claude",),
            returncode=0,
            stdout=json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "No findings.",
                    "modelUsage": {providers.CLAUDE_MODELS[1]: {}},
                }
            ).encode(),
            stderr=b"",
        )
        self.trust_preflight.side_effect = fresh_trust_material
        with (
            mock.patch.object(
                providers,
                "child_environment",
                return_value=claude_env,
            ),
            mock.patch.object(
                providers,
                "_resolve_validated_claude_executable",
                side_effect=lambda *, review, env: (
                    pathlib.Path("/fixture/claude"),
                    dict(env),
                    frozenset(),
                ),
            ),
            mock.patch.object(
                providers,
                "_with_claude_review_tool_path",
                side_effect=lambda _review, env: dict(env),
            ),
            mock.patch.object(
                providers,
                "_prepare_claude_tls_environment",
                wraps=providers._prepare_claude_tls_environment,
            ) as prepare_tls,
            mock.patch.object(
                providers,
                "_claude_connect_proxy",
                return_value=contextlib.nullcontext(43210),
            ),
            mock.patch.object(
                providers,
                "_claude_review_sandbox_profile",
                return_value="(version 1)(deny default)",
            ),
            mock.patch.object(
                providers,
                "run",
                side_effect=self.logged_run_side_effect(first, second),
            ) as run_command,
        ):
            outcome = providers.run_review(
                review=self.review,
                reviewer="claude",
                egress_consent="triple-review",
            )

        self.assertEqual(outcome.returncode, 0)
        self.assertEqual(
            [attempt.category for attempt in outcome.attempts],
            ["entitlement", "success"],
        )
        self.assertIsNone(outcome.attempts[0].effective_model)
        self.assertEqual(
            outcome.attempts[1].effective_model,
            providers.CLAUDE_MODELS[1],
        )
        self.assertEqual(
            [attempt.effective_effort for attempt in outcome.attempts],
            [providers.CLAUDE_REASONING_EFFORT] * 2,
        )
        self.assertEqual(self.trust_preflight.call_count, 2)
        self.assertEqual(prepare_tls.call_count, 2)
        self.assertEqual(run_command.call_count, 2)
        self.assertEqual(len(trust_materials), 2)
        self.assertNotEqual(
            trust_materials[0].evidence["generation"],
            trust_materials[1].evidence["generation"],
        )
        for call, material in zip(
            prepare_tls.call_args_list,
            trust_materials,
            strict=True,
        ):
            self.assertIs(call.kwargs["trust_material"], material)
        evidence = json.loads(
            (
                self.review.container_dir / providers.CLAUDE_TRUST_POLICY_EVIDENCE_NAME
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(evidence["status"], "complete")

    def test_run_review_blocks_first_preflight_exclusion_with_terminal_evidence(
        self,
    ) -> None:
        claude_env = {"ANTHROPIC_API_KEY": "fixture-key"}
        excluded = providers.ClaudeTrustMaterial(
            certificates=b"",
            excluded_sha1_fingerprints=frozenset({"A" * 40}),
            bundled_root_sha256_fingerprints=frozenset(),
        )
        self.trust_preflight.side_effect = self.preflight_claude_trust_policy
        self.trust.side_effect = self.read_claude_trust_certificates
        with (
            mock.patch.object(
                providers,
                "child_environment",
                return_value=claude_env,
            ),
            mock.patch.object(
                providers,
                "_resolve_validated_claude_executable",
                return_value=(
                    pathlib.Path("/fixture/claude"),
                    claude_env,
                    frozenset(),
                ),
            ),
            mock.patch.object(
                providers,
                "_with_claude_review_tool_path",
                return_value=claude_env,
            ),
            mock.patch.object(
                providers,
                "_read_claude_trust_certificates_impl",
                return_value=excluded,
            ),
        ):
            outcome = providers.run_review(
                review=self.review,
                reviewer="claude",
                egress_consent="triple-review",
            )

        evidence = json.loads(
            (
                self.review.container_dir / providers.CLAUDE_TRUST_POLICY_EVIDENCE_NAME
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(outcome.returncode, 2)
        self.assertEqual(evidence["status"], "blocked")
        self.assertEqual(
            evidence["policy"],
            "require-bundled-root-subset",
        )
        self.assertTrue((self.review.container_dir / "claude-blocked.json").is_file())
        self.warmup.assert_not_called()

    def test_run_review_blocks_refreshed_exclusion_after_warmup(self) -> None:
        claude_env: dict[str, str] = {}
        initial = providers.ClaudeTrustMaterial(
            certificates=b"",
            excluded_sha1_fingerprints=frozenset(),
            bundled_root_sha256_fingerprints=frozenset(),
        )
        excluded = providers.ClaudeTrustMaterial(
            certificates=b"",
            excluded_sha1_fingerprints=frozenset({"B" * 40}),
            bundled_root_sha256_fingerprints=frozenset(),
        )
        self.trust_preflight.side_effect = self.preflight_claude_trust_policy
        self.trust.side_effect = self.read_claude_trust_certificates
        with (
            mock.patch.object(
                providers,
                "child_environment",
                return_value=claude_env,
            ),
            mock.patch.object(
                providers,
                "_resolve_validated_claude_executable",
                return_value=(
                    pathlib.Path("/fixture/claude"),
                    claude_env,
                    frozenset(),
                ),
            ),
            mock.patch.object(
                providers,
                "_with_claude_review_tool_path",
                return_value=claude_env,
            ),
            mock.patch.object(
                providers,
                "_read_claude_trust_certificates_impl",
                side_effect=(initial, excluded),
            ) as read_impl,
        ):
            outcome = providers.run_review(
                review=self.review,
                reviewer="claude",
                egress_consent="triple-review",
            )

        evidence = json.loads(
            (
                self.review.container_dir / providers.CLAUDE_TRUST_POLICY_EVIDENCE_NAME
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(outcome.returncode, 2)
        self.assertEqual(read_impl.call_count, 2)
        self.assertEqual(evidence["status"], "blocked")
        self.assertEqual(
            evidence["policy"],
            "require-bundled-root-subset",
        )
        self.warmup.assert_called_once()

    def test_run_review_replaces_complete_evidence_when_refreshed_system_ca_fails(
        self,
    ) -> None:
        claude_env: dict[str, str] = {}
        initial = providers.ClaudeTrustMaterial(
            certificates=b"",
            excluded_sha1_fingerprints=frozenset(),
            bundled_root_sha256_fingerprints=frozenset(),
        )
        first_generations: list[str] = []

        def corrupt_system_ca(*_args: object) -> None:
            evidence = json.loads(
                (
                    self.review.container_dir
                    / providers.CLAUDE_TRUST_POLICY_EVIDENCE_NAME
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(evidence["status"], "complete")
            first_generations.append(str(evidence["generation"]))
            private_key_label = b"PRIVATE" + b" KEY"
            self.claude_system_ca.write_bytes(
                b"-----BEGIN "
                + private_key_label
                + b"-----\nfixture\n-----END "
                + private_key_label
                + b"-----\n"
            )

        self.trust_preflight.side_effect = self.preflight_claude_trust_policy
        self.trust.side_effect = self.read_claude_trust_certificates
        self.warmup.side_effect = corrupt_system_ca
        with (
            mock.patch.object(
                providers,
                "child_environment",
                return_value=claude_env,
            ),
            mock.patch.object(
                providers,
                "_resolve_validated_claude_executable",
                return_value=(
                    pathlib.Path("/fixture/claude"),
                    claude_env,
                    frozenset(),
                ),
            ),
            mock.patch.object(
                providers,
                "_with_claude_review_tool_path",
                return_value=claude_env,
            ),
            mock.patch.object(
                providers,
                "_read_claude_trust_certificates_impl",
                return_value=initial,
            ) as read_impl,
        ):
            outcome = providers.run_review(
                review=self.review,
                reviewer="claude",
                egress_consent="triple-review",
            )

        evidence = json.loads(
            (
                self.review.container_dir / providers.CLAUDE_TRUST_POLICY_EVIDENCE_NAME
            ).read_text(encoding="utf-8")
        )
        runner_error = (self.review.container_dir / "runner-error.txt").read_text(
            encoding="utf-8"
        )
        self.assertEqual(outcome.returncode, 2)
        self.assertEqual(read_impl.call_count, 1)
        self.assertEqual(evidence["status"], "blocked")
        self.assertNotEqual(evidence["generation"], first_generations[0])
        self.assertTrue((self.review.container_dir / "claude-blocked.json").is_file())
        self.assertIn("cannot be represented", runner_error)
        self.assertNotIn("executable validation failed", runner_error)
        self.warmup.assert_called_once()

    def test_native_claude_entitlement_exhaustion_stops_the_lane(self) -> None:
        claude_env = {"ANTHROPIC_API_KEY": "fixture-key"}
        attempts = tuple(
            self.attempt("claude", model, "entitlement")
            for model in providers.CLAUDE_MODELS
        )
        with (
            mock.patch.object(
                providers,
                "child_environment",
                return_value=claude_env,
            ),
            mock.patch.object(
                providers,
                "_resolve_validated_claude_executable",
                return_value=(
                    pathlib.Path("/fixture/claude"),
                    claude_env,
                    frozenset(),
                ),
            ),
            mock.patch.object(
                providers,
                "_with_claude_review_tool_path",
                return_value=claude_env,
            ),
            mock.patch.object(
                providers,
                "_prepare_claude_tls_environment",
                return_value=claude_env,
            ),
            mock.patch.object(
                providers,
                "_claude_attempt",
                side_effect=attempts,
            ) as claude_attempt,
        ):
            outcome = providers.run_review(
                review=self.review,
                reviewer="claude",
                egress_consent="double-review",
            )

        self.assertEqual(outcome.returncode, 1)
        self.assertEqual(
            [attempt.requested_model for attempt in outcome.attempts],
            list(providers.CLAUDE_MODELS),
        )
        self.assertEqual(
            [call.kwargs["model"] for call in claude_attempt.call_args_list],
            list(providers.CLAUDE_MODELS),
        )
        self.assertIn(
            "blocked (entitlement); no alternate provider is configured",
            (self.review.container_dir / "runner-error.txt").read_text(
                encoding="utf-8"
            ),
        )

    def test_claude_environment_excludes_github_credentials_and_legacy_path(
        self,
    ) -> None:
        legacy_token = "COPI" + "LOT_GITHUB_TOKEN"
        legacy_path = "CODEX_REVIEW_COPI" + "LOT_PATH"
        host_values = {
            "ANTHROPIC_API_KEY": "placeholder-test-key",
            "GH_TOKEN": "github-fixture",
            "GITHUB_TOKEN": "github-fixture",
            legacy_token: "legacy-fixture",
            legacy_path: "/tmp/legacy-provider",
        }
        with mock.patch.dict(os.environ, host_values, clear=True):
            env = providers._review_environment(
                review=self.review,
                passthrough_keys=providers.CLAUDE_ENV_KEYS,
            )

        self.assertEqual(env["ANTHROPIC_API_KEY"], "placeholder-test-key")
        for key in ("GH_TOKEN", "GITHUB_TOKEN", legacy_token, legacy_path):
            self.assertNotIn(key, env)

    def test_trust_policy_preflight_propagates_deny_and_cleans_material(
        self,
    ) -> None:
        error = providers.ClaudeTrustSettingsDeny("explicit deny fixture")
        with mock.patch.object(
            providers,
            "_read_claude_trust_certificates",
            side_effect=error,
        ) as read_trust:
            with self.assertRaises(providers.ClaudeTrustSettingsDeny) as raised:
                self.preflight_claude_trust_policy(self.review)

        self.assertIs(raised.exception, error)
        read_trust.assert_called_once()
        preflight_root = read_trust.call_args.args[1]
        self.assertEqual(read_trust.call_args.args[0], self.review)
        self.assertFalse(preflight_root.exists())
        self.assertTrue(preflight_root.name.startswith("claude-trust-preflight-"))

    def test_trust_policy_preflight_terminalizes_system_ca_base_exceptions(
        self,
    ) -> None:
        for label, error in (
            ("forwarded-signal", common.ForwardedSignal(signal.SIGTERM)),
            ("keyboard-interrupt", KeyboardInterrupt("fixture interrupt")),
            ("cancellation", asyncio.CancelledError("fixture cancellation")),
            ("unexpected", RuntimeError("fixture failure")),
        ):
            with self.subTest(label=label):
                with (
                    mock.patch.object(
                        providers,
                        "_read_ca_source",
                        side_effect=error,
                    ),
                    self.assertRaises(type(error)) as raised,
                ):
                    self.preflight_claude_trust_policy(self.review)

                evidence = json.loads(
                    (
                        self.review.container_dir
                        / providers.CLAUDE_TRUST_POLICY_EVIDENCE_NAME
                    ).read_text(encoding="utf-8")
                )
                self.assertIs(raised.exception, error)
                self.assertEqual(evidence["status"], "inconclusive")
                self.assertEqual(
                    evidence["additional_root_resolution"],
                    "inconclusive",
                )

    def test_trust_policy_preflight_terminalizes_temp_cleanup_base_exceptions(
        self,
    ) -> None:
        real_temporary_directory = tempfile.TemporaryDirectory
        for label, error in (
            ("forwarded-signal", common.ForwardedSignal(signal.SIGTERM)),
            ("keyboard-interrupt", KeyboardInterrupt("fixture interrupt")),
            ("cancellation", asyncio.CancelledError("fixture cancellation")),
            ("unexpected", RuntimeError("fixture failure")),
        ):
            with self.subTest(label=label):

                @contextlib.contextmanager
                def cleanup_failure(*args: object, **kwargs: object):
                    with real_temporary_directory(*args, **kwargs) as temporary:
                        yield temporary
                    raise error

                with (
                    mock.patch.object(
                        providers.tempfile,
                        "TemporaryDirectory",
                        side_effect=cleanup_failure,
                    ),
                    self.assertRaises(type(error)) as raised,
                ):
                    self.preflight_claude_trust_policy(self.review)

                evidence = json.loads(
                    (
                        self.review.container_dir
                        / providers.CLAUDE_TRUST_POLICY_EVIDENCE_NAME
                    ).read_text(encoding="utf-8")
                )
                self.assertIs(raised.exception, error)
                self.assertEqual(evidence["status"], "inconclusive")
                self.assertEqual(
                    evidence["additional_root_resolution"],
                    "inconclusive",
                )

    def test_effective_model_substitution_does_not_infer_entitlement(self) -> None:
        completed = Completed(
            argv=("claude",),
            returncode=0,
            stdout=json.dumps(
                {"result": "No findings.", "modelUsage": {"claude-opus-4-7": {}}}
            ).encode(),
            stderr=b"",
        )
        attempt = providers._record_attempt(
            review=self.review,
            index=1,
            runtime="claude",
            model="claude-opus-4-8",
            completed=completed,
            final_text="No findings.",
            effective_model="claude-opus-4-7",
            requested_effort="max",
            effective_effort=None,
        )
        self.assertEqual(attempt.category, "runtime-unverified")
        self.assertIsNone(attempt.final_text)

    def test_failed_auth_and_entitlement_without_usage_retain_category(self) -> None:
        cases = (
            ("auth", "Not logged in - Please run /login"),
            ("entitlement", "Model is not enabled for your account"),
        )
        for index, (expected_category, message) in enumerate(cases, start=1):
            with self.subTest(expected_category=expected_category):
                completed = Completed(
                    argv=("claude",),
                    returncode=1,
                    stdout=json.dumps(
                        {
                            "type": "result",
                            "subtype": "error_during_execution",
                            "is_error": True,
                            "error": {"message": message},
                        }
                    ).encode(),
                    stderr=b"",
                )
                attempt = providers._record_attempt(
                    review=self.review,
                    index=index,
                    runtime="claude",
                    model="claude-opus-4-8",
                    completed=completed,
                    final_text=None,
                    effective_model=None,
                    requested_effort="max",
                    effective_effort=None,
                    require_verified_model=True,
                    require_verified_effort=True,
                )

                self.assertEqual(attempt.category, expected_category)
                self.assertEqual(attempt.returncode, 1)
                self.assertIsNone(attempt.final_text)

    def test_failed_attempt_metadata_mismatch_blocks_fallback(self) -> None:
        completed = Completed(
            argv=("codex",),
            returncode=1,
            stdout=json.dumps(
                {
                    "type": "turn.failed",
                    "error": {"message": "Model is not available for your account"},
                }
            ).encode(),
            stderr=b"",
        )
        cases = (
            (1, "gpt-5.5", "xhigh", "runtime-unverified"),
            (2, "gpt-5.6-sol", "high", "effort-mismatch"),
        )
        for index, effective_model, effective_effort, expected_category in cases:
            with self.subTest(expected_category=expected_category):
                attempt = providers._record_attempt(
                    review=self.review,
                    index=index,
                    runtime="codex",
                    model="gpt-5.6-sol",
                    completed=completed,
                    final_text=None,
                    effective_model=effective_model,
                    requested_effort="xhigh",
                    effective_effort=effective_effort,
                )
                self.assertEqual(attempt.category, expected_category)
                self.assertIsNone(attempt.final_text)

    @mock.patch.object(
        providers,
        "resolve_reviewer_executable",
        return_value=pathlib.Path("/bin/codex"),
    )
    @mock.patch.object(providers, "_codex_session_metadata")
    @mock.patch.object(providers, "run")
    def test_failed_codex_permission_mismatch_blocks_fallback(
        self,
        run_command: mock.Mock,
        session_metadata: mock.Mock,
        _resolve: mock.Mock,
    ) -> None:
        run_command.return_value = Completed(
            argv=("codex",),
            returncode=1,
            stdout=json.dumps(
                {
                    "type": "turn.failed",
                    "error": {"message": "Model is not available for your account"},
                }
            ).encode(),
            stderr=b"",
        )
        session_metadata.return_value = ("gpt-5.6-sol", "xhigh", False)

        attempt = providers._codex_attempt(
            review=self.review,
            model="gpt-5.6-sol",
            index=1,
            env={},
        )

        self.assertEqual(attempt.category, "permission-mismatch")
        self.assertIsNone(attempt.final_text)

    def test_success_without_verified_runtime_metadata_is_not_accepted(self) -> None:
        completed = Completed(
            argv=("codex",),
            returncode=0,
            stdout=b'{"type":"thread.started","thread_id":"missing"}\n',
            stderr=b"",
        )
        attempt = providers._record_attempt(
            review=self.review,
            index=1,
            runtime="codex",
            model="gpt-5.6-sol",
            completed=completed,
            final_text="No findings.",
            effective_model=None,
            requested_effort="xhigh",
            effective_effort=None,
            require_verified_model=True,
            require_verified_effort=True,
        )
        self.assertEqual(attempt.category, "runtime-unverified")
        self.assertIsNone(attempt.final_text)

    def test_success_without_verified_effort_is_not_accepted(self) -> None:
        completed = Completed(
            argv=("claude",),
            returncode=0,
            stdout=json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "No findings.",
                    "modelUsage": {"claude-opus-4-8": {}},
                }
            ).encode(),
            stderr=b"",
        )
        attempt = providers._record_attempt(
            review=self.review,
            index=1,
            runtime="claude",
            model="claude-opus-4-8",
            completed=completed,
            final_text="No findings.",
            effective_model="claude-opus-4-8",
            requested_effort="max",
            effective_effort=None,
            require_verified_model=True,
            require_verified_effort=True,
        )

        self.assertEqual(attempt.category, "runtime-unverified")
        self.assertIsNone(attempt.final_text)

    @mock.patch.object(providers, "child_environment", return_value={})
    def test_claude_lane_requires_explicit_egress_consent(
        self,
        _environment: mock.Mock,
    ) -> None:
        with mock.patch.object(providers, "resolve_reviewer_executable") as resolve:
            outcome = providers.run_review(
                review=self.review,
                reviewer="claude",
            )
        self.assertEqual(outcome.returncode, 2)
        resolve.assert_not_called()
        self.assertIn(
            "explicit egress-consent",
            (self.review.container_dir / "runner-error.txt").read_text(
                encoding="utf-8"
            ),
        )
        self.assertFalse(
            (self.review.container_dir / "claude-unavailable.json").exists()
        )

    @mock.patch.object(providers, "resolve_reviewer_executable")
    def test_sensitive_content_blocks_external_reviewer_before_launch(
        self,
        resolve: mock.Mock,
    ) -> None:
        secret = "AKIA" + "A" * 16
        (self.review.workspace_root / "secret.txt").write_text(
            secret + "\n",
            encoding="utf-8",
        )
        outcome = providers.run_review(
            review=self.review,
            reviewer="claude",
            egress_consent="double-review",
        )
        self.assertEqual(outcome.returncode, 2)
        resolve.assert_not_called()
        self.assertFalse((self.review.container_dir / "egress.json").exists())
        self.assertFalse((self.review.container_dir / "preflight.json").exists())
        self.assertFalse(
            (self.review.container_dir / "claude-unavailable.json").exists()
        )
        error = (self.review.container_dir / "runner-error.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("sensitive content preflight", error)
        self.assertNotIn(secret, error)

    @mock.patch.object(providers, "_codex_attempt")
    def test_sensitive_content_blocks_codex_before_launch(
        self,
        codex_attempt: mock.Mock,
    ) -> None:
        secret = "AKIA" + "B" * 16
        self.review.diff_file.write_text(
            "diff --git a/config b/config\n-AWS_KEY=" + secret + "\n",
            encoding="utf-8",
        )
        outcome = providers.run_review(
            review=self.review,
            reviewer="codex",
        )
        self.assertEqual(outcome.returncode, 2)
        codex_attempt.assert_not_called()
        self.assertFalse((self.review.container_dir / "preflight.json").exists())
        error = (self.review.container_dir / "runner-error.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("sensitive content preflight", error)
        self.assertNotIn(secret, error)

    @mock.patch.object(providers, "_review_environment", return_value={})
    @mock.patch.object(providers, "_run_model_chain")
    def test_codex_preflight_evidence_precedes_model_launch(
        self,
        run_model_chain: mock.Mock,
        _environment: mock.Mock,
    ) -> None:
        def inspect_preflight(**_kwargs):
            evidence = json.loads(
                (self.review.container_dir / "preflight.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                evidence["review_range"],
                f"{self.review.base_ref}..{self.review.head_ref}",
            )
            self.assertEqual(evidence["synthetic_secret_exemptions"], [])
            return "success", "No findings."

        run_model_chain.side_effect = inspect_preflight

        outcome = providers.run_review(
            review=self.review,
            reviewer="codex",
        )

        self.assertEqual(outcome.returncode, 0)
        self.assertEqual(outcome.final_text, "No findings.")

    def test_codex_preflight_records_applied_synthetic_fixture_exemption(
        self,
    ) -> None:
        identifier = "synthetic-fixture-v1"

        def inspect_preflight(**_kwargs):
            evidence = json.loads(
                (self.review.container_dir / "preflight.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                evidence["synthetic_secret_exemptions"],
                [identifier],
            )
            return "success", "No findings."

        with (
            mock.patch.object(
                providers,
                "validate_external_workspace",
                return_value=(identifier,),
            ),
            mock.patch.object(
                providers,
                "_review_environment",
                return_value={},
            ),
            mock.patch.object(
                providers,
                "_run_model_chain",
                side_effect=inspect_preflight,
            ),
        ):
            outcome = providers.run_review(
                review=self.review,
                reviewer="codex",
            )

        self.assertEqual(outcome.returncode, 0)
        self.assertEqual(outcome.final_text, "No findings.")

    @mock.patch.object(providers, "resolve_reviewer_executable")
    def test_deleted_generic_token_in_diff_blocks_external_reviewer(
        self,
        resolve: mock.Mock,
    ) -> None:
        token = "z9Y8x7W6v5U4t3S2r1Q0p9O8n7M6"
        self.review.diff_file.write_text(
            "diff --git a/config b/config\n-AUTH_TOKEN=" + token + "\n",
            encoding="utf-8",
        )
        outcome = providers.run_review(
            review=self.review,
            reviewer="claude",
            egress_consent="double-review",
        )
        self.assertEqual(outcome.returncode, 2)
        resolve.assert_not_called()
        error = (self.review.container_dir / "runner-error.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("review.diff (generic-secret-assignment)", error)
        self.assertNotIn(token, error)

    @mock.patch.object(providers, "resolve_reviewer_executable")
    def test_deleted_sensitive_path_blocks_external_reviewer(
        self,
        resolve: mock.Mock,
    ) -> None:
        (self.review.workspace_root / ".codex-review/changed-paths.z").write_bytes(
            b"config/.env.production\0"
        )
        outcome = providers.run_review(
            review=self.review,
            reviewer="claude",
            egress_consent="double-review",
        )
        self.assertEqual(outcome.returncode, 2)
        resolve.assert_not_called()
        error = (self.review.container_dir / "runner-error.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn(".env.production (environment-file; changed-path)", error)

    @mock.patch.object(providers, "resolve_reviewer_executable")
    def test_nested_credential_basename_blocks_external_reviewer(
        self,
        resolve: mock.Mock,
    ) -> None:
        credential = self.review.workspace_root / "fixtures/home/.netrc"
        credential.parent.mkdir(parents=True)
        credential.write_text("machine example.invalid\n", encoding="utf-8")
        outcome = providers.run_review(
            review=self.review,
            reviewer="claude",
            egress_consent="double-review",
        )
        self.assertEqual(outcome.returncode, 2)
        resolve.assert_not_called()
        error = (self.review.container_dir / "runner-error.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("fixtures/home/.netrc (credential-path)", error)

    @mock.patch.object(
        providers,
        "resolve_reviewer_executable",
        return_value=pathlib.Path("/bin/codex"),
    )
    @mock.patch.object(providers, "run")
    def test_codex_command_pins_model_and_reasoning(
        self,
        run_command: mock.Mock,
        _resolve: mock.Mock,
    ) -> None:
        thread_id = "019f18a6-ed56-7ff3-af51-08703a6d225a"
        codex_home = pathlib.Path(self.temporary.name) / "codex-home"
        rollout = (
            codex_home
            / "sessions/2026/06/30"
            / f"rollout-2026-06-30T21-10-20-{thread_id}.jsonl"
        )
        rollout.parent.mkdir(parents=True)
        rollout.write_text(
            json.dumps(
                {
                    "type": "turn_context",
                    "payload": {
                        "model": "gpt-5.6-sol",
                        "effort": "xhigh",
                        "approval_policy": "never",
                        "sandbox_policy": {"type": "read-only"},
                        "permission_profile": {
                            "type": "managed",
                            "network": "restricted",
                            "file_system": {
                                "type": "restricted",
                                "glob_scan_max_depth": 8,
                                "entries": [
                                    {
                                        "path": {
                                            "type": "special",
                                            "value": {"kind": "minimal"},
                                        },
                                        "access": "read",
                                    },
                                    {
                                        "path": {
                                            "type": "path",
                                            "path": str(
                                                self.review.workspace_root.resolve()
                                            ),
                                        },
                                        "access": "read",
                                    },
                                    *[
                                        {
                                            "path": {
                                                "type": "path",
                                                "path": str(
                                                    (
                                                        self.review.workspace_root
                                                        / name
                                                    ).resolve()
                                                ),
                                            },
                                            "access": "deny",
                                        }
                                        for name in (".git", ".codex", ".agents")
                                    ],
                                    *[
                                        {
                                            "path": {
                                                "type": "glob_pattern",
                                                "pattern": str(
                                                    self.review.workspace_root.resolve()
                                                    / pattern
                                                ),
                                            },
                                            "access": "deny",
                                        }
                                        for pattern in ("*.env", "**/*.env")
                                    ],
                                ],
                            },
                        },
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

        def complete(argv, **_kwargs):
            argv = tuple(argv)
            final_path = pathlib.Path(argv[argv.index("-o") + 1])
            final_path.parent.mkdir(parents=True, exist_ok=True)
            final_path.write_text("No findings.\n", encoding="utf-8")
            stdout = json.dumps(
                {"type": "thread.started", "thread_id": thread_id}
            ).encode()
            return Completed(argv=argv, returncode=0, stdout=stdout, stderr=b"")

        run_command.side_effect = complete
        attempt = providers._codex_attempt(
            review=self.review,
            model="gpt-5.6-sol",
            index=1,
            env={
                "CODEX_HOME": str(codex_home),
                "OPENAI_API_KEY": "parent-only-secret",
            },
        )
        argv = run_command.call_args.args[0]
        self.assertIn("gpt-5.6-sol", argv)
        self.assertIn('model_reasoning_effort="xhigh"', argv)
        configs = [argv[index + 1] for index, value in enumerate(argv) if value == "-c"]
        self.assertIn('approval_policy="never"', configs)
        self.assertIn('default_permissions="isolated_review"', configs)
        permission_configs = [
            value
            for value in configs
            if value.startswith("permissions.isolated_review=")
        ]
        self.assertEqual(len(permission_configs), 1)
        permission_config = permission_configs[0]
        parsed_permissions = tomllib.loads(
            f"profile = {permission_config.partition('=')[2]}"
        )["profile"]
        self.assertEqual(
            set(parsed_permissions["filesystem"]),
            {"glob_scan_max_depth", ":minimal", ":workspace_roots"},
        )
        self.assertIn('"glob_scan_max_depth"=8', permission_config)
        self.assertIn('":minimal"="read"', permission_config)
        self.assertIn('":workspace_roots"={"."="read"', permission_config)
        self.assertIn('".git"="deny"', permission_config)
        self.assertTrue(
            any("shell_environment_policy.inherit" in value for value in configs)
        )
        self.assertTrue(
            any("shell_environment_policy.set" in value for value in configs)
        )
        self.assertIn("project_doc_max_bytes=0", configs)
        self.assertNotIn("parent-only-secret", "\n".join(configs))
        self.assertIn("--skip-git-repo-check", argv)
        self.assertIn("--ignore-user-config", argv)
        self.assertIn("--ignore-rules", argv)
        self.assertIn("--strict-config", argv)
        self.assertNotIn("-s", argv)
        final_path = pathlib.Path(argv[argv.index("-o") + 1])
        self.assertTrue(final_path.parent.is_dir())
        self.assertEqual(attempt.effective_model, "gpt-5.6-sol")
        self.assertEqual(attempt.effective_effort, "xhigh")
        self.assertEqual(attempt.category, "success")
        self.assertEqual(
            run_command.call_args.kwargs["timeout_seconds"],
            providers.REVIEW_ATTEMPT_TIMEOUT_SECONDS,
        )
        self.assertEqual(
            run_command.call_args.kwargs["output_file_limit_bytes"],
            providers.REVIEW_ATTEMPT_OUTPUT_LIMIT_BYTES,
        )

    def test_codex_rejects_legacy_sandbox_override(self) -> None:
        payload = {
            "approval_policy": "never",
            "sandbox_policy": {"type": "workspace-write"},
            "permission_profile": {
                "type": "managed",
                "network": "restricted",
                "file_system": {"type": "restricted", "entries": []},
            },
        }
        self.assertFalse(
            providers._codex_permissions_match(
                payload,
                review_root=self.review.workspace_root,
            )
        )

    def test_codex_rejects_extra_permission_profile_read_path(self) -> None:
        root = self.review.workspace_root.resolve()
        payload = {
            "approval_policy": "never",
            "sandbox_policy": {"type": "read-only"},
            "permission_profile": {
                "type": "managed",
                "network": "restricted",
                "file_system": {
                    "type": "restricted",
                    "glob_scan_max_depth": 8,
                    "entries": [
                        {
                            "path": {
                                "type": "special",
                                "value": {"kind": "minimal"},
                            },
                            "access": "read",
                        },
                        {"path": {"type": "path", "path": str(root)}, "access": "read"},
                        *[
                            {
                                "path": {
                                    "type": "path",
                                    "path": str((root / name).resolve()),
                                },
                                "access": "deny",
                            }
                            for name in (".git", ".codex", ".agents")
                        ],
                        *[
                            {
                                "path": {
                                    "type": "glob_pattern",
                                    "pattern": str(root / pattern),
                                },
                                "access": "deny",
                            }
                            for pattern in ("*.env", "**/*.env")
                        ],
                        {
                            "path": {"type": "path", "path": str(root.parent)},
                            "access": "read",
                        },
                    ],
                },
            },
        }
        self.assertFalse(
            providers._codex_permissions_match(
                payload,
                review_root=self.review.workspace_root,
            )
        )

    def test_codex_allows_only_one_direct_arg_transport_file(self) -> None:
        root = self.review.workspace_root.resolve()
        codex_home = pathlib.Path(self.temporary.name) / "codex-home"
        arg_root = codex_home.resolve() / "tmp/arg0"

        def payload(extra_entries):
            return {
                "approval_policy": "never",
                "sandbox_policy": {"type": "read-only"},
                "permission_profile": {
                    "type": "managed",
                    "network": "restricted",
                    "file_system": {
                        "type": "restricted",
                        "glob_scan_max_depth": 8,
                        "entries": [
                            {
                                "path": {
                                    "type": "special",
                                    "value": {"kind": "minimal"},
                                },
                                "access": "read",
                            },
                            {
                                "path": {"type": "path", "path": str(root)},
                                "access": "read",
                            },
                            *[
                                {
                                    "path": {
                                        "type": "path",
                                        "path": str((root / name).resolve()),
                                    },
                                    "access": "deny",
                                }
                                for name in (".git", ".codex", ".agents")
                            ],
                            *[
                                {
                                    "path": {
                                        "type": "glob_pattern",
                                        "pattern": str(root / pattern),
                                    },
                                    "access": "deny",
                                }
                                for pattern in ("*.env", "**/*.env")
                            ],
                            *extra_entries,
                        ],
                    },
                },
            }

        def read_entry(path: pathlib.Path):
            return {
                "path": {"type": "path", "path": str(path)},
                "access": "read",
            }

        direct = read_entry(arg_root / "codex-arg0AbE73u")
        nested = read_entry(arg_root / "private/codex-arg0AbE73u")
        second = read_entry(arg_root / "codex-arg0Second")
        self.assertTrue(
            providers._codex_permissions_match(
                payload([direct]),
                review_root=root,
                codex_home=codex_home,
            )
        )
        for extras in ([nested], [direct, second]):
            with self.subTest(extras=extras):
                self.assertFalse(
                    providers._codex_permissions_match(
                        payload(extras),
                        review_root=root,
                        codex_home=codex_home,
                    )
                )

    @mock.patch.object(
        providers,
        "_native_macho_dependencies",
        return_value=(
            pathlib.Path("/review-install/claude"),
            pathlib.Path("/review-real/claude"),
        ),
    )
    def test_claude_probe_profile_only_reads_runtime_and_probe_roots(
        self,
        _dependencies: mock.Mock,
    ) -> None:
        profile = providers._claude_probe_sandbox_profile(
            pathlib.Path("/review-install/claude"),
            pathlib.Path("/isolated/probe-home"),
        )

        self.assertIn("(deny default)", profile)
        self.assertNotIn("(allow default)", profile)
        self.assertIn('(literal "/review-install/claude")', profile)
        self.assertIn('(literal "/review-real/claude")', profile)
        self.assertIn('(subpath "/isolated/probe-home")', profile)
        self.assertIn('(subpath "/review-install")', profile)
        self.assertIn('(subpath "/review-real")', profile)
        self.assertNotIn("(allow file-read-metadata)", profile)
        self.assertIn(
            '(allow file-read-metadata (literal "/")',
            profile,
        )
        deny_rule = '(deny file-read* (subpath "/System/Library/Keychains"))'
        self.assertIn(deny_rule, profile)
        self.assertGreater(
            profile.index(deny_rule),
            profile.index("(allow file-read* "),
        )
        self.assertNotIn("/Users/joey", profile)

    def test_claude_keychain_deny_overrides_runtime_ancestor_allow(self) -> None:
        system_library = pathlib.Path("/System/Library")
        keychain_root = pathlib.Path("/System/Library/Keychains")
        system_roots = keychain_root / "SystemRootCertificates.keychain"
        framework = system_library / "Frameworks/Security.framework"

        self.assertTrue(providers.is_relative_to(system_roots, system_library))
        self.assertTrue(providers.is_relative_to(framework, system_library))
        self.assertTrue(providers.is_relative_to(system_roots, keychain_root))
        self.assertFalse(providers.is_relative_to(framework, keychain_root))
        self.assertFalse(providers.is_relative_to(system_library, keychain_root))
        self.assertEqual(
            providers.CLAUDE_SANDBOX_DENIED_KEYCHAIN_SUBPATHS,
            (keychain_root,),
        )

    def test_claude_probe_profile_rejects_overly_broad_dependency_roots(
        self,
    ) -> None:
        for dependency in (
            pathlib.Path("/Users/joey/claude"),
            pathlib.Path("/claude"),
        ):
            with (
                self.subTest(dependency=dependency),
                mock.patch.object(
                    providers,
                    "_native_macho_dependencies",
                    return_value=(dependency,),
                ),
                mock.patch.dict(providers.os.environ, {"HOME": "/Users/joey"}),
            ):
                with self.assertRaisesRegex(
                    providers.InvalidReviewerExecutable, "overly broad"
                ):
                    providers._claude_probe_sandbox_profile(
                        dependency,
                        pathlib.Path("/isolated/probe-home"),
                    )

    @mock.patch.object(providers, "_run_claude_probe")
    def test_claude_identity_requires_exact_supported_version(
        self,
        run_probe: mock.Mock,
    ) -> None:
        run_probe.return_value = Completed(
            argv=("claude", "--version"),
            returncode=0,
            stdout=b"2.1.203 (Claude Code)\n",
            stderr=b"",
        )

        with self.assertRaisesRegex(
            providers.InvalidReviewerExecutable,
            "supported Claude Code 2.1.202",
        ):
            providers._require_claude_identity(
                pathlib.Path("/bin/claude"),
                {"HOME": "/isolated/probe-home"},
            )

    def test_real_resolver_types_rejected_automatic_claude_candidate(
        self,
    ) -> None:
        wrapper = self.review.source_root / "claude"
        wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        wrapper.chmod(0o700)

        def reject_candidate(_candidate: pathlib.Path) -> None:
            raise providers.InvalidReviewerExecutable("not native")

        with (
            mock.patch.object(
                common,
                "_user_executable_candidates",
                return_value=(wrapper,),
            ),
            mock.patch.object(common.shutil, "which", return_value=None),
            mock.patch.object(
                common.pathlib.Path,
                "is_file",
                autospec=True,
                side_effect=lambda path: path == wrapper,
            ),
            mock.patch.object(
                common.os,
                "access",
                side_effect=lambda path, _mode: pathlib.Path(path) == wrapper,
            ),
            mock.patch.dict(common.os.environ, {}, clear=True),
            self.assertRaises(common.RejectedReviewerCandidates),
        ):
            common.resolve_reviewer_executable(
                "claude",
                candidate_validator=reject_candidate,
            )

    @mock.patch.object(
        providers,
        "resolve_reviewer_executable",
        side_effect=common.RejectedReviewerCandidates("only wrapper found"),
    )
    def test_claude_resolver_maps_automatic_rejection_to_unavailable(
        self,
        _resolve: mock.Mock,
    ) -> None:
        with self.assertRaisesRegex(
            providers.ClaudeExecutableUnavailable,
            "only wrapper found",
        ):
            providers._resolve_validated_claude_executable(
                review=self.review,
                env={"HOME": str(self.review.container_dir / "home")},
            )

    def test_claude_review_path_pins_broker_and_trusted_ripgrep(self) -> None:
        trusted_dir = self.review.source_root / "trusted-tools"
        trusted_dir.mkdir()
        trusted_rg = trusted_dir / "rg"
        trusted_rg.write_bytes(b"\xcf\xfa\xed\xfe" + b"\x00" * 32)
        trusted_rg.chmod(0o700)

        with mock.patch.object(
            providers,
            "CLAUDE_REVIEW_TOOL_EXECUTABLE_CANDIDATES",
            (trusted_rg,),
        ):
            prepared = providers._with_claude_review_tool_path(
                self.review,
                {
                    "PATH": "/untrusted/claude:/usr/bin",
                },
            )

        self.assertEqual(
            prepared["PATH"].split(os.pathsep),
            [str(self.claude_broker.parent.resolve()), str(trusted_dir.absolute())],
        )

    def test_trusted_ripgrep_skips_invalid_native_candidate(self) -> None:
        first_dir = self.review.source_root / "first-tools"
        second_dir = self.review.source_root / "second-tools"
        first_dir.mkdir()
        second_dir.mkdir()
        first = first_dir / "rg"
        second = second_dir / "rg"
        for candidate in (first, second):
            candidate.write_bytes(b"fixture")
            candidate.chmod(0o700)

        def dependencies(path: pathlib.Path, *, label: str) -> tuple[pathlib.Path, ...]:
            self.assertEqual(label, "ripgrep")
            if path == first:
                raise providers.InvalidReviewerExecutable("not native")
            return (path,)

        with (
            mock.patch.object(
                providers,
                "CLAUDE_REVIEW_TOOL_EXECUTABLE_CANDIDATES",
                (first, second),
            ),
            mock.patch.object(
                providers,
                "_native_macho_dependencies",
                side_effect=dependencies,
            ),
        ):
            selected = providers._trusted_claude_ripgrep()

        self.assertEqual(selected, second)

    def test_claude_review_path_classifies_non_native_ripgrep_unavailable(
        self,
    ) -> None:
        with (
            mock.patch.object(
                providers,
                "_trusted_claude_ripgrep",
                return_value=pathlib.Path("/usr/bin/rg"),
            ),
            mock.patch.object(
                providers,
                "_native_macho_dependencies",
                side_effect=providers.InvalidReviewerExecutable(
                    "ripgrep must be native"
                ),
            ),
            self.assertRaisesRegex(
                providers.ClaudeReviewToolUnavailable,
                "ripgrep must be native",
            ),
        ):
            providers._with_claude_review_tool_path(self.review, {})

    @mock.patch.object(providers, "_trusted_claude_ripgrep", return_value=None)
    def test_claude_sandbox_classifies_missing_ripgrep_unavailable(
        self,
        _rg: mock.Mock,
    ) -> None:
        with self.assertRaisesRegex(
            providers.ClaudeReviewToolUnavailable,
            "requires ripgrep",
        ):
            providers._claude_review_sandbox_profile(
                pathlib.Path("/bin/true"),
                self.review,
                {
                    "ANTHROPIC_API_KEY": "test-api-key",
                    "HOME": str(self.review.container_dir / "claude-home"),
                    "TMPDIR": str(self.review.container_dir / "tmp"),
                },
                proxy_port=43210,
            )

    @mock.patch.object(
        providers,
        "resolve_reviewer_executable",
        return_value=pathlib.Path("/bin/claude"),
    )
    @mock.patch.object(
        providers,
        "_trusted_claude_ripgrep",
        return_value=pathlib.Path("/bin/echo"),
    )
    @mock.patch.object(
        providers,
        "CLAUDE_PROBE_SANDBOX",
        pathlib.Path("/usr/bin/true"),
    )
    @mock.patch.object(
        providers,
        "_claude_connect_proxy",
        return_value=contextlib.nullcontext(43210),
    )
    @mock.patch.object(providers, "run")
    def test_claude_command_pins_model_and_max_with_local_login_safe_mode(
        self,
        run_command: mock.Mock,
        _proxy: mock.Mock,
        _rg: mock.Mock,
        resolve: mock.Mock,
    ) -> None:
        def resolve_and_validate(_name: str, **kwargs) -> pathlib.Path:
            candidate = pathlib.Path("/bin/claude")
            kwargs["candidate_validator"](candidate)
            return candidate

        resolve.side_effect = resolve_and_validate
        self.assertIn("(deny default)", providers.CLAUDE_PROBE_SANDBOX_PROFILE)
        self.assertNotIn("(allow default)", providers.CLAUDE_PROBE_SANDBOX_PROFILE)
        payload = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "No findings.",
            "modelUsage": {"claude-opus-4-8": {}},
        }
        run_command.side_effect = (
            Completed(
                argv=("claude", "--version"),
                returncode=0,
                stdout=b"2.1.202 (Claude Code)\n",
                stderr=b"",
            ),
            Completed(
                argv=("claude", "--help"),
                returncode=0,
                stdout=(
                    "Options:\n  "
                    + providers.CLAUDE_SAFE_MODE_HELP_FORM
                    + "\n  --betas <betas...> Beta headers\n"
                ).encode(),
                stderr=b"",
            ),
            Completed(
                argv=("claude",),
                returncode=0,
                stdout=json.dumps(payload).encode(),
                stderr=b"",
            ),
        )
        attempt = providers._claude_attempt(
            review=self.review,
            model="claude-opus-4-8",
            index=1,
            env={
                "HOME": "/Users/reviewer",
                "XDG_CONFIG_HOME": "/Users/reviewer/.config",
                "TMPDIR": str(self.review.container_dir / "tmp"),
                "PATH": str(self.claude_broker.parent),
                "CODEX_ISOLATED_REVIEW_RANGE": "base..head",
            },
        )
        self.assertEqual(attempt.category, "success")
        self.assertEqual(attempt.effective_model, "claude-opus-4-8")
        self.assertEqual(attempt.effective_effort, "max")
        self.trust_preflight.assert_called_once_with(
            self.review,
            bundled_root_sha256_fingerprints=frozenset(),
        )
        argv = run_command.call_args_list[2].args[0]
        self.assertIn("claude-opus-4-8", argv)
        self.assertEqual(argv[argv.index("--effort") + 1], "max")
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "default")
        self.assertNotIn("--prompt-suggestions", argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "Read,Grep,Glob")
        self.assertEqual(argv[argv.index("--allowedTools") + 1], "Read(./**)")
        self.assertNotIn("Read,Grep,Glob", argv[argv.index("--allowedTools") + 1 :])
        settings = json.loads(argv[argv.index("--settings") + 1])
        self.assertIn("Read(~/.ssh/**)", settings["permissions"]["deny"])
        self.assertTrue(settings["disableAllHooks"])
        self.assertEqual(argv[:2], ("/usr/bin/true", "-p"))
        self.assertEqual(argv[3], "/bin/claude")
        review_profile = argv[2]
        ca_bundle = (
            self.review.container_dir / "claude-ca" / providers.CLAUDE_CA_BUNDLE_NAME
        )
        self.assertIn("(deny default)", review_profile)
        self.assertIn(str(self.review.workspace_root), review_profile)
        self.assertNotIn("com.apple.securityd.xpc", review_profile)
        self.assertIn('(remote ip "localhost:43210")', review_profile)
        self.assertIn('(remote ip "localhost:43211")', review_profile)
        self.assertIn("(allow process-fork)", review_profile)
        self.assertIn(f'(literal "{self.claude_broker.resolve()}")', review_profile)
        self.assertNotIn('(literal "/usr/bin/security")', review_profile)
        self.assertNotIn(f'(subpath "{self.claude_broker.parent}")', review_profile)
        self.assertIn('(literal "/bin/echo")', review_profile)
        self.assertNotIn('(literal "/bin/sh")', review_profile)
        self.assertNotIn("mdsDirectory.db", review_profile)
        self.assertNotIn("mdsObject.db", review_profile)
        self.assertNotIn('(subpath "/private/etc/ssl")', review_profile)
        self.assertNotIn("/private/etc/ssl/cert.pem", review_profile)
        self.assertNotIn("/Library/Keychains/System.keychain", review_profile)
        deny_rule = '(deny file-read* (subpath "/System/Library/Keychains"))'
        self.assertIn(deny_rule, review_profile)
        self.assertGreater(
            review_profile.index(deny_rule),
            review_profile.index("(allow file-read* "),
        )
        self.assertIn(f'(literal "{ca_bundle}")', review_profile)
        self.assertNotIn("/Users/reviewer", review_profile)
        self.assertIn("--safe-mode", argv)
        self.assertNotIn("--bare", argv)
        self.assertIn("--strict-mcp-config", argv)
        self.assertEqual(
            argv[argv.index("--mcp-config") + 1],
            '{"mcpServers":{}}',
        )
        version_argv = run_command.call_args_list[0].args[0]
        self.assertEqual(version_argv[:2], ("/usr/bin/true", "-p"))
        self.assertEqual(
            version_argv[3:],
            ("/bin/claude", "--safe-mode", "--version"),
        )
        self.assertIn("(deny default)", version_argv[2])
        self.assertIn('(literal "/bin/claude")', version_argv[2])
        self.assertNotIn("(allow default)", version_argv[2])
        probe_env = run_command.call_args_list[0].kwargs["env"]
        self.assertNotIn("ANTHROPIC_API_KEY", probe_env)
        self.assertNotIn("CODEX_ISOLATED_REVIEW_RANGE", probe_env)
        for key in (
            *providers.CLAUDE_TLS_FILE_ENV_KEYS,
            *providers.CLAUDE_TLS_DIR_ENV_KEYS,
            providers.CLAUDE_CERT_STORE_ENV,
        ):
            self.assertNotIn(key, probe_env)
        self.assertEqual(
            probe_env["HOME"],
            str(self.review.container_dir / "claude-probe-home"),
        )
        self.assertNotIn("XDG_CONFIG_HOME", probe_env)
        self.assertEqual(
            run_command.call_args_list[1].args[0][-3:],
            ("/bin/claude", "--safe-mode", "--help"),
        )
        self.assertEqual(run_command.call_args_list[1].kwargs["env"], probe_env)
        for probe_call in run_command.call_args_list[:2]:
            self.assertEqual(
                probe_call.kwargs["timeout_seconds"],
                providers.CLAUDE_PROBE_TIMEOUT_SECONDS,
            )
            self.assertEqual(
                probe_call.kwargs["capture_limit_bytes"],
                providers.CLAUDE_PROBE_OUTPUT_LIMIT_BYTES,
            )
            self.assertEqual(
                probe_call.kwargs["output_file_limit_bytes"],
                providers.CLAUDE_PROBE_OUTPUT_LIMIT_BYTES,
            )
            self.assertEqual(
                probe_call.kwargs["stdout_path"].parent.parent,
                self.review.container_dir / "claude-probe-home",
            )
            self.assertFalse(probe_call.kwargs["stdout_path"].parent.exists())
        review_env = run_command.call_args_list[2].kwargs["env"]
        self.assertNotIn("ANTHROPIC_API_KEY", review_env)
        self.assertEqual(
            review_env["HOME"],
            str(self.review.container_dir / "claude-home"),
        )
        self.assertEqual(
            review_env["CLAUDE_CODE_TMPDIR"],
            str(self.review.container_dir / "tmp"),
        )
        self.assertNotIn("XDG_CONFIG_HOME", review_env)
        self.assertEqual(
            review_env["PATH"].split(os.pathsep),
            [str(self.claude_broker.parent.resolve()), "/bin"],
        )
        self.assertEqual(review_env["HTTPS_PROXY"], "http://127.0.0.1:43210")
        self.assertEqual(review_env["HTTP_PROXY"], review_env["HTTPS_PROXY"])
        self.assertEqual(review_env["ALL_PROXY"], review_env["HTTPS_PROXY"])
        self.assertEqual(review_env["NO_PROXY"], "")
        self.assertEqual(review_env["SSL_CERT_FILE"], str(ca_bundle))
        self.assertEqual(review_env["NODE_EXTRA_CA_CERTS"], str(ca_bundle))
        self.assertEqual(
            review_env[providers.CLAUDE_CERT_STORE_ENV],
            providers.CLAUDE_CERT_STORE,
        )
        self.assertEqual(
            run_command.call_args_list[2].kwargs["timeout_seconds"],
            providers.REVIEW_ATTEMPT_TIMEOUT_SECONDS,
        )
        self.assertEqual(
            run_command.call_args_list[2].kwargs["output_file_limit_bytes"],
            providers.REVIEW_ATTEMPT_OUTPUT_LIMIT_BYTES,
        )

    def test_claude_attempt_tls_rebuild_terminalizes_internal_trust_evidence(
        self,
    ) -> None:
        payload = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "No findings.",
            "modelUsage": {"claude-opus-4-8": {}},
        }
        self.trust_preflight.side_effect = self.preflight_claude_trust_policy
        self.trust.side_effect = self.read_claude_trust_certificates
        with (
            mock.patch.object(
                providers,
                "_resolve_validated_claude_executable",
                side_effect=lambda *, review, env: (
                    pathlib.Path("/bin/claude"),
                    dict(env),
                    frozenset(),
                ),
            ),
            mock.patch.object(
                providers,
                "_with_claude_review_tool_path",
                side_effect=lambda _review, env: dict(env),
            ),
            mock.patch.object(
                providers,
                "_claude_connect_proxy",
                return_value=contextlib.nullcontext(43210),
            ),
            mock.patch.object(
                providers,
                "_claude_review_sandbox_profile",
                return_value="(version 1)(deny default)",
            ),
            mock.patch.object(
                providers,
                "_read_claude_trust_certificates_impl",
                return_value=providers.ClaudeTrustMaterial(
                    certificates=b"",
                    excluded_sha1_fingerprints=frozenset(),
                    bundled_root_sha256_fingerprints=frozenset(),
                ),
            ),
            mock.patch.object(
                providers,
                "run",
                return_value=Completed(
                    argv=("claude",),
                    returncode=0,
                    stdout=json.dumps(payload).encode(),
                    stderr=b"",
                ),
            ),
        ):
            attempt = providers._claude_attempt(
                review=self.review,
                model="claude-opus-4-8",
                index=1,
                env={
                    "HOME": str(self.review.container_dir / "claude-home"),
                    "TMPDIR": str(self.review.container_dir / "tmp"),
                },
            )

        evidence = json.loads(
            (
                self.review.container_dir / providers.CLAUDE_TRUST_POLICY_EVIDENCE_NAME
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(attempt.category, "success")
        self.assertEqual(evidence["status"], "complete")
        self.assertTrue(
            (
                self.review.container_dir
                / "claude-ca"
                / providers.CLAUDE_CA_BUNDLE_NAME
            ).is_file()
        )
        self.trust.assert_called_once()

    def test_claude_review_sandbox_rejects_host_home(self) -> None:
        with self.assertRaisesRegex(ReviewError, "helper-owned HOME and TMPDIR"):
            providers._claude_review_sandbox_profile(
                pathlib.Path("/bin/true"),
                self.review,
                {
                    "HOME": "/Users/reviewer",
                    "TMPDIR": str(self.review.container_dir / "tmp"),
                },
                proxy_port=43210,
            )

    @mock.patch.object(
        providers,
        "_trusted_claude_ripgrep",
        return_value=None,
    )
    def test_claude_review_sandbox_requires_trusted_ripgrep(
        self,
        _rg: mock.Mock,
    ) -> None:
        with self.assertRaisesRegex(ReviewError, "requires ripgrep"):
            providers._claude_review_sandbox_profile(
                pathlib.Path("/bin/true"),
                self.review,
                {
                    "HOME": str(self.review.container_dir / "claude-home"),
                    "TMPDIR": str(self.review.container_dir / "tmp"),
                    "PATH": str(self.claude_broker.parent),
                    providers.CLAUDE_KEYCHAIN_BROKER_PORT_ENV: "43211",
                    providers.CLAUDE_KEYCHAIN_BROKER_CAPABILITY_ENV: "00" * 32,
                },
                proxy_port=43210,
            )

    @mock.patch.object(
        providers,
        "_trusted_claude_ripgrep",
        return_value=pathlib.Path("/bin/echo"),
    )
    def test_claude_review_sandbox_reads_only_exact_executable(
        self,
        _rg: mock.Mock,
    ) -> None:
        install_dir = self.review.source_root / "private-install"
        install_dir.mkdir()
        executable = install_dir / "claude"
        executable.write_bytes(b"\xcf\xfa\xed\xfe" + b"\x00" * 32)
        executable.chmod(0o700)

        profile = providers._claude_review_sandbox_profile(
            executable,
            self.review,
            {
                "HOME": str(self.review.container_dir / "claude-home"),
                "TMPDIR": str(self.review.container_dir / "tmp"),
                "PATH": str(self.claude_broker.parent),
                providers.CLAUDE_KEYCHAIN_BROKER_PORT_ENV: "43211",
                providers.CLAUDE_KEYCHAIN_BROKER_CAPABILITY_ENV: "00" * 32,
            },
            proxy_port=43210,
        )

        self.assertIn(f'(literal "{executable.resolve()}")', profile)
        self.assertNotIn(f'(subpath "{install_dir.resolve()}")', profile)

    @mock.patch.object(
        providers,
        "_trusted_claude_ripgrep",
        return_value=pathlib.Path("/bin/echo"),
    )
    def test_claude_review_sandbox_allows_exact_custom_ca_paths(
        self,
        _rg: mock.Mock,
    ) -> None:
        certificate = self.sample_ca_certificate()
        ca_file = self.review.source_root / "corporate-ca.pem"
        ca_file.write_bytes(certificate)
        ca_dir = self.review.source_root / "certs"
        ca_dir.mkdir()
        (ca_dir / "12345678.0").write_bytes(certificate)

        prepared_env = providers._prepare_claude_tls_environment(
            self.review,
            {
                "HOME": str(self.review.container_dir / "claude-home"),
                "TMPDIR": str(self.review.container_dir / "tmp"),
                "PATH": str(self.claude_broker.parent),
                providers.CLAUDE_KEYCHAIN_BROKER_PORT_ENV: "43211",
                providers.CLAUDE_KEYCHAIN_BROKER_CAPABILITY_ENV: "00" * 32,
                "SSL_CERT_FILE": str(ca_file),
                "SSL_CERT_DIR": str(ca_dir),
            },
        )

        profile = providers._claude_review_sandbox_profile(
            pathlib.Path("/bin/true"),
            self.review,
            prepared_env,
            proxy_port=43210,
        )

        prepared_file = pathlib.Path(prepared_env["SSL_CERT_FILE"])
        self.assertTrue(
            providers.is_relative_to(prepared_file, self.review.container_dir)
        )
        self.assertNotIn("SSL_CERT_DIR", prepared_env)
        self.assertEqual(prepared_env["NODE_EXTRA_CA_CERTS"], str(prepared_file))
        self.assertIn(f'(literal "{prepared_file}")', profile)
        self.assertNotIn(str(ca_file), profile)
        self.assertNotIn(str(ca_dir), profile)
        self.assertNotIn(str(self.claude_system_ca), profile)
        self.assertNotIn(str(providers.CLAUDE_SYSTEM_KEYCHAIN), profile)

    def test_claude_tls_preparation_composes_default_admin_and_custom_ca(
        self,
    ) -> None:
        system_certificate, custom_certificate, admin_certificate = (
            self.sample_ca_certificates(3)
        )
        self.claude_system_ca.write_bytes(system_certificate)
        self.trust.return_value = providers.ClaudeTrustMaterial(
            certificates=admin_certificate,
            excluded_sha1_fingerprints=frozenset(),
            bundled_root_sha256_fingerprints=frozenset(
                {
                    self.ca_sha256_fingerprint(system_certificate),
                    self.ca_sha256_fingerprint(admin_certificate),
                }
            ),
        )
        custom_path = self.review.source_root / "caller-ca.pem"
        custom_path.write_bytes(custom_certificate)

        prepared = providers._prepare_claude_tls_environment(
            self.review,
            {"NODE_EXTRA_CA_CERTS": str(custom_path)},
        )
        bundle = pathlib.Path(prepared["SSL_CERT_FILE"])
        first_content = bundle.read_bytes()
        blocks = providers.CLAUDE_CERTIFICATE_BLOCK.findall(first_content)
        actual = {
            hashlib.sha256(
                providers._canonical_ca_certificate(
                    block,
                    source="test bundle",
                )[0]
            ).hexdigest()
            for block in blocks
        }
        expected = {
            hashlib.sha256(
                providers._canonical_ca_certificate(
                    certificate,
                    source="test fixture",
                )[0]
            ).hexdigest()
            for certificate in (
                system_certificate,
                admin_certificate,
                custom_certificate,
            )
        }

        self.assertEqual(actual, expected)
        self.assertEqual(len(blocks), 3)
        self.assertTrue(providers.is_relative_to(bundle, self.review.container_dir))
        self.assertEqual(
            {prepared[key] for key in providers.CLAUDE_TLS_FILE_ENV_KEYS},
            {str(bundle)},
        )
        self.assertEqual(
            prepared[providers.CLAUDE_CERT_STORE_ENV],
            providers.CLAUDE_CERT_STORE,
        )
        self.assertNotIn(str(custom_path), prepared.values())
        self.assertNotIn(str(self.claude_system_ca), prepared.values())

        repeated = providers._prepare_claude_tls_environment(self.review, prepared)
        self.assertEqual(
            pathlib.Path(repeated["SSL_CERT_FILE"]).read_bytes(), first_content
        )

    def test_claude_tls_scrubs_hidden_root_bypass_environment(self) -> None:
        system_certificate = self.sample_ca_certificate()
        self.claude_system_ca.write_bytes(system_certificate)
        trust_material = providers.ClaudeTrustMaterial(
            certificates=b"",
            excluded_sha1_fingerprints=frozenset(),
            bundled_root_sha256_fingerprints=frozenset(
                {self.ca_sha256_fingerprint(system_certificate)}
            ),
        )

        prepared = providers._prepare_claude_tls_environment(
            self.review,
            {
                providers.CLAUDE_CERT_STORE_ENV: "bundled,system",
                "NODE_OPTIONS": "--use-system-ca --use-openssl-ca",
                "NODE_TLS_REJECT_UNAUTHORIZED": "0",
            },
            trust_material=trust_material,
        )

        self.assertEqual(
            prepared[providers.CLAUDE_CERT_STORE_ENV],
            providers.CLAUDE_CERT_STORE,
        )
        for key in providers.CLAUDE_TLS_BYPASS_ENV_KEYS:
            self.assertNotIn(key, prepared)
        self.assertEqual(
            {prepared[key] for key in providers.CLAUDE_TLS_FILE_ENV_KEYS},
            {prepared["SSL_CERT_FILE"]},
        )

    def test_claude_prepared_bundle_uses_complete_bundle_limit(self) -> None:
        certificate = self.sample_ca_certificate()
        ca_root = self.review.container_dir / "claude-ca"
        ca_root.mkdir(mode=0o700)
        bundle = ca_root / providers.CLAUDE_CA_BUNDLE_NAME
        bundle.write_bytes(certificate)
        bundle.chmod(0o600)
        env = {key: str(bundle) for key in providers.CLAUDE_TLS_FILE_ENV_KEYS}
        env[providers.CLAUDE_CERT_STORE_ENV] = providers.CLAUDE_CERT_STORE

        with (
            mock.patch.object(
                providers,
                "CLAUDE_CALLER_CA_SNAPSHOT_LIMIT_BYTES",
                len(certificate) - 1,
            ),
            mock.patch.object(
                providers,
                "CLAUDE_CA_BUNDLE_LIMIT_BYTES",
                len(certificate),
            ),
        ):
            self.assertTrue(
                providers._is_claude_tls_environment_prepared(self.review, env)
            )

    def test_claude_ca_merge_counts_canonical_pem_bytes(self) -> None:
        certificate = self.sample_ca_certificate()
        lines = certificate.strip().splitlines()
        compact = b"\n".join((lines[0], b"".join(lines[1:-1]), lines[-1])) + b"\n"
        canonical = providers._canonical_ca_certificate(
            compact,
            source="compact fixture",
        )[1]
        self.assertGreater(len(canonical), len(compact))

        with self.assertRaisesRegex(ReviewError, "exceeds the size limit"):
            providers._merge_ca_certificates(
                (("compact fixture", compact),),
                limit_bytes=len(canonical) - 1,
                label="Claude caller CA snapshot",
            )

        self.assertEqual(
            providers._merge_ca_certificates(
                (("compact fixture", compact),),
                limit_bytes=len(canonical),
                label="Claude caller CA snapshot",
            ),
            canonical,
        )

    def test_claude_ca_output_budgets_cover_pem_normalization(self) -> None:
        self.assertEqual(
            providers.CLAUDE_CALLER_CA_INPUT_LIMIT_BYTES,
            8 * 1024 * 1024,
        )
        self.assertEqual(
            providers.CLAUDE_CALLER_CA_SNAPSHOT_LIMIT_BYTES,
            providers.CLAUDE_CALLER_CA_INPUT_LIMIT_BYTES
            + (providers.CLAUDE_CALLER_CA_INPUT_LIMIT_BYTES + 31) // 32,
        )
        self.assertEqual(
            providers.CLAUDE_CA_BUNDLE_LIMIT_BYTES,
            providers.CLAUDE_CA_BUNDLE_INPUT_LIMIT_BYTES
            + (providers.CLAUDE_CA_BUNDLE_INPUT_LIMIT_BYTES + 31) // 32,
        )
        self.assertLess(providers.CLAUDE_CA_BUNDLE_LIMIT_BYTES, 32 * 1024 * 1024)

    def test_claude_ca_merge_converts_memory_exhaustion_to_review_error(
        self,
    ) -> None:
        with (
            mock.patch.object(
                providers,
                "_extract_ca_certificates",
                side_effect=MemoryError("fixture exhaustion"),
            ),
            self.assertRaisesRegex(ReviewError, "bounded memory budget"),
        ):
            providers._merge_ca_certificates(
                (("fixture", self.sample_ca_certificate()),),
                limit_bytes=providers.CLAUDE_CA_BUNDLE_LIMIT_BYTES,
                label="Claude review CA bundle",
            )

    def test_claude_tls_preparation_bounds_aggregate_caller_material(self) -> None:
        first, second = self.sample_ca_certificates(2)
        first_path = self.review.source_root / "first-caller.pem"
        second_path = self.review.source_root / "second-caller.pem"
        first_path.write_bytes(first + b"\n" + b"first-padding" * 512)
        second_path.write_bytes(second + b"\n" + b"second-padding" * 512)
        canonical_size = sum(
            len(
                providers._canonical_ca_certificate(
                    certificate,
                    source="caller fixture",
                )[1]
            )
            for certificate in (first, second)
        )
        raw_size = first_path.stat().st_size + second_path.stat().st_size
        self.assertLess(canonical_size, raw_size - 1)

        with (
            mock.patch.object(
                providers,
                "CLAUDE_CALLER_CA_INPUT_LIMIT_BYTES",
                raw_size - 1,
            ),
            self.assertRaisesRegex(ReviewError, "aggregate limit"),
        ):
            providers._prepare_claude_tls_environment(
                self.review,
                {
                    "CURL_CA_BUNDLE": str(first_path),
                    "GIT_SSL_CAINFO": str(second_path),
                },
                trust_material=providers.ClaudeTrustMaterial(
                    certificates=b"",
                    excluded_sha1_fingerprints=frozenset(),
                    bundled_root_sha256_fingerprints=frozenset(),
                ),
            )

        ca_root = self.review.container_dir / "claude-ca"
        self.assertFalse((ca_root / providers.CLAUDE_CA_BUNDLE_NAME).exists())
        self.assertFalse((ca_root / providers.CLAUDE_CALLER_CA_SNAPSHOT_NAME).exists())

    def test_claude_tls_preparation_converts_input_memory_exhaustion(self) -> None:
        caller_path = self.review.source_root / "caller.pem"
        caller_path.write_bytes(self.sample_ca_certificate())
        with (
            mock.patch.object(
                providers,
                "_read_ca_source_with_size",
                side_effect=MemoryError("fixture exhaustion"),
            ),
            self.assertRaisesRegex(ReviewError, "bounded memory budget"),
        ):
            providers._prepare_claude_tls_environment(
                self.review,
                {"SSL_CERT_FILE": str(caller_path)},
                trust_material=providers.ClaudeTrustMaterial(
                    certificates=b"",
                    excluded_sha1_fingerprints=frozenset(),
                    bundled_root_sha256_fingerprints=frozenset(),
                ),
            )

    def test_claude_tls_aggregate_counts_non_certificate_directory_bytes(
        self,
    ) -> None:
        file_certificate, directory_certificate = self.sample_ca_certificates(2)
        caller_path = self.review.source_root / "caller.pem"
        caller_path.write_bytes(file_certificate + b"\n" + b"file-padding" * 128)
        caller_dir = self.review.source_root / "caller-ca"
        caller_dir.mkdir()
        ignored_path = caller_dir / "a-ignored.txt"
        ignored_path.write_bytes(b"not-a-certificate" * 256)
        directory_path = caller_dir / "b-certificate.pem"
        directory_path.write_bytes(directory_certificate)
        raw_size = sum(
            path.stat().st_size for path in (caller_path, ignored_path, directory_path)
        )

        with (
            mock.patch.object(
                providers,
                "CLAUDE_CALLER_CA_INPUT_LIMIT_BYTES",
                raw_size - 1,
            ),
            self.assertRaisesRegex(ReviewError, "aggregate limit"),
        ):
            providers._prepare_claude_tls_environment(
                self.review,
                {
                    "SSL_CERT_FILE": str(caller_path),
                    "SSL_CERT_DIR": str(caller_dir),
                },
                trust_material=providers.ClaudeTrustMaterial(
                    certificates=b"",
                    excluded_sha1_fingerprints=frozenset(),
                    bundled_root_sha256_fingerprints=frozenset(),
                ),
            )

        ca_root = self.review.container_dir / "claude-ca"
        self.assertFalse((ca_root / providers.CLAUDE_CA_BUNDLE_NAME).exists())
        self.assertFalse((ca_root / providers.CLAUDE_CALLER_CA_SNAPSHOT_NAME).exists())

    def test_claude_preflight_records_blocked_system_private_key_before_exclusions(
        self,
    ) -> None:
        self.claude_system_ca.write_bytes(
            b"-----BEGIN PRIVATE KEY-----\nfixture\n-----END PRIVATE KEY-----\n"
        )
        with (
            mock.patch.object(
                providers,
                "_read_claude_trust_certificates_impl",
            ) as read_impl,
            self.assertRaisesRegex(
                providers.ClaudeTrustPolicyUnavailable,
                "system CA baseline is invalid",
            ),
        ):
            self.preflight_claude_trust_policy(self.review)

        read_impl.assert_not_called()
        evidence = json.loads(
            (
                self.review.container_dir / providers.CLAUDE_TRUST_POLICY_EVIDENCE_NAME
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(evidence["status"], "blocked")
        self.assertEqual(evidence["additional_root_resolution"], "blocked")

    def test_claude_trust_evidence_completes_only_after_bundle_validation(
        self,
    ) -> None:
        self.trust.side_effect = self.read_claude_trust_certificates
        with mock.patch.object(
            providers,
            "_read_claude_trust_certificates_impl",
            return_value=providers.ClaudeTrustMaterial(
                certificates=b"",
                excluded_sha1_fingerprints=frozenset(),
                bundled_root_sha256_fingerprints=frozenset(),
            ),
        ):
            trust_material = self.preflight_claude_trust_policy(self.review)

        evidence_path = (
            self.review.container_dir / providers.CLAUDE_TRUST_POLICY_EVIDENCE_NAME
        )
        self.assertEqual(
            json.loads(evidence_path.read_text(encoding="utf-8"))["status"],
            "checking",
        )

        prepared = providers._prepare_claude_tls_environment(
            self.review,
            {},
            trust_material=trust_material,
        )

        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(evidence["status"], "complete")
        self.assertTrue(pathlib.Path(prepared["SSL_CERT_FILE"]).is_file())

    def test_claude_bundle_failure_terminalizes_pending_trust_evidence(
        self,
    ) -> None:
        self.trust.side_effect = self.read_claude_trust_certificates
        with mock.patch.object(
            providers,
            "_read_claude_trust_certificates_impl",
            return_value=providers.ClaudeTrustMaterial(
                certificates=b"",
                excluded_sha1_fingerprints=frozenset(),
                bundled_root_sha256_fingerprints=frozenset(),
            ),
        ):
            trust_material = self.preflight_claude_trust_policy(self.review)
        caller_ca = self.review.source_root / "invalid-caller-ca.pem"
        caller_ca.write_bytes(self.synthetic_private_key_pem())

        with self.assertRaisesRegex(ReviewError, "private key"):
            providers._prepare_claude_tls_environment(
                self.review,
                {"SSL_CERT_FILE": str(caller_ca)},
                trust_material=trust_material,
            )

        evidence = json.loads(
            (
                self.review.container_dir / providers.CLAUDE_TRUST_POLICY_EVIDENCE_NAME
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(evidence["status"], "blocked")
        self.assertFalse(
            (
                self.review.container_dir
                / "claude-ca"
                / providers.CLAUDE_CA_BUNDLE_NAME
            ).exists()
        )

    def test_claude_tls_internal_material_terminalizes_unexpected_failure(
        self,
    ) -> None:
        self.trust.side_effect = self.read_claude_trust_certificates
        with (
            mock.patch.object(
                providers,
                "_read_claude_trust_certificates_impl",
                return_value=providers.ClaudeTrustMaterial(
                    certificates=b"",
                    excluded_sha1_fingerprints=frozenset(),
                    bundled_root_sha256_fingerprints=frozenset(),
                ),
            ),
            mock.patch.object(
                providers,
                "_validate_ca_file",
                side_effect=RuntimeError("fixture failure"),
            ),
            self.assertRaisesRegex(RuntimeError, "fixture failure"),
        ):
            providers._prepare_claude_tls_environment(self.review, {})

        evidence = json.loads(
            (
                self.review.container_dir / providers.CLAUDE_TRUST_POLICY_EVIDENCE_NAME
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(evidence["status"], "inconclusive")
        self.assertEqual(evidence["additional_root_resolution"], "inconclusive")
        self.trust.assert_called_once()

    def test_claude_tls_preparation_terminalizes_forwarded_signal(self) -> None:
        trust_material = self.pending_claude_trust_material()
        forwarded = common.ForwardedSignal(signal.SIGTERM)

        with (
            mock.patch.object(
                providers,
                "_prepare_claude_tls_environment_impl",
                side_effect=forwarded,
            ),
            self.assertRaises(common.ForwardedSignal) as raised,
        ):
            providers._prepare_claude_tls_environment(
                self.review,
                {},
                trust_material=trust_material,
            )

        evidence = json.loads(
            (
                self.review.container_dir / providers.CLAUDE_TRUST_POLICY_EVIDENCE_NAME
            ).read_text(encoding="utf-8")
        )
        self.assertIs(raised.exception, forwarded)
        self.assertEqual(evidence["status"], "inconclusive")
        self.assertEqual(evidence["additional_root_resolution"], "inconclusive")

    def test_claude_tls_preparation_terminalizes_unexpected_and_cancellation(
        self,
    ) -> None:
        for label, error in (
            ("unexpected", RuntimeError("fixture failure")),
            ("cancellation", KeyboardInterrupt("fixture cancellation")),
        ):
            with self.subTest(label=label):
                trust_material = self.pending_claude_trust_material()
                with (
                    mock.patch.object(
                        providers,
                        "_prepare_claude_tls_environment_impl",
                        side_effect=error,
                    ),
                    self.assertRaises(type(error)) as raised,
                ):
                    providers._prepare_claude_tls_environment(
                        self.review,
                        {},
                        trust_material=trust_material,
                    )

                evidence = json.loads(
                    (
                        self.review.container_dir
                        / providers.CLAUDE_TRUST_POLICY_EVIDENCE_NAME
                    ).read_text(encoding="utf-8")
                )
                self.assertIs(raised.exception, error)
                self.assertEqual(evidence["status"], "inconclusive")
                self.assertEqual(
                    evidence["additional_root_resolution"],
                    "inconclusive",
                )

    def test_claude_tls_preparation_enforces_canonical_output_budgets(self) -> None:
        system_certificate, caller_certificate, trust_certificate = (
            self.sample_ca_certificates(3)
        )
        caller_lines = caller_certificate.strip().splitlines()
        compact_caller = (
            b"\n".join(
                (
                    caller_lines[0],
                    b"".join(caller_lines[1:-1]),
                    caller_lines[-1],
                )
            )
            + b"\n"
        )
        canonical_system = providers._canonical_ca_certificate(
            system_certificate,
            source="system fixture",
        )[1]
        canonical_caller = providers._canonical_ca_certificate(
            compact_caller,
            source="caller fixture",
        )[1]
        canonical_trust = providers._canonical_ca_certificate(
            trust_certificate,
            source="trust fixture",
        )[1]
        expected_bundle = canonical_system + canonical_trust + canonical_caller
        self.assertGreater(len(canonical_caller), len(compact_caller))
        self.assertLessEqual(
            len(canonical_caller),
            len(compact_caller) + (len(compact_caller) + 31) // 32,
        )
        self.claude_system_ca.write_bytes(system_certificate)
        caller_path = self.review.source_root / "compact-caller.pem"
        caller_path.write_bytes(compact_caller)
        trust_material = providers.ClaudeTrustMaterial(
            certificates=trust_certificate,
            excluded_sha1_fingerprints=frozenset(),
            bundled_root_sha256_fingerprints=frozenset(),
        )
        ca_root = self.review.container_dir / "claude-ca"
        snapshot = ca_root / providers.CLAUDE_CALLER_CA_SNAPSHOT_NAME
        bundle = ca_root / providers.CLAUDE_CA_BUNDLE_NAME

        def reset_ca_root() -> None:
            snapshot.unlink(missing_ok=True)
            bundle.unlink(missing_ok=True)
            ca_root.rmdir()

        with (
            mock.patch.object(
                providers,
                "CLAUDE_CALLER_CA_SNAPSHOT_LIMIT_BYTES",
                len(canonical_caller),
            ),
            mock.patch.object(
                providers,
                "CLAUDE_CA_BUNDLE_LIMIT_BYTES",
                len(expected_bundle),
            ),
        ):
            providers._prepare_claude_tls_environment(
                self.review,
                {"NODE_EXTRA_CA_CERTS": str(caller_path)},
                trust_material=trust_material,
            )

        self.assertEqual(snapshot.read_bytes(), canonical_caller)
        self.assertEqual(bundle.read_bytes(), expected_bundle)
        reset_ca_root()

        with (
            mock.patch.object(
                providers,
                "CLAUDE_CALLER_CA_SNAPSHOT_LIMIT_BYTES",
                len(canonical_caller) - 1,
            ),
            mock.patch.object(
                providers,
                "CLAUDE_CA_BUNDLE_LIMIT_BYTES",
                len(expected_bundle),
            ),
            self.assertRaisesRegex(
                ReviewError,
                "Claude caller CA snapshot exceeds the size limit",
            ),
        ):
            providers._prepare_claude_tls_environment(
                self.review,
                {"NODE_EXTRA_CA_CERTS": str(caller_path)},
                trust_material=trust_material,
            )
        ca_root.rmdir()

        with (
            mock.patch.object(
                providers,
                "CLAUDE_CALLER_CA_SNAPSHOT_LIMIT_BYTES",
                len(canonical_caller),
            ),
            mock.patch.object(
                providers,
                "CLAUDE_CA_BUNDLE_LIMIT_BYTES",
                len(expected_bundle) - 1,
            ),
            self.assertRaisesRegex(
                ReviewError,
                "Claude review CA bundle exceeds the size limit",
            ),
        ):
            providers._prepare_claude_tls_environment(
                self.review,
                {"NODE_EXTRA_CA_CERTS": str(caller_path)},
                trust_material=trust_material,
            )
        ca_root.rmdir()

    def test_claude_final_tls_environment_drops_removed_trust_root(self) -> None:
        system_certificate, caller_certificate, removed_trust_certificate = (
            self.sample_ca_certificates(3)
        )
        self.claude_system_ca.write_bytes(system_certificate)
        caller_path = self.review.source_root / "caller-ca.pem"
        caller_path.write_bytes(caller_certificate)
        self.trust.side_effect = (
            providers.ClaudeTrustMaterial(
                certificates=removed_trust_certificate,
                excluded_sha1_fingerprints=frozenset(),
                bundled_root_sha256_fingerprints=frozenset(),
            ),
            providers.ClaudeTrustMaterial(
                certificates=b"",
                excluded_sha1_fingerprints=frozenset(),
                bundled_root_sha256_fingerprints=frozenset(),
            ),
        )

        prepared = providers._prepare_claude_tls_environment(
            self.review,
            {"NODE_EXTRA_CA_CERTS": str(caller_path)},
        )
        first_bundle_content = pathlib.Path(prepared["SSL_CERT_FILE"]).read_bytes()
        final_env = providers._prepare_claude_tls_environment(
            self.review,
            prepared,
        )

        def fingerprints(data: bytes) -> set[str]:
            return {
                hashlib.sha256(
                    providers._canonical_ca_certificate(
                        block,
                        source="prepared bundle",
                    )[0]
                ).hexdigest()
                for block in providers.CLAUDE_CERTIFICATE_BLOCK.findall(data)
            }

        expected_system = hashlib.sha256(
            providers._canonical_ca_certificate(
                system_certificate,
                source="system fixture",
            )[0]
        ).hexdigest()
        expected_caller = hashlib.sha256(
            providers._canonical_ca_certificate(
                caller_certificate,
                source="caller fixture",
            )[0]
        ).hexdigest()
        removed_trust = hashlib.sha256(
            providers._canonical_ca_certificate(
                removed_trust_certificate,
                source="trust fixture",
            )[0]
        ).hexdigest()
        caller_snapshot = (
            self.review.container_dir
            / "claude-ca"
            / providers.CLAUDE_CALLER_CA_SNAPSHOT_NAME
        )
        first_fingerprints = fingerprints(first_bundle_content)
        final_fingerprints = fingerprints(
            pathlib.Path(final_env["SSL_CERT_FILE"]).read_bytes()
        )

        self.assertIn(removed_trust, first_fingerprints)
        self.assertEqual(
            fingerprints(caller_snapshot.read_bytes()),
            {expected_caller},
        )
        self.assertEqual(final_fingerprints, {expected_system, expected_caller})
        self.assertNotIn(removed_trust, final_fingerprints)
        self.assertEqual(
            {final_env[key] for key in providers.CLAUDE_TLS_FILE_ENV_KEYS},
            {final_env["SSL_CERT_FILE"]},
        )

    def test_claude_trust_accepts_unconditional_roots(self) -> None:
        always_trusted = "A" * 40
        explicitly_empty = "B" * 40
        payload = plistlib.dumps(
            {
                "trustVersion": 1,
                "trustList": {
                    always_trusted: {},
                    explicitly_empty: {"trustSettings": []},
                },
            },
        )

        classified = providers._classify_trust_fingerprints(
            payload,
            domain="admin",
        )

        self.assertEqual(
            classified,
            providers.ClaudeTrustFingerprints(
                unconditional=(always_trusted, explicitly_empty),
                constrained=(),
            ),
        )

    def test_claude_trust_rejects_bool_and_float_version_aliases(self) -> None:
        for version in (True, 1.0):
            with self.subTest(version=version):
                payload = plistlib.dumps(
                    {
                        "trustVersion": version,
                        "trustList": {},
                    }
                )

                with self.assertRaisesRegex(ReviewError, "unsupported format"):
                    providers._classify_trust_fingerprints(
                        payload,
                        domain="user",
                    )

    def test_claude_trust_bounds_entries_before_classification(self) -> None:
        payload = plistlib.dumps(
            {
                "trustVersion": 1,
                "trustList": {
                    f"{index:040X}": {}
                    for index in range(providers.CLAUDE_TRUST_ENTRY_LIMIT + 1)
                },
            }
        )

        with self.assertRaisesRegex(
            providers.ClaudeTrustPolicyUnavailable,
            "trust entry limit",
        ):
            providers._classify_trust_fingerprints(payload, domain="user")

    def test_claude_trust_deny_wins_over_entry_limit(self) -> None:
        trust_list = {
            f"{index:040X}": {}
            for index in range(providers.CLAUDE_TRUST_ENTRY_LIMIT + 1)
        }
        trust_list["F" * 40] = {
            "trustSettings": [{providers.CLAUDE_TRUST_RESULT_KEY: 3}]
        }
        payload = plistlib.dumps(
            {
                "trustVersion": 1,
                "trustList": trust_list,
            }
        )

        with self.assertRaisesRegex(
            providers.ClaudeTrustSettingsDeny,
            "explicit deny",
        ):
            providers._classify_trust_fingerprints(payload, domain="user")

    def test_claude_trust_deny_wins_over_malformed_sibling(self) -> None:
        payload = plistlib.dumps(
            {
                "trustVersion": 1,
                "trustList": {
                    "A" * 40: {"trustSettings": "malformed"},
                    "B" * 40: {
                        "trustSettings": [{providers.CLAUDE_TRUST_RESULT_KEY: 3}]
                    },
                },
            }
        )

        with self.assertRaisesRegex(
            providers.ClaudeTrustSettingsDeny,
            "explicit deny",
        ):
            providers._classify_trust_fingerprints(payload, domain="admin")

    def test_claude_trust_invalid_fingerprint_cannot_impersonate_deny(self) -> None:
        payload = plistlib.dumps(
            {
                "trustVersion": 1,
                "trustList": {
                    "not-a-fingerprint": {
                        "trustSettings": [{providers.CLAUDE_TRUST_RESULT_KEY: 3}]
                    }
                },
            }
        )

        with self.assertRaisesRegex(
            providers.ClaudeTrustPolicyUnavailable,
            "invalid entry",
        ):
            providers._classify_trust_fingerprints(payload, domain="user")

    def test_claude_trust_classifies_user_and_system_policy_fixtures(
        self,
    ) -> None:
        for domain, fixture, expected_constraint in (
            (
                "user",
                "securitytool-user-deny.plist",
                None,
            ),
            (
                "user",
                "securitytool-user-constraint.plist",
                "C" * 40,
            ),
            (
                "system",
                "securitytool-system-deny.plist",
                None,
            ),
            (
                "system",
                "securitytool-system-constraint.plist",
                "B" * 40,
            ),
        ):
            with self.subTest(domain=domain, fixture=fixture):
                if expected_constraint is None:
                    with self.assertRaises(providers.ClaudeTrustSettingsDeny):
                        providers._classify_trust_fingerprints(
                            (FIXTURES / fixture).read_bytes(),
                            domain=domain,
                        )
                else:
                    classified = providers._classify_trust_fingerprints(
                        (FIXTURES / fixture).read_bytes(),
                        domain=domain,
                    )
                    self.assertEqual(classified.unconditional, ())
                    self.assertEqual(
                        classified.constrained,
                        (expected_constraint,),
                    )

    def test_claude_trust_omits_every_non_deny_result(self) -> None:
        fingerprints = tuple(character * 40 for character in "ABC")
        payload = plistlib.dumps(
            {
                "trustVersion": 1,
                "trustList": {
                    fingerprint: {
                        "trustSettings": [{providers.CLAUDE_TRUST_RESULT_KEY: result}]
                    }
                    for fingerprint, result in zip(
                        fingerprints,
                        (1, 2, 4),
                        strict=True,
                    )
                },
            }
        )

        classified = providers._classify_trust_fingerprints(
            payload,
            domain="user",
        )

        self.assertEqual(classified.unconditional, ())
        self.assertEqual(classified.constrained, fingerprints)

    def test_claude_trust_explicit_deny_wins_over_other_constraints(self) -> None:
        payload = plistlib.dumps(
            {
                "trustVersion": 1,
                "trustList": {
                    "A" * 40: {
                        "trustSettings": [
                            {
                                "kSecTrustSettingsPolicyName": "sslServer",
                                "kSecTrustSettingsPolicyString": "example.invalid",
                            }
                        ]
                    },
                    "B" * 40: {
                        "trustSettings": [{providers.CLAUDE_TRUST_RESULT_KEY: 3}]
                    },
                },
            },
        )

        with self.assertRaisesRegex(
            providers.ClaudeTrustSettingsDeny,
            "explicit deny",
        ):
            providers._classify_trust_fingerprints(payload, domain="admin")

    def test_claude_trust_rejects_malformed_constraints(self) -> None:
        payload = plistlib.dumps(
            {
                "trustVersion": 1,
                "trustList": {"A" * 40: {"trustSettings": "always"}},
            },
        )

        with self.assertRaisesRegex(
            providers.ClaudeTrustPolicyUnavailable,
            "invalid constraints",
        ):
            providers._classify_trust_fingerprints(payload, domain="user")

    def test_claude_trust_rejects_non_numeric_and_invalid_results(self) -> None:
        for result in ("3", True, 0, 5):
            with self.subTest(result=result):
                payload = plistlib.dumps(
                    {
                        "trustVersion": 1,
                        "trustList": {
                            "A" * 40: {
                                "trustSettings": [
                                    {providers.CLAUDE_TRUST_RESULT_KEY: result}
                                ]
                            }
                        },
                    }
                )
                with self.assertRaisesRegex(ReviewError, "invalid constraints"):
                    providers._classify_trust_fingerprints(
                        payload,
                        domain="user",
                    )

    def test_claude_trust_rejects_legacy_result_alias(self) -> None:
        payload = plistlib.dumps(
            {
                "trustVersion": 1,
                "trustList": {
                    "A" * 40: {
                        "trustSettings": [
                            {
                                providers.CLAUDE_TRUST_RESULT_KEY: 1,
                                "result": 3,
                            }
                        ]
                    }
                },
            }
        )

        with self.assertRaisesRegex(ReviewError, "ambiguous constraints"):
            providers._classify_trust_fingerprints(payload, domain="user")

    def test_claude_trust_rejects_duplicate_conflicting_result_keys(self) -> None:
        payload = b"""<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
<key>trustVersion</key><integer>1</integer>
<key>trustList</key><dict>
<key>AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA</key><dict>
<key>trustSettings</key><array><dict>
<key>kSecTrustSettingsResult</key><integer>1</integer>
<key>kSecTrustSettingsResult</key><integer>3</integer>
</dict></array></dict></dict></dict></plist>"""

        with self.assertRaisesRegex(ReviewError, "invalid"):
            providers._classify_trust_fingerprints(payload, domain="user")

    def test_claude_trust_selects_only_requested_certificates(self) -> None:
        requested = self.strict_root_certificate()
        ignored = self.sample_ca_certificate()
        der, canonical = providers._canonical_ca_certificate(
            requested,
            source="requested fixture",
        )
        fingerprint = (
            hashlib.sha1(
                der,
                usedforsecurity=False,
            )
            .hexdigest()
            .upper()
        )

        selected = providers._select_trust_certificates(
            (("combined fixture", ignored + requested),),
            (fingerprint,),
            ca_root=self.review.container_dir,
        )

        self.assertEqual(selected.certificates, canonical)
        self.assertEqual(selected.omitted_sha1_fingerprints, frozenset())

    def test_claude_trust_omits_non_root_and_expired_certificates(self) -> None:
        for fixture in (
            "trust-root-non-ca.pem",
            "trust-root-bad-key-usage.pem",
            "trust-root-expired.pem",
        ):
            with self.subTest(fixture=fixture):
                certificate = (FIXTURES / fixture).read_bytes()
                der, _ = providers._canonical_ca_certificate(
                    certificate,
                    source=fixture,
                )
                fingerprint = (
                    hashlib.sha1(
                        der,
                        usedforsecurity=False,
                    )
                    .hexdigest()
                    .upper()
                )

                selected = providers._select_trust_certificates(
                    ((fixture, certificate),),
                    (fingerprint,),
                    ca_root=self.review.container_dir,
                )

                self.assertEqual(selected.certificates, b"")
                self.assertEqual(
                    selected.omitted_sha1_fingerprints,
                    frozenset({fingerprint}),
                )

    def test_claude_trust_omits_expired_root_with_discovered_openssl(self) -> None:
        executable = shutil.which("openssl", path=providers.TRUSTED_PATH)
        if executable is None:
            self.skipTest("requires an OpenSSL executable on the trusted path")
        certificate = (FIXTURES / "trust-root-expired.pem").read_bytes()
        der, _ = providers._canonical_ca_certificate(
            certificate,
            source="trust-root-expired.pem",
        )
        fingerprint = hashlib.sha1(der, usedforsecurity=False).hexdigest().upper()

        with mock.patch.object(
            providers,
            "CLAUDE_OPENSSL_CLIENT",
            pathlib.Path(executable),
        ):
            selected = providers._select_trust_certificates(
                (("trust-root-expired.pem", certificate),),
                (fingerprint,),
                ca_root=self.review.container_dir,
            )

        self.assertEqual(selected.certificates, b"")
        self.assertEqual(
            selected.omitted_sha1_fingerprints,
            frozenset({fingerprint}),
        )

    def test_claude_trust_omits_missing_additional_certificate(self) -> None:
        fingerprint = "A" * 40

        selected = providers._select_trust_certificates(
            (),
            (fingerprint,),
            ca_root=self.review.container_dir,
        )

        self.assertEqual(selected.certificates, b"")
        self.assertEqual(
            selected.omitted_sha1_fingerprints,
            frozenset({fingerprint}),
        )

    def test_claude_trust_does_not_omit_verification_runtime_failure(self) -> None:
        certificate = self.strict_root_certificate()
        der, _ = providers._canonical_ca_certificate(
            certificate,
            source="verification fixture",
        )
        fingerprint = hashlib.sha1(der, usedforsecurity=False).hexdigest().upper()

        with (
            mock.patch.object(
                providers,
                "_verify_unconditional_trust_root",
                side_effect=common.ReviewTimeoutError("fixture timeout"),
            ),
            self.assertRaises(common.ReviewTimeoutError),
        ):
            providers._select_trust_certificates(
                (("verification fixture", certificate),),
                (fingerprint,),
                ca_root=self.review.container_dir,
            )

    def test_claude_trust_classifies_only_explicit_verify_failure_as_invalid(
        self,
    ) -> None:
        certificate = self.strict_root_certificate()
        der, canonical = providers._canonical_ca_certificate(
            certificate,
            source="verification fixture",
        )

        for error_code, error_reason in (
            (10, "certificate has expired"),
            (69, "CA signature digest algorithm too weak"),
        ):
            for prefix, diagnostic_stream in (("", "stderr"), ("error ", "stdout")):
                captures: list[common.BoundedCapture] = []

                def explicit_failure(
                    argv: tuple[str, ...],
                    **_kwargs: object,
                ) -> common.BoundedCapture:
                    diagnostic = (
                        f"{prefix}{argv[-1]}: verification failed: "
                        f"{error_code} ({error_reason})\n"
                    ).encode()
                    capture = common.BoundedCapture(
                        argv=argv,
                        returncode=2,
                        stdout=bytearray(
                            diagnostic
                            if diagnostic_stream == "stdout"
                            else b"bounded stdout"
                        ),
                        stderr=bytearray(
                            diagnostic
                            if diagnostic_stream == "stderr"
                            else b"bounded stderr"
                        ),
                    )
                    captures.append(capture)
                    return capture

                with (
                    self.subTest(
                        error_code=error_code,
                        prefix=prefix,
                        diagnostic_stream=diagnostic_stream,
                    ),
                    mock.patch.object(
                        providers,
                        "run_bounded_capture",
                        side_effect=explicit_failure,
                    ),
                    self.assertRaises(providers.ClaudeTrustCertificateInvalid),
                ):
                    providers._verify_unconditional_trust_root(
                        der,
                        canonical,
                        ca_root=self.review.container_dir,
                    )

                self.assertEqual(len(captures), 1)
                self.assertFalse(any(captures[0].stdout))
                self.assertFalse(any(captures[0].stderr))

    def test_claude_trust_classifies_openssl_verify_failure_as_invalid(
        self,
    ) -> None:
        certificate = self.strict_root_certificate()
        der, canonical = providers._canonical_ca_certificate(
            certificate,
            source="verification fixture",
        )

        for error_code, error_reason in (
            (10, "certificate has expired"),
            (68, "CA signature digest algorithm too weak"),
            (76, "unsupported signature algorithm"),
            (94, "Certificate public key has explicit ECC parameters"),
        ):
            captures: list[common.BoundedCapture] = []

            def explicit_failure(
                argv: tuple[str, ...],
                **_kwargs: object,
            ) -> common.BoundedCapture:
                capture = common.BoundedCapture(
                    argv=argv,
                    returncode=2,
                    stdout=bytearray(
                        b"CN=Verification Fixture\n"
                        + (
                            f"error {error_code} at 0 depth lookup: {error_reason}\n"
                        ).encode()
                    ),
                    stderr=bytearray(
                        f"error {argv[-1]}: verification failed\n".encode()
                    ),
                )
                captures.append(capture)
                return capture

            with (
                self.subTest(error_code=error_code),
                mock.patch.object(
                    providers,
                    "run_bounded_capture",
                    side_effect=explicit_failure,
                ),
                self.assertRaises(providers.ClaudeTrustCertificateInvalid),
            ):
                providers._verify_unconditional_trust_root(
                    der,
                    canonical,
                    ca_root=self.review.container_dir,
                )

            self.assertEqual(len(captures), 1)
            self.assertFalse(any(captures[0].stdout))
            self.assertFalse(any(captures[0].stderr))

    def test_claude_trust_verify_uses_safe_relative_certificate_path(self) -> None:
        certificate = self.strict_root_certificate()
        der, canonical = providers._canonical_ca_certificate(
            certificate,
            source="verification fixture",
        )
        ca_root = self.review.container_dir / "trust\nroot"
        ca_root.mkdir()
        captures: list[common.BoundedCapture] = []

        def explicit_failure(
            argv: tuple[str, ...],
            **kwargs: object,
        ) -> common.BoundedCapture:
            self.assertEqual(kwargs["cwd"], ca_root)
            self.assertNotIn("/", argv[-1])
            self.assertNotIn("\n", argv[-1])
            self.assertEqual(argv[-2], argv[-1])
            self.assertEqual(
                kwargs["env"],
                {
                    "LANG": "C",
                    "LC_ALL": "C",
                    "PATH": providers.TRUSTED_PATH,
                    "SSL_CERT_FILE": argv[-1],
                    "SSL_CERT_DIR": ".",
                },
            )
            diagnostic = (
                f"{argv[-1]}: verification failed: 10 (certificate has expired)\n"
            ).encode()
            capture = common.BoundedCapture(
                argv=argv,
                returncode=2,
                stdout=bytearray(),
                stderr=bytearray(diagnostic),
            )
            captures.append(capture)
            return capture

        with (
            mock.patch.object(
                providers,
                "run_bounded_capture",
                side_effect=explicit_failure,
            ),
            self.assertRaises(providers.ClaudeTrustCertificateInvalid),
        ):
            providers._verify_unconditional_trust_root(
                der,
                canonical,
                ca_root=ca_root,
            )

        self.assertEqual(len(captures), 1)
        self.assertFalse(any(captures[0].stdout))
        self.assertFalse(any(captures[0].stderr))

    def test_claude_trust_does_not_treat_verify_tool_failures_as_invalid(
        self,
    ) -> None:
        certificate = self.strict_root_certificate()
        der, canonical = providers._canonical_ca_certificate(
            certificate,
            source="verification fixture",
        )
        cases = (
            "non-two-exact-diagnostic",
            "wrong-path",
            "path-prefix",
            "path-bound-unspecified",
            "path-bound-out-of-memory",
            "path-bound-application-verification",
            "path-bound-invalid-call",
            "path-bound-store-lookup",
            "internal-error",
            "signal",
        )

        for case in cases:
            captures: list[common.BoundedCapture] = []

            def tool_failure(
                argv: tuple[str, ...],
                **_kwargs: object,
            ) -> common.BoundedCapture:
                path = argv[-1]
                if case == "non-two-exact-diagnostic":
                    returncode = 1
                    diagnostic = (
                        f"{path}: verification failed: 10 (certificate has expired)\n"
                    ).encode()
                elif case == "wrong-path":
                    returncode = 2
                    diagnostic = (
                        f"{path}.other: verification failed: "
                        "10 (certificate has expired)\n"
                    ).encode()
                elif case == "path-prefix":
                    returncode = 2
                    diagnostic = (
                        f"prefix-{path}: verification failed: "
                        "10 (certificate has expired)\n"
                    ).encode()
                elif case == "path-bound-unspecified":
                    returncode = 2
                    diagnostic = (
                        f"{path}: verification failed: "
                        "1 (unspecified certificate verification error)\n"
                    ).encode()
                elif case == "path-bound-out-of-memory":
                    returncode = 2
                    diagnostic = (
                        f"{path}: verification failed: 17 (out of memory)\n"
                    ).encode()
                elif case == "path-bound-application-verification":
                    returncode = 2
                    diagnostic = (
                        f"{path}: verification failed: "
                        "50 (application verification failure)\n"
                    ).encode()
                elif case == "path-bound-invalid-call":
                    returncode = 2
                    diagnostic = (
                        f"{path}: verification failed: 65 (invalid call)\n"
                    ).encode()
                elif case == "path-bound-store-lookup":
                    returncode = 2
                    diagnostic = (
                        f"{path}: verification failed: 66 (store lookup error)\n"
                    ).encode()
                elif case == "internal-error":
                    returncode = 2
                    diagnostic = b"internal verification engine failure\n"
                else:
                    returncode = -int(signal.SIGTERM)
                    diagnostic = b"terminated verification runtime\n"
                capture = common.BoundedCapture(
                    argv=argv,
                    returncode=returncode,
                    stdout=bytearray(b"bounded stdout"),
                    stderr=bytearray(diagnostic),
                )
                captures.append(capture)
                return capture

            with (
                self.subTest(case=case),
                mock.patch.object(
                    providers,
                    "run_bounded_capture",
                    side_effect=tool_failure,
                ),
                self.assertRaises(providers.ClaudeTrustToolUnavailable),
            ):
                providers._verify_unconditional_trust_root(
                    der,
                    canonical,
                    ca_root=self.review.container_dir,
                )

            self.assertEqual(len(captures), 1)
            self.assertFalse(any(captures[0].stdout))
            self.assertFalse(any(captures[0].stderr))

    def test_claude_trust_rejects_openssl_internal_or_unbound_failures(
        self,
    ) -> None:
        certificate = self.strict_root_certificate()
        der, canonical = providers._canonical_ca_certificate(
            certificate,
            source="verification fixture",
        )
        cases = (
            ("unspecified", 1, "unspecified certificate verification error", "exact"),
            ("out-of-memory", 17, "out of memory", "exact"),
            ("application", 50, "application verification failure", "exact"),
            ("invalid-call", 69, "invalid call", "exact"),
            ("store-lookup", 70, "store lookup error", "exact"),
            ("wrong-path", 10, "certificate has expired", "wrong"),
            ("missing-code", None, "", "exact"),
        )

        for case, error_code, error_reason, path_kind in cases:
            captures: list[common.BoundedCapture] = []

            def tool_failure(
                argv: tuple[str, ...],
                **_kwargs: object,
            ) -> common.BoundedCapture:
                path = argv[-1] if path_kind == "exact" else f"{argv[-1]}.other"
                code_line = (
                    b""
                    if error_code is None
                    else (
                        f"error {error_code} at 0 depth lookup: {error_reason}\n"
                    ).encode()
                )
                capture = common.BoundedCapture(
                    argv=argv,
                    returncode=2,
                    stdout=bytearray(code_line),
                    stderr=bytearray(f"error {path}: verification failed\n".encode()),
                )
                captures.append(capture)
                return capture

            with (
                self.subTest(case=case),
                mock.patch.object(
                    providers,
                    "run_bounded_capture",
                    side_effect=tool_failure,
                ),
                self.assertRaises(providers.ClaudeTrustToolUnavailable),
            ):
                providers._verify_unconditional_trust_root(
                    der,
                    canonical,
                    ca_root=self.review.container_dir,
                )

            self.assertEqual(len(captures), 1)
            self.assertFalse(any(captures[0].stdout))
            self.assertFalse(any(captures[0].stderr))

    def test_claude_trust_bounds_additional_root_count(self) -> None:
        with self.assertRaisesRegex(
            providers.ClaudeTrustPolicyUnavailable,
            "verification limit",
        ):
            providers._select_trust_certificates(
                (),
                ("A" * 40,) * (providers.CLAUDE_ADDITIONAL_TRUST_ROOT_LIMIT + 1),
                ca_root=self.review.container_dir,
            )

    def test_claude_trust_export_unavailable_matches_exact_diagnostic(self) -> None:
        self.assertIsInstance(providers.CLAUDE_TRUST_EXPORT_UNAVAILABLE, tuple)
        self.assertEqual(len(providers.CLAUDE_TRUST_EXPORT_UNAVAILABLE), 1)
        diagnostic = providers.CLAUDE_TRUST_EXPORT_UNAVAILABLE[0]

        self.assertTrue(providers._is_trust_export_unavailable(diagnostic))
        self.assertFalse(
            providers._is_trust_export_unavailable(
                "SecTrustSettingsCreateExternalRepresentation: malformed fixture"
            )
        )

    def test_claude_trust_bounds_total_root_verification_time(self) -> None:
        certificate = self.strict_root_certificate()
        der, _ = providers._canonical_ca_certificate(
            certificate,
            source="deadline fixture",
        )
        fingerprint = hashlib.sha1(der, usedforsecurity=False).hexdigest().upper()

        with (
            mock.patch.object(
                providers.time,
                "monotonic",
                side_effect=(100.0, 131.0),
            ),
            mock.patch.object(
                providers,
                "_verify_unconditional_trust_root",
            ) as verify,
            self.assertRaisesRegex(
                common.ReviewTimeoutError,
                "exceeded its deadline",
            ),
        ):
            providers._select_trust_certificates(
                (("deadline fixture", certificate),),
                (fingerprint,),
                ca_root=self.review.container_dir,
            )

        verify.assert_not_called()

    def test_claude_trust_no_settings_error_is_exact(self) -> None:
        for message in providers.CLAUDE_TRUST_NO_SETTINGS:
            with self.subTest(message=message):
                self.assertTrue(providers._is_no_trust_settings(message))
                self.assertTrue(providers._is_no_trust_settings(f"security: {message}"))
        for fixture in (
            "securitytool-no-user-trust-settings.stderr",
            "securitytool-no-trust-settings.stderr",
            "securitytool-no-system-trust-settings.stderr",
        ):
            with self.subTest(fixture=fixture):
                real_output = (FIXTURES / fixture).read_text(encoding="utf-8")
                self.assertTrue(providers._is_no_trust_settings(real_output))
        self.assertFalse(
            providers._is_no_trust_settings(
                "permission denied: No Trust Settings were found."
            )
        )

    def test_claude_trust_missing_security_tool_is_capability_unavailable(
        self,
    ) -> None:
        missing_security = self.review.container_dir / "missing-security"
        ca_root = self.review.container_dir / "claude-ca"
        ca_root.mkdir()

        with (
            mock.patch.object(
                providers,
                "CLAUDE_KEYCHAIN_CLIENT",
                missing_security,
            ),
            self.assertRaisesRegex(
                providers.ClaudeTrustToolUnavailable,
                "trust export tool",
            ),
        ):
            self.read_claude_trust_certificates(self.review, ca_root)

        self.assertFalse(missing_security.exists())

    @mock.patch.object(providers, "run_bounded_capture")
    def test_claude_trust_accepts_published_security_help(
        self,
        run_command: mock.Mock,
    ) -> None:
        ca_root = self.review.container_dir / "claude-ca"
        ca_root.mkdir()
        capture = common.BoundedCapture(
            argv=(str(self.claude_keychain_client), "help", "trust-settings-export"),
            returncode=0,
            stdout=bytearray(
                (
                    "\n".join(providers.CLAUDE_TRUST_EXPORT_PUBLISHED_HELP_LINES) + "\n"
                ).encode()
            ),
            stderr=bytearray(),
        )
        run_command.return_value = capture

        client, environment = providers._require_claude_trust_export_tool(
            self.review,
            ca_root,
        )

        self.assertEqual(client, self.claude_keychain_client)
        self.assertEqual(environment["LANG"], "C")
        self.assertEqual(environment["LC_ALL"], "C")
        self.assertFalse(any(capture.stdout))
        self.assertFalse(any(capture.stderr))

    @mock.patch.object(providers, "run_bounded_capture")
    def test_claude_trust_rejects_non_exact_security_help(
        self,
        run_command: mock.Mock,
    ) -> None:
        ca_root = self.review.container_dir / "claude-ca"
        ca_root.mkdir()
        published = providers.CLAUDE_TRUST_EXPORT_PUBLISHED_HELP_LINES
        cases = (
            (
                "mutated",
                tuple(
                    line.replace("Export admin", "Import admin") for line in published
                ),
            ),
            ("extra", (*published, "-x Unknown future behavior")),
            ("duplicate", (published[0], *published)),
            (
                "mixed",
                (
                    published[0],
                    providers.CLAUDE_TRUST_EXPORT_HELP_LINES[1],
                    published[2],
                ),
            ),
        )

        for case, lines in cases:
            capture = common.BoundedCapture(
                argv=(
                    str(self.claude_keychain_client),
                    "help",
                    "trust-settings-export",
                ),
                returncode=0,
                stdout=bytearray(("\n".join(lines) + "\n").encode()),
                stderr=bytearray(),
            )
            run_command.return_value = capture

            with (
                self.subTest(case=case),
                self.assertRaises(providers.ClaudeTrustToolUnavailable),
            ):
                providers._require_claude_trust_export_tool(
                    self.review,
                    ca_root,
                )

            self.assertFalse(any(capture.stdout))
            self.assertFalse(any(capture.stderr))

    @mock.patch.object(providers, "run_bounded_capture")
    def test_claude_trust_queries_every_domain_with_exact_bounded_commands(
        self,
        run_command: mock.Mock,
    ) -> None:
        fixture_names = (
            "securitytool-no-user-trust-settings.stderr",
            "securitytool-no-trust-settings.stderr",
            "securitytool-no-system-trust-settings.stderr",
        )
        completed: list[common.BoundedCapture] = []

        def no_settings(
            argv: tuple[str, ...],
            **_kwargs: object,
        ) -> common.BoundedCapture:
            if argv[1:3] == ("help", "trust-settings-export"):
                return self.trust_export_help_capture(argv)
            output = (FIXTURES / fixture_names[len(completed)]).read_bytes()
            result = common.BoundedCapture(
                argv=argv,
                returncode=1,
                stdout=bytearray(),
                stderr=bytearray(output),
            )
            completed.append(result)
            return result

        run_command.side_effect = no_settings
        ca_root = self.review.container_dir / "claude-ca"
        ca_root.mkdir()

        material = self.read_claude_trust_certificates(
            self.review,
            ca_root,
        )

        self.assertEqual(
            material,
            providers.ClaudeTrustMaterial(
                certificates=b"",
                excluded_sha1_fingerprints=frozenset(),
                bundled_root_sha256_fingerprints=frozenset(),
            ),
        )
        self.assertEqual(
            [call.args[0] for call in run_command.call_args_list],
            [
                (
                    str(self.claude_keychain_client),
                    "help",
                    "trust-settings-export",
                ),
                (
                    str(self.claude_keychain_client),
                    "trust-settings-export",
                    str(ca_root / ".user-trust.plist"),
                ),
                (
                    str(self.claude_keychain_client),
                    "trust-settings-export",
                    "-d",
                    str(ca_root / ".admin-trust.plist"),
                ),
                (
                    str(self.claude_keychain_client),
                    "trust-settings-export",
                    "-s",
                    str(ca_root / ".system-trust.plist"),
                ),
            ],
        )
        for index, call in enumerate(run_command.call_args_list):
            self.assertEqual(
                call.kwargs["timeout_seconds"],
                providers.CLAUDE_KEYCHAIN_QUERY_TIMEOUT_SECONDS,
            )
            self.assertEqual(
                call.kwargs["stdout_limit_bytes"],
                providers.CLAUDE_KEYCHAIN_BROKER_OUTPUT_LIMIT_BYTES,
            )
            self.assertEqual(
                call.kwargs["stderr_limit_bytes"],
                providers.CLAUDE_KEYCHAIN_BROKER_OUTPUT_LIMIT_BYTES,
            )
            if index == 0:
                self.assertNotIn("regular_file_limit_bytes", call.kwargs)
            else:
                self.assertEqual(
                    call.kwargs["regular_file_limit_bytes"],
                    providers.CLAUDE_TRUST_SETTINGS_LIMIT_BYTES,
                )
                self.assertEqual(
                    call.kwargs["regular_file_limit_path"],
                    pathlib.Path(call.args[0][-1]),
                )
        for result in completed:
            self.assertEqual(result.stdout, bytearray())
            self.assertFalse(any(result.stderr))
        for domain, _options in providers.CLAUDE_TRUST_DOMAINS:
            self.assertFalse((ca_root / f".{domain}-trust.plist").exists())
        evidence = json.loads(
            (
                self.review.container_dir / providers.CLAUDE_TRUST_POLICY_EVIDENCE_NAME
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            evidence["policy"],
            "require-bundled-root-subset",
        )
        self.assertEqual(evidence["distinct_constrained_omitted_count"], 0)
        self.assertEqual(
            [item["status"] for item in evidence["domains"]],
            ["no-settings", "no-settings", "no-settings"],
        )

    def test_claude_trust_unlinks_raw_export_on_every_failure_stage(self) -> None:
        raw_trust = b"private trust settings fixture"
        cases = (
            ("command", providers.ClaudeTrustToolUnavailable, "unavailable"),
            ("status", ReviewError, "blocked"),
            ("parse", ReviewError, "blocked"),
        )
        for failure_stage, expected_error, expected_status in cases:
            with self.subTest(failure_stage=failure_stage):
                ca_root = self.review.container_dir / f"claude-ca-{failure_stage}"
                ca_root.mkdir()
                trust_path = ca_root / ".user-trust.plist"

                def fail_export(
                    argv: tuple[str, ...],
                    **_kwargs: object,
                ) -> common.BoundedCapture:
                    if argv[1:3] == ("help", "trust-settings-export"):
                        return self.trust_export_help_capture(argv)
                    pathlib.Path(argv[-1]).write_bytes(raw_trust)
                    if failure_stage == "command":
                        raise OSError("fixture process failure")
                    return common.BoundedCapture(
                        argv=argv,
                        returncode=1 if failure_stage == "status" else 0,
                        stdout=bytearray(),
                        stderr=bytearray(b"private status detail"),
                    )

                with (
                    mock.patch.object(
                        providers,
                        "run_bounded_capture",
                        side_effect=fail_export,
                    ),
                    self.assertRaises(expected_error),
                ):
                    self.read_claude_trust_certificates(self.review, ca_root)

                self.assertFalse(trust_path.exists())
                retained = b"".join(
                    path.read_bytes() for path in ca_root.rglob("*") if path.is_file()
                )
                self.assertNotIn(raw_trust, retained)
                evidence_text = (
                    self.review.container_dir
                    / providers.CLAUDE_TRUST_POLICY_EVIDENCE_NAME
                ).read_text(encoding="utf-8")
                evidence = json.loads(evidence_text)
                self.assertEqual(evidence["status"], expected_status)
                self.assertNotIn("private trust settings fixture", evidence_text)
                self.assertNotIn("private status detail", evidence_text)

    @mock.patch.object(providers, "run_bounded_capture")
    def test_oversized_trust_exports_are_policy_blocks(
        self,
        run_command: mock.Mock,
    ) -> None:
        def oversized(
            argv: tuple[str, ...],
            **_kwargs: object,
        ) -> common.BoundedCapture:
            if argv[1:3] == ("help", "trust-settings-export"):
                return self.trust_export_help_capture(argv)
            raise common.ReviewOutputLimitError("fixture output limit")

        run_command.side_effect = oversized
        ca_root = self.review.container_dir / "claude-ca-oversized"
        ca_root.mkdir()

        with self.assertRaisesRegex(
            providers.ClaudeTrustPolicyUnavailable,
            "inspection limit",
        ):
            self.read_claude_trust_certificates(self.review, ca_root)

        evidence = json.loads(
            (
                self.review.container_dir / providers.CLAUDE_TRUST_POLICY_EVIDENCE_NAME
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(evidence["status"], "blocked")
        self.assertEqual(
            [item["status"] for item in evidence["domains"]],
            ["blocked", "blocked", "blocked"],
        )

    @mock.patch.object(providers, "run_bounded_capture")
    def test_deferred_policy_block_wins_over_later_tool_unavailability(
        self,
        run_command: mock.Mock,
    ) -> None:
        malformed = plistlib.dumps(
            {
                "trustVersion": 1,
                "trustList": {"A" * 40: {"trustSettings": "invalid"}},
            }
        )

        def malformed_then_unavailable(
            argv: tuple[str, ...],
            **_kwargs: object,
        ) -> common.BoundedCapture:
            if argv[1:3] == ("help", "trust-settings-export"):
                return self.trust_export_help_capture(argv)
            if "-d" not in argv:
                pathlib.Path(argv[-1]).write_bytes(malformed)
                return common.BoundedCapture(
                    argv=argv,
                    returncode=0,
                    stdout=bytearray(),
                    stderr=bytearray(),
                )
            return common.BoundedCapture(
                argv=argv,
                returncode=1,
                stdout=bytearray(),
                stderr=bytearray(
                    (providers.CLAUDE_TRUST_EXPORT_UNAVAILABLE[0] + "\n").encode()
                ),
            )

        run_command.side_effect = malformed_then_unavailable
        ca_root = self.review.container_dir / "claude-ca-deferred"
        ca_root.mkdir()

        with self.assertRaises(providers.ClaudeTrustPolicyUnavailable):
            self.read_claude_trust_certificates(self.review, ca_root)

        evidence = json.loads(
            (
                self.review.container_dir / providers.CLAUDE_TRUST_POLICY_EVIDENCE_NAME
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(evidence["status"], "blocked")
        self.assertEqual(
            [item["status"] for item in evidence["domains"]],
            ["blocked", "unavailable"],
        )

    @mock.patch.object(providers, "run_bounded_capture")
    def test_claude_trust_exports_certificates_only_after_all_domains_pass(
        self,
        run_command: mock.Mock,
    ) -> None:
        certificate = self.strict_root_certificate()
        der, canonical = providers._canonical_ca_certificate(
            certificate,
            source="trusted fixture",
        )
        fingerprint = (
            hashlib.sha1(
                der,
                usedforsecurity=False,
            )
            .hexdigest()
            .upper()
        )
        user_settings = plistlib.dumps(
            {
                "trustVersion": 1,
                "trustList": {fingerprint: {"trustSettings": []}},
            }
        )
        empty_settings = plistlib.dumps(
            {"trustVersion": 1, "trustList": {}},
        )

        def complete(
            argv: tuple[str, ...],
            **_kwargs: object,
        ) -> common.BoundedCapture:
            if argv[1:3] == ("help", "trust-settings-export"):
                return self.trust_export_help_capture(argv)
            if argv[1] == "trust-settings-export":
                pathlib.Path(argv[-1]).write_bytes(
                    user_settings
                    if "-d" not in argv and "-s" not in argv
                    else empty_settings
                )
                return common.BoundedCapture(
                    argv=argv,
                    returncode=0,
                    stdout=bytearray(),
                    stderr=bytearray(),
                )
            return common.BoundedCapture(
                argv=argv,
                returncode=0,
                stdout=bytearray(
                    certificate
                    if argv[-1] == str(providers.CLAUDE_SYSTEM_ROOT_KEYCHAIN)
                    else b""
                ),
                stderr=bytearray(),
            )

        run_command.side_effect = complete
        ca_root = self.review.container_dir / "claude-ca"
        ca_root.mkdir()

        material = self.read_claude_trust_certificates(self.review, ca_root)

        self.assertEqual(material.certificates, canonical)
        self.assertEqual(material.excluded_sha1_fingerprints, frozenset())
        evidence_text = (
            self.review.container_dir / providers.CLAUDE_TRUST_POLICY_EVIDENCE_NAME
        ).read_text(encoding="utf-8")
        evidence = json.loads(evidence_text)
        self.assertEqual(evidence["additional_root_resolution"], "complete")
        self.assertEqual(evidence["additional_unconditional_included_count"], 1)
        self.assertEqual(evidence["additional_unconditional_omitted_count"], 0)
        self.assertNotIn(fingerprint, evidence_text)
        find_calls = [
            call
            for call in run_command.call_args_list
            if call.args[0][1:4] == ("find-certificate", "-a", "-p")
        ]
        self.assertEqual(
            [call.args[0][4:] for call in find_calls],
            [
                arguments
                for _source, arguments in providers.CLAUDE_TRUST_CERTIFICATE_SOURCES
            ],
        )
        for call in find_calls:
            self.assertEqual(
                call.kwargs["stdout_limit_bytes"],
                providers.CLAUDE_CA_FILE_LIMIT_BYTES,
            )
        self.assertEqual(
            tuple(run_command.call_args_list[-1].args[0])[:4],
            (
                str(providers.CLAUDE_OPENSSL_CLIENT),
                "verify",
                "-x509_strict",
                "-check_ss_sig",
            ),
        )

    def test_claude_trust_evidence_replaces_prior_generation_on_deny(self) -> None:
        no_settings = (
            FIXTURES / "securitytool-no-user-trust-settings.stderr"
        ).read_bytes()

        def no_trust_settings(
            argv: tuple[str, ...],
            **_kwargs: object,
        ) -> common.BoundedCapture:
            if argv[1:3] == ("help", "trust-settings-export"):
                return self.trust_export_help_capture(argv)
            return common.BoundedCapture(
                argv=argv,
                returncode=1,
                stdout=bytearray(),
                stderr=bytearray(no_settings),
            )

        ca_root = self.review.container_dir / "claude-ca"
        ca_root.mkdir()
        evidence_path = (
            self.review.container_dir / providers.CLAUDE_TRUST_POLICY_EVIDENCE_NAME
        )
        with mock.patch.object(
            providers,
            "run_bounded_capture",
            side_effect=no_trust_settings,
        ):
            self.read_claude_trust_certificates(self.review, ca_root)
        first = json.loads(evidence_path.read_text(encoding="utf-8"))

        def denied(
            argv: tuple[str, ...],
            **_kwargs: object,
        ) -> common.BoundedCapture:
            if argv[1:3] == ("help", "trust-settings-export"):
                return self.trust_export_help_capture(argv)
            pathlib.Path(argv[-1]).write_bytes(
                (FIXTURES / "securitytool-user-deny.plist").read_bytes()
            )
            return common.BoundedCapture(
                argv=argv,
                returncode=0,
                stdout=bytearray(),
                stderr=bytearray(),
            )

        with (
            mock.patch.object(
                providers,
                "run_bounded_capture",
                side_effect=denied,
            ),
            self.assertRaises(providers.ClaudeTrustSettingsDeny),
        ):
            self.read_claude_trust_certificates(self.review, ca_root)
        second_text = evidence_path.read_text(encoding="utf-8")
        second = json.loads(second_text)

        self.assertEqual(first["status"], "checking")
        self.assertEqual(first["additional_root_resolution"], "not-required")
        self.assertNotEqual(first["generation"], second["generation"])
        self.assertEqual(second["status"], "denied")
        self.assertEqual(second["additional_root_resolution"], "blocked")
        self.assertIsNone(providers.CLAUDE_TRUST_FINGERPRINT.search(second_text))

    def test_claude_trust_evidence_terminalizes_certificate_export_timeout(
        self,
    ) -> None:
        fingerprint = "A" * 40
        settings = plistlib.dumps(
            {
                "trustVersion": 1,
                "trustList": {fingerprint: {"trustSettings": []}},
            }
        )
        empty_settings = plistlib.dumps({"trustVersion": 1, "trustList": {}})

        def timeout_on_certificates(
            argv: tuple[str, ...],
            **_kwargs: object,
        ) -> common.BoundedCapture:
            if argv[1:3] == ("help", "trust-settings-export"):
                return self.trust_export_help_capture(argv)
            if argv[1] == "trust-settings-export":
                pathlib.Path(argv[-1]).write_bytes(
                    settings
                    if "-d" not in argv and "-s" not in argv
                    else empty_settings
                )
                return common.BoundedCapture(
                    argv=argv,
                    returncode=0,
                    stdout=bytearray(),
                    stderr=bytearray(),
                )
            raise common.ReviewTimeoutError("fixture certificate export timeout")

        ca_root = self.review.container_dir / "claude-ca"
        ca_root.mkdir()
        with (
            mock.patch.object(
                providers,
                "run_bounded_capture",
                side_effect=timeout_on_certificates,
            ),
            self.assertRaises(common.ReviewTimeoutError),
        ):
            self.read_claude_trust_certificates(self.review, ca_root)

        evidence = json.loads(
            (
                self.review.container_dir / providers.CLAUDE_TRUST_POLICY_EVIDENCE_NAME
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(evidence["status"], "inconclusive")
        self.assertEqual(evidence["additional_root_resolution"], "inconclusive")

    def test_claude_certificate_export_start_failure_is_tool_unavailable(
        self,
    ) -> None:
        settings = plistlib.dumps(
            {
                "trustVersion": 1,
                "trustList": {"A" * 40: {"trustSettings": []}},
            }
        )
        empty_settings = plistlib.dumps({"trustVersion": 1, "trustList": {}})

        def fail_certificate_start(
            argv: tuple[str, ...],
            **_kwargs: object,
        ) -> common.BoundedCapture:
            if argv[1:3] == ("help", "trust-settings-export"):
                return self.trust_export_help_capture(argv)
            if argv[1] == "trust-settings-export":
                pathlib.Path(argv[-1]).write_bytes(
                    settings
                    if "-d" not in argv and "-s" not in argv
                    else empty_settings
                )
                return common.BoundedCapture(
                    argv=argv,
                    returncode=0,
                    stdout=bytearray(),
                    stderr=bytearray(),
                )
            raise OSError("fixture certificate export start failure")

        ca_root = self.review.container_dir / "claude-ca"
        ca_root.mkdir()
        with (
            mock.patch.object(
                providers,
                "run_bounded_capture",
                side_effect=fail_certificate_start,
            ),
            self.assertRaises(providers.ClaudeTrustToolUnavailable),
        ):
            self.read_claude_trust_certificates(self.review, ca_root)

        evidence = json.loads(
            (
                self.review.container_dir / providers.CLAUDE_TRUST_POLICY_EVIDENCE_NAME
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(evidence["status"], "unavailable")
        self.assertEqual(evidence["additional_root_resolution"], "unavailable")
        self.assertEqual(evidence["additional_unconditional_candidate_count"], 1)

    def test_claude_trust_evidence_terminalizes_blocked_and_unavailable(
        self,
    ) -> None:
        ca_root = self.review.container_dir / "claude-ca"
        ca_root.mkdir()
        evidence_path = (
            self.review.container_dir / providers.CLAUDE_TRUST_POLICY_EVIDENCE_NAME
        )
        cases = (
            (
                providers.ClaudeTrustPolicyUnavailable("fixture malformed policy"),
                "blocked",
                "blocked",
            ),
            (
                providers.ClaudeTrustToolUnavailable("fixture missing tool"),
                "unavailable",
                "unavailable",
            ),
        )

        previous_generation = None
        for error, expected_status, expected_resolution in cases:
            with (
                self.subTest(status=expected_status),
                mock.patch.object(
                    providers,
                    "_read_claude_trust_certificates_impl",
                    side_effect=error,
                ),
                self.assertRaises(type(error)),
            ):
                self.read_claude_trust_certificates(self.review, ca_root)
            evidence_text = evidence_path.read_text(encoding="utf-8")
            evidence = json.loads(evidence_text)
            self.assertEqual(evidence["status"], expected_status)
            self.assertEqual(
                evidence["additional_root_resolution"],
                expected_resolution,
            )
            self.assertNotEqual(evidence["generation"], previous_generation)
            self.assertNotIn(str(error), evidence_text)
            previous_generation = evidence["generation"]

    @mock.patch.object(providers, "run_bounded_capture")
    def test_claude_user_deny_stops_before_other_domains_or_certificate_export(
        self,
        run_command: mock.Mock,
    ) -> None:
        def denied(
            argv: tuple[str, ...],
            **_kwargs: object,
        ) -> common.BoundedCapture:
            if argv[1:3] == ("help", "trust-settings-export"):
                return self.trust_export_help_capture(argv)
            pathlib.Path(argv[-1]).write_bytes(
                (FIXTURES / "securitytool-user-deny.plist").read_bytes()
            )
            return common.BoundedCapture(
                argv=argv,
                returncode=0,
                stdout=bytearray(),
                stderr=bytearray(),
            )

        run_command.side_effect = denied
        ca_root = self.review.container_dir / "claude-ca"
        ca_root.mkdir()

        with self.assertRaisesRegex(
            providers.ClaudeTrustSettingsDeny,
            "Claude user trust settings.*explicit deny",
        ):
            self.read_claude_trust_certificates(self.review, ca_root)

        self.assertEqual(run_command.call_count, 2)

    @mock.patch.object(providers, "run_bounded_capture")
    def test_claude_system_constraint_blocks_without_certificate_export(
        self,
        run_command: mock.Mock,
    ) -> None:
        no_settings = (
            FIXTURES / "securitytool-no-user-trust-settings.stderr"
        ).read_bytes()

        def constrained(
            argv: tuple[str, ...],
            **_kwargs: object,
        ) -> common.BoundedCapture:
            if argv[1:3] == ("help", "trust-settings-export"):
                return self.trust_export_help_capture(argv)
            if "-s" in argv:
                pathlib.Path(argv[-1]).write_bytes(
                    (FIXTURES / "securitytool-system-constraint.plist").read_bytes()
                )
                return common.BoundedCapture(
                    argv=argv,
                    returncode=0,
                    stdout=bytearray(),
                    stderr=bytearray(),
                )
            return common.BoundedCapture(
                argv=argv,
                returncode=1,
                stdout=bytearray(),
                stderr=bytearray(no_settings),
            )

        run_command.side_effect = constrained
        ca_root = self.review.container_dir / "claude-ca"
        ca_root.mkdir()

        with self.assertRaisesRegex(
            providers.ClaudeTrustPolicyUnavailable,
            "cannot enforce excluded trust roots",
        ):
            self.read_claude_trust_certificates(self.review, ca_root)

        self.assertEqual(run_command.call_count, 4)
        evidence_path = (
            self.review.container_dir / providers.CLAUDE_TRUST_POLICY_EVIDENCE_NAME
        )
        evidence_text = evidence_path.read_text(encoding="utf-8")
        evidence = json.loads(evidence_text)
        self.assertEqual(evidence["status"], "blocked")
        self.assertEqual(evidence["distinct_constrained_omitted_count"], 1)
        self.assertEqual(evidence["additional_unconditional_candidate_count"], 0)
        self.assertNotIn("B" * 40, evidence_text)

    @mock.patch.object(providers, "run_bounded_capture")
    def test_claude_constraint_cannot_hide_deny_in_later_trust_domain(
        self,
        run_command: mock.Mock,
    ) -> None:
        def constrained_then_denied(
            argv: tuple[str, ...],
            **_kwargs: object,
        ) -> common.BoundedCapture:
            if argv[1:3] == ("help", "trust-settings-export"):
                return self.trust_export_help_capture(argv)
            fixture = (
                "securitytool-user-constraint.plist"
                if "-d" not in argv
                else "securitytool-system-deny.plist"
            )
            pathlib.Path(argv[-1]).write_bytes((FIXTURES / fixture).read_bytes())
            return common.BoundedCapture(
                argv=argv,
                returncode=0,
                stdout=bytearray(),
                stderr=bytearray(),
            )

        run_command.side_effect = constrained_then_denied
        ca_root = self.review.container_dir / "claude-ca"
        ca_root.mkdir()

        with self.assertRaisesRegex(
            providers.ClaudeTrustSettingsDeny,
            "explicit deny",
        ):
            self.read_claude_trust_certificates(self.review, ca_root)

        self.assertEqual(run_command.call_count, 3)

    @mock.patch.object(providers, "run_bounded_capture")
    def test_claude_malformed_domain_cannot_hide_later_deny(
        self,
        run_command: mock.Mock,
    ) -> None:
        malformed = plistlib.dumps(
            {
                "trustVersion": 1,
                "trustList": {"A" * 40: {"trustSettings": "invalid"}},
            }
        )

        def malformed_then_denied(
            argv: tuple[str, ...],
            **_kwargs: object,
        ) -> common.BoundedCapture:
            if argv[1:3] == ("help", "trust-settings-export"):
                return self.trust_export_help_capture(argv)
            pathlib.Path(argv[-1]).write_bytes(
                (FIXTURES / "securitytool-system-deny.plist").read_bytes()
                if "-d" in argv
                else malformed
            )
            return common.BoundedCapture(
                argv=argv,
                returncode=0,
                stdout=bytearray(),
                stderr=bytearray(),
            )

        run_command.side_effect = malformed_then_denied
        ca_root = self.review.container_dir / "claude-ca"
        ca_root.mkdir()

        with self.assertRaisesRegex(
            providers.ClaudeTrustSettingsDeny,
            "explicit deny",
        ):
            self.read_claude_trust_certificates(self.review, ca_root)

        evidence = json.loads(
            (
                self.review.container_dir / providers.CLAUDE_TRUST_POLICY_EVIDENCE_NAME
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(run_command.call_count, 3)
        self.assertEqual(evidence["status"], "denied")
        self.assertEqual(evidence["additional_root_resolution"], "blocked")
        self.assertEqual(
            [item["status"] for item in evidence["domains"]],
            ["blocked", "denied"],
        )

    @mock.patch.object(providers, "run_bounded_capture")
    def test_claude_terminal_evidence_keeps_partial_aggregate_counts(
        self,
        run_command: mock.Mock,
    ) -> None:
        malformed = plistlib.dumps(
            {
                "trustVersion": 1,
                "trustList": {"A" * 40: {"trustSettings": "invalid"}},
            }
        )
        no_settings = (
            FIXTURES / "securitytool-no-system-trust-settings.stderr"
        ).read_bytes()

        def constrained_then_malformed(
            argv: tuple[str, ...],
            **_kwargs: object,
        ) -> common.BoundedCapture:
            if argv[1:3] == ("help", "trust-settings-export"):
                return self.trust_export_help_capture(argv)
            if "-s" in argv:
                return common.BoundedCapture(
                    argv=argv,
                    returncode=1,
                    stdout=bytearray(),
                    stderr=bytearray(no_settings),
                )
            pathlib.Path(argv[-1]).write_bytes(
                malformed
                if "-d" in argv
                else (FIXTURES / "securitytool-user-constraint.plist").read_bytes()
            )
            return common.BoundedCapture(
                argv=argv,
                returncode=0,
                stdout=bytearray(),
                stderr=bytearray(),
            )

        run_command.side_effect = constrained_then_malformed
        ca_root = self.review.container_dir / "claude-ca"
        ca_root.mkdir()

        with self.assertRaises(providers.ClaudeTrustPolicyUnavailable):
            self.read_claude_trust_certificates(self.review, ca_root)

        evidence = json.loads(
            (
                self.review.container_dir / providers.CLAUDE_TRUST_POLICY_EVIDENCE_NAME
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(run_command.call_count, 4)
        self.assertEqual(evidence["status"], "blocked")
        self.assertEqual(
            [item["status"] for item in evidence["domains"]],
            ["exported", "blocked", "no-settings"],
        )
        self.assertEqual(evidence["distinct_unconditional_count"], 0)
        self.assertEqual(evidence["distinct_constrained_omitted_count"], 1)
        self.assertEqual(evidence["additional_unconditional_candidate_count"], 0)

    def test_claude_tls_rejects_admin_deny_present_in_system_bundle(self) -> None:
        certificate = self.sample_ca_certificate()
        der, _ = providers._canonical_ca_certificate(
            certificate,
            source="denied system fixture",
        )
        fingerprint = hashlib.sha1(der, usedforsecurity=False).hexdigest().upper()
        payload = plistlib.dumps(
            {
                "trustVersion": 1,
                "trustList": {
                    fingerprint: {
                        "trustSettings": [{providers.CLAUDE_TRUST_RESULT_KEY: 3}]
                    },
                },
            },
        )
        self.claude_system_ca.write_bytes(certificate)
        self.trust.side_effect = lambda *_args: (
            providers._classify_trust_fingerprints(
                payload,
                domain="admin",
            ),
            b"",
        )[1]

        with self.assertRaisesRegex(
            providers.ClaudeTrustSettingsDeny,
            "explicit deny",
        ):
            providers._prepare_claude_tls_environment(self.review, {})

        self.assertFalse(
            (
                self.review.container_dir
                / "claude-ca"
                / providers.CLAUDE_CA_BUNDLE_NAME
            ).exists()
        )

    def test_claude_tls_blocks_exclusions_unenforceable_by_bundled_store(
        self,
    ) -> None:
        (
            system_certificate,
            custom_certificate,
            directory_certificate,
            constrained_certificate,
        ) = self.sample_ca_certificates(4)
        der, _ = providers._canonical_ca_certificate(
            constrained_certificate,
            source="constrained custom fixture",
        )
        fingerprint = hashlib.sha1(der, usedforsecurity=False).hexdigest().upper()
        self.claude_system_ca.write_bytes(system_certificate + constrained_certificate)
        custom_path = self.review.source_root / "caller-ca.pem"
        custom_path.write_bytes(custom_certificate + constrained_certificate)
        custom_dir = self.review.source_root / "caller-ca-dir"
        custom_dir.mkdir()
        (custom_dir / "allowed.pem").write_bytes(directory_certificate)
        (custom_dir / "constrained.pem").write_bytes(constrained_certificate)
        self.trust.return_value = providers.ClaudeTrustMaterial(
            certificates=b"",
            excluded_sha1_fingerprints=frozenset({fingerprint}),
            bundled_root_sha256_fingerprints=frozenset(),
        )

        with self.assertRaisesRegex(
            providers.ClaudeTrustPolicyUnavailable,
            "bundled certificate store cannot enforce excluded trust roots",
        ):
            providers._prepare_claude_tls_environment(
                self.review,
                {
                    "SSL_CERT_FILE": str(custom_path),
                    "SSL_CERT_DIR": str(custom_dir),
                },
            )

        ca_root = self.review.container_dir / "claude-ca"
        self.assertFalse((ca_root / providers.CLAUDE_CA_BUNDLE_NAME).exists())
        self.assertFalse((ca_root / providers.CLAUDE_CALLER_CA_SNAPSHOT_NAME).exists())

    def test_claude_tls_blocks_extra_bundled_only_root(self) -> None:
        system_certificate, bundled_only_certificate = self.sample_ca_certificates(2)
        self.claude_system_ca.write_bytes(system_certificate)
        evidence = providers._new_claude_trust_policy_evidence()
        evidence["bundled_root_count"] = 2
        evidence["bundled_root_resolution"] = "pending"
        providers._write_claude_trust_policy_evidence(self.review, evidence)
        trust_material = providers.ClaudeTrustMaterial(
            certificates=b"",
            excluded_sha1_fingerprints=frozenset(),
            bundled_root_sha256_fingerprints=frozenset(
                {
                    self.ca_sha256_fingerprint(system_certificate),
                    self.ca_sha256_fingerprint(bundled_only_certificate),
                }
            ),
            system_certificates=system_certificate,
            evidence=evidence,
        )

        with self.assertRaisesRegex(
            providers.ClaudeTrustPolicyUnavailable,
            "bundled certificate store contains roots outside",
        ):
            providers._prepare_claude_tls_environment(
                self.review,
                {},
                trust_material=trust_material,
            )

        ca_root = self.review.container_dir / "claude-ca"
        self.assertFalse((ca_root / providers.CLAUDE_CA_BUNDLE_NAME).exists())
        self.assertFalse((ca_root / providers.CLAUDE_CALLER_CA_SNAPSHOT_NAME).exists())
        retained = json.loads(
            (
                self.review.container_dir / providers.CLAUDE_TRUST_POLICY_EVIDENCE_NAME
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(retained["status"], "blocked")
        self.assertEqual(retained["bundled_root_count"], 2)
        self.assertEqual(retained["bundled_root_extra_count"], 1)
        self.assertEqual(retained["bundled_root_resolution"], "blocked")

    def test_claude_tls_blocks_missing_bundled_root_evidence(self) -> None:
        with self.assertRaisesRegex(
            providers.ClaudeTrustPolicyUnavailable,
            "bundled root evidence is unavailable",
        ):
            providers._prepare_claude_tls_environment(
                self.review,
                {},
                trust_material=providers.ClaudeTrustMaterial(
                    certificates=b"",
                    excluded_sha1_fingerprints=frozenset(),
                ),
            )

        ca_root = self.review.container_dir / "claude-ca"
        self.assertFalse((ca_root / providers.CLAUDE_CA_BUNDLE_NAME).exists())

    @mock.patch.object(
        providers,
        "_trusted_claude_ripgrep",
        return_value=pathlib.Path("/bin/echo"),
    )
    def test_claude_review_sandbox_omits_keychain_broker_for_api_key(
        self,
        _rg: mock.Mock,
    ) -> None:
        profile = providers._claude_review_sandbox_profile(
            pathlib.Path("/bin/true"),
            self.review,
            {
                "ANTHROPIC_API_KEY": "test-api-key",
                "HOME": str(self.review.container_dir / "claude-home"),
                "TMPDIR": str(self.review.container_dir / "tmp"),
                "PATH": "/usr/bin",
            },
            proxy_port=43210,
        )

        self.assertNotIn("/usr/bin/security", profile)
        self.assertNotIn("com.apple.securityd.xpc", profile)

    @mock.patch.object(
        providers,
        "_trusted_claude_ripgrep",
        return_value=pathlib.Path("/bin/echo"),
    )
    def test_claude_auth_warmup_allows_only_direct_keychain_client(
        self,
        _rg: mock.Mock,
    ) -> None:
        profile = providers._claude_review_sandbox_profile(
            pathlib.Path("/bin/true"),
            self.review,
            {
                "HOME": str(self.review.container_dir / "claude-home"),
                "TMPDIR": str(self.review.container_dir / "tmp"),
                "PATH": "/usr/bin:/bin",
            },
            proxy_port=43210,
            allow_direct_keychain=True,
            allow_workspace_read=False,
        )

        self.assertIn(
            f'(literal "{self.claude_keychain_client.resolve()}")',
            profile,
        )
        self.assertIn("com.apple.securityd.xpc", profile)
        self.assertNotIn(str(self.claude_broker.resolve()), profile)
        self.assertNotIn(
            f'(subpath "{self.review.workspace_root.resolve()}")',
            profile,
        )

    @mock.patch.object(
        providers,
        "_trusted_claude_ripgrep",
        return_value=pathlib.Path("/bin/echo"),
    )
    def test_claude_review_sandbox_rejects_relative_ca_file(
        self,
        _rg: mock.Mock,
    ) -> None:
        with self.assertRaisesRegex(ReviewError, "valid absolute SSL_CERT_FILE"):
            providers._prepare_claude_tls_environment(
                self.review,
                {
                    "SSL_CERT_FILE": "corporate-ca.pem",
                },
            )

    @mock.patch.object(
        providers,
        "_trusted_claude_ripgrep",
        return_value=pathlib.Path("/bin/echo"),
    )
    def test_claude_review_sandbox_rejects_host_ca_directory(
        self,
        _rg: mock.Mock,
    ) -> None:
        with self.assertRaisesRegex(ReviewError, "helper-owned SSL_CERT_DIR"):
            providers._claude_review_sandbox_profile(
                pathlib.Path("/bin/true"),
                self.review,
                {
                    "HOME": str(self.review.container_dir / "claude-home"),
                    "TMPDIR": str(self.review.container_dir / "tmp"),
                    "SSL_CERT_DIR": "/",
                },
                proxy_port=43210,
            )

    @mock.patch.object(
        providers,
        "_trusted_claude_ripgrep",
        return_value=pathlib.Path("/bin/echo"),
    )
    def test_claude_review_sandbox_rejects_host_node_extra_ca(
        self,
        _rg: mock.Mock,
    ) -> None:
        with self.assertRaisesRegex(
            ReviewError,
            "helper-owned NODE_EXTRA_CA_CERTS",
        ):
            providers._claude_review_sandbox_profile(
                pathlib.Path("/bin/true"),
                self.review,
                {
                    "HOME": str(self.review.container_dir / "claude-home"),
                    "TMPDIR": str(self.review.container_dir / "tmp"),
                    "NODE_EXTRA_CA_CERTS": str(self.claude_system_ca),
                },
                proxy_port=43210,
            )

    def test_claude_tls_preparation_rejects_non_certificate_file(self) -> None:
        source = self.review.source_root / ".netrc"
        source.write_text(
            "machine example.test login user password secret\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ReviewError, "contains no PEM certificate"):
            providers._prepare_claude_tls_environment(
                self.review,
                {"SSL_CERT_FILE": str(source)},
            )

    def test_claude_tls_preparation_rejects_private_key_material(self) -> None:
        source = self.review.source_root / "combined.pem"
        source.write_bytes(
            self.sample_ca_certificate() + self.synthetic_private_key_pem(b"secret")
        )

        with self.assertRaisesRegex(ReviewError, "contains a private key"):
            providers._prepare_claude_tls_environment(
                self.review,
                {"SSL_CERT_FILE": str(source)},
            )
        self.trust.assert_not_called()

    def test_claude_ca_read_is_anchored_to_open_file_descriptor(self) -> None:
        certificate = self.sample_ca_certificate()
        source = self.review.source_root / "caller-ca.pem"
        source.write_bytes(certificate)
        replacement = self.review.source_root / "replacement.pem"
        replacement.write_bytes(self.synthetic_private_key_pem(b"secret"))
        real_open = os.open

        def open_then_replace(path: os.PathLike[str], flags: int) -> int:
            fd = real_open(path, flags)
            replacement.replace(source)
            return fd

        with mock.patch.object(providers.os, "open", side_effect=open_then_replace):
            material = providers._read_ca_source(source, source="fixture")

        self.assertEqual(material, certificate)
        self.assertIn(b"PRIVATE" + b" KEY", source.read_bytes())

    def test_claude_caller_snapshot_read_is_anchored_to_open_file_descriptor(
        self,
    ) -> None:
        certificate = self.sample_ca_certificate()
        source = self.review.source_root / "caller-snapshot.pem"
        source.write_bytes(certificate)
        replacement = self.review.source_root / "snapshot-replacement.pem"
        replacement.write_bytes(self.synthetic_private_key_pem(b"secret"))
        real_open = os.open

        def open_then_replace(path: os.PathLike[str], flags: int) -> int:
            fd = real_open(path, flags)
            replacement.replace(source)
            return fd

        with mock.patch.object(providers.os, "open", side_effect=open_then_replace):
            material = providers._read_claude_caller_ca_snapshot(source)

        self.assertEqual(material, certificate)
        self.assertIn(b"PRIVATE" + b" KEY", source.read_bytes())

    def test_claude_caller_snapshot_read_rejects_growth_after_fstat(self) -> None:
        certificate = self.sample_ca_certificate()
        source = self.review.source_root / "growing-snapshot.pem"
        source.write_bytes(certificate)
        real_read = os.read
        read_count = 0

        def read_then_grow(fd: int, size: int) -> bytes:
            nonlocal read_count
            chunk = real_read(fd, size)
            if read_count == 0:
                with source.open("ab") as handle:
                    handle.write(b"x")
            read_count += 1
            return chunk

        with (
            mock.patch.object(
                providers,
                "CLAUDE_CALLER_CA_SNAPSHOT_LIMIT_BYTES",
                len(certificate),
            ),
            mock.patch.object(providers.os, "read", side_effect=read_then_grow),
            self.assertRaisesRegex(ReviewError, "exceeds the size limit"),
        ):
            providers._read_claude_caller_ca_snapshot(source)

    def test_claude_caller_snapshot_read_rejects_symlink(self) -> None:
        target = self.review.source_root / "snapshot-target.pem"
        target.write_bytes(self.sample_ca_certificate())
        source = self.review.source_root / "snapshot-link.pem"
        source.symlink_to(target)

        with self.assertRaisesRegex(ReviewError, "cannot open"):
            providers._read_claude_caller_ca_snapshot(source)

    def test_claude_ca_read_rejects_growth_after_fstat(self) -> None:
        certificate = self.sample_ca_certificate()
        source = self.review.source_root / "growing-ca.pem"
        source.write_bytes(certificate)
        real_read = os.read
        read_count = 0

        def read_then_grow(fd: int, size: int) -> bytes:
            nonlocal read_count
            chunk = real_read(fd, size)
            if read_count == 0:
                with source.open("ab") as handle:
                    handle.write(b"x")
            read_count += 1
            return chunk

        with (
            mock.patch.object(
                providers,
                "CLAUDE_CA_FILE_LIMIT_BYTES",
                len(certificate),
            ),
            mock.patch.object(providers.os, "read", side_effect=read_then_grow),
            self.assertRaisesRegex(ReviewError, "exceeds the size limit"),
        ):
            providers._read_ca_source(source, source="fixture")

    def test_claude_ca_read_rejects_symlink(self) -> None:
        target = self.review.source_root / "target-ca.pem"
        target.write_bytes(self.sample_ca_certificate())
        source = self.review.source_root / "linked-ca.pem"
        source.symlink_to(target)

        with self.assertRaisesRegex(ReviewError, "cannot open"):
            providers._read_ca_source(source, source="fixture")

    def test_claude_ca_read_rejects_fifo_without_blocking(self) -> None:
        source = self.review.source_root / "caller-ca.fifo"
        os.mkfifo(source)

        with self.assertRaisesRegex(ReviewError, "not a regular file"):
            providers._read_ca_source(source, source="fixture")

    def test_claude_ca_directory_budget_counts_non_certificate_bytes(self) -> None:
        source_dir = self.review.source_root / "invalid-ca-dir"
        source_dir.mkdir()
        (source_dir / "first").write_bytes(b"abcd")
        (source_dir / "second").write_bytes(b"efgh")

        with (
            mock.patch.object(providers, "CLAUDE_CA_DIR_LIMIT_BYTES", 6),
            self.assertRaisesRegex(ReviewError, "directory exceeds the size limit"),
        ):
            providers._prepare_claude_tls_environment(
                self.review,
                {"SSL_CERT_DIR": str(source_dir)},
            )

    def test_claude_tls_preparation_folds_ca_directories_into_bundle(self) -> None:
        certificate = self.sample_ca_certificate()
        source_dirs = []
        for name in ("first", "second"):
            source_dir = self.review.source_root / name
            source_dir.mkdir()
            (source_dir / "deadbeef.0").write_bytes(certificate)
            source_dirs.append(source_dir)

        prepared = providers._prepare_claude_tls_environment(
            self.review,
            {"SSL_CERT_DIR": os.pathsep.join(str(path) for path in source_dirs)},
        )

        self.assertNotIn("SSL_CERT_DIR", prepared)
        bundle = pathlib.Path(prepared["SSL_CERT_FILE"]).read_bytes()
        expected_der = providers._canonical_ca_certificate(
            certificate,
            source="directory fixture",
        )[0]
        actual = [
            providers._canonical_ca_certificate(
                block,
                source="prepared bundle",
            )[0]
            for block in providers.CLAUDE_CERTIFICATE_BLOCK.findall(bundle)
        ]
        self.assertEqual(actual.count(expected_der), 1)

    def test_claude_tls_preparation_skips_ca_directory_symlinks(self) -> None:
        certificate = self.sample_ca_certificate()
        source_dir = self.review.source_root / "ca-dir-with-links"
        source_dir.mkdir()
        certificate_path = source_dir / "certificate.pem"
        certificate_path.write_bytes(certificate)
        (source_dir / "deadbeef.0").symlink_to(certificate_path.name)

        prepared = providers._prepare_claude_tls_environment(
            self.review,
            {"SSL_CERT_DIR": str(source_dir)},
        )

        bundle = pathlib.Path(prepared["SSL_CERT_FILE"]).read_bytes()
        expected_der = providers._canonical_ca_certificate(
            certificate,
            source="directory fixture",
        )[0]
        actual = [
            providers._canonical_ca_certificate(
                block,
                source="prepared bundle",
            )[0]
            for block in providers.CLAUDE_CERTIFICATE_BLOCK.findall(bundle)
        ]
        self.assertEqual(actual.count(expected_der), 1)

    def test_claude_tls_preparation_bounds_directory_enumeration_before_sort(
        self,
    ) -> None:
        source_dir = self.review.source_root / "large-ca-dir"
        source_dir.mkdir()
        consumed = 0

        def entries():
            nonlocal consumed
            for index in range(providers.CLAUDE_CA_DIR_ENTRY_LIMIT + 10):
                consumed += 1
                yield source_dir / f"entry-{index:05d}"

        with (
            mock.patch.object(pathlib.Path, "iterdir", return_value=entries()),
            self.assertRaisesRegex(ReviewError, "too many entries"),
        ):
            providers._prepare_claude_tls_environment(
                self.review,
                {"SSL_CERT_DIR": str(source_dir)},
            )

        self.assertEqual(consumed, providers.CLAUDE_CA_DIR_ENTRY_LIMIT + 1)

    @mock.patch.object(providers.ssl, "create_default_context")
    def test_proxy_ssl_context_honors_git_ca_bundle(
        self,
        create_context: mock.Mock,
    ) -> None:
        context = create_context.return_value

        result = providers._proxy_ssl_context(
            {"GIT_SSL_CAINFO": "/isolated/git-ca.pem"}
        )

        self.assertIs(result, context)
        create_context.assert_called_once_with(cafile="/isolated/git-ca.pem")

    @mock.patch.object(providers.ssl, "create_default_context")
    def test_proxy_ssl_context_loads_each_ca_directory(
        self,
        create_context: mock.Mock,
    ) -> None:
        context = create_context.return_value

        result = providers._proxy_ssl_context(
            {"SSL_CERT_DIR": os.pathsep.join(("/first", "/second"))}
        )

        self.assertIs(result, context)
        create_context.assert_called_once_with(cafile=None)
        self.assertEqual(
            context.load_verify_locations.call_args_list,
            [mock.call(capath="/first"), mock.call(capath="/second")],
        )

    def test_claude_connect_proxy_allows_only_configured_target(self) -> None:
        class EchoHandler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                data = self.request.recv(1024)
                self.request.sendall(data)

        try:
            target = socketserver.ThreadingTCPServer(("127.0.0.1", 0), EchoHandler)
        except PermissionError:
            self.skipTest("loopback bind is unavailable in the current sandbox")
        target.daemon_threads = True
        target_thread = threading.Thread(target=target.serve_forever, daemon=True)
        target_thread.start()
        target_port = int(target.server_address[1])
        try:
            with providers._claude_connect_proxy(
                {},
                allowed_targets=frozenset({("127.0.0.1", target_port)}),
            ) as proxy_port:
                with socket.create_connection(("127.0.0.1", proxy_port)) as client:
                    client.sendall(
                        (
                            f"CONNECT 127.0.0.1:{target_port} HTTP/1.1\r\n"
                            f"Host: 127.0.0.1:{target_port}\r\n\r\n"
                        ).encode("ascii")
                    )
                    response = client.recv(4096)
                    self.assertIn(b"200 Connection Established", response)
                    client.sendall(b"ping")
                    self.assertEqual(client.recv(4), b"ping")

                with socket.create_connection(("127.0.0.1", proxy_port)) as client:
                    client.sendall(
                        b"CONNECT example.com:443 HTTP/1.1\r\n"
                        b"Host: example.com:443\r\n\r\n"
                    )
                    self.assertIn(b"403 Forbidden", client.recv(4096))
        finally:
            target.shutdown()
            target.server_close()
            target_thread.join(timeout=5.0)

    def test_https_proxy_tunnel_drains_decrypted_pending_data(self) -> None:
        class PlainSocket:
            def __init__(self) -> None:
                self.sent: list[bytes] = []

            def settimeout(self, _value) -> None:
                return

            def sendall(self, data: bytes) -> None:
                self.sent.append(data)

        class FakeTLSSocket:
            def __init__(self) -> None:
                self.pending_values = iter((1, 1, 0))
                self.received = iter((b"a" * (64 * 1024), b"b" * (64 * 1024), b""))

            def settimeout(self, _value) -> None:
                return

            def pending(self) -> int:
                return next(self.pending_values)

            def recv(self, _size: int) -> bytes:
                return next(self.received)

        client = PlainSocket()
        upstream = FakeTLSSocket()
        with (
            mock.patch.object(providers.ssl, "SSLSocket", FakeTLSSocket),
            mock.patch.object(
                providers.select,
                "select",
                return_value=([upstream], (), ()),
            ) as select_call,
        ):
            providers._tunnel_proxy_sockets(client, upstream)

        self.assertEqual(client.sent, [b"a" * (64 * 1024), b"b" * (64 * 1024)])
        select_call.assert_called_once()

    def test_claude_proxy_environment_blocks_bypass_variables(self) -> None:
        env = providers._with_claude_proxy_environment(
            {
                "HTTPS_PROXY": "http://corporate-proxy:8080",
                "NO_PROXY": "example.com",
            },
            43210,
        )

        for key in (
            "ALL_PROXY",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "all_proxy",
            "http_proxy",
            "https_proxy",
        ):
            self.assertEqual(env[key], "http://127.0.0.1:43210")
        self.assertEqual(env["NO_PROXY"], "")
        self.assertEqual(env["no_proxy"], "")

    def test_claude_upstream_proxy_accepts_lowercase_environment(self) -> None:
        self.assertEqual(
            providers._upstream_proxy_url(
                {"https_proxy": "http://corporate-proxy:8080"},
                host="api.anthropic.com",
                port=443,
            ),
            "http://corporate-proxy:8080",
        )

    def test_claude_upstream_proxy_prefers_lowercase_override(self) -> None:
        self.assertEqual(
            providers._upstream_proxy_url(
                {
                    "HTTPS_PROXY": "http://system-proxy:8080",
                    "https_proxy": "http://task-proxy:8080",
                },
                host="api.anthropic.com",
                port=443,
            ),
            "http://task-proxy:8080",
        )

    def test_empty_lowercase_proxy_disables_uppercase_pair(self) -> None:
        for lowercase, uppercase in (
            ("https_proxy", "HTTPS_PROXY"),
            ("http_proxy", "HTTP_PROXY"),
            ("all_proxy", "ALL_PROXY"),
        ):
            with self.subTest(lowercase=lowercase):
                self.assertIsNone(
                    providers._upstream_proxy_url(
                        {
                            lowercase: "",
                            uppercase: "http://system-proxy:8080",
                        },
                        host="api.anthropic.com",
                        port=443,
                    )
                )

    def test_claude_proxy_rejects_invalid_upstream_ports_before_bind(self) -> None:
        for value in (
            "http://corporate-proxy:0",
            "http://corporate-proxy:99999",
        ):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(
                    ReviewError,
                    "upstream proxy .* invalid",
                ),
            ):
                with providers._claude_connect_proxy({"https_proxy": value}):
                    self.fail("invalid upstream proxy unexpectedly started")

    @mock.patch.object(
        providers,
        "_ClaudeProxyServer",
        side_effect=OSError("bind failed"),
    )
    def test_claude_proxy_bind_failure_is_runtime_unavailable(
        self,
        _server: mock.Mock,
    ) -> None:
        with self.assertRaisesRegex(
            providers.ClaudeLoopbackUnavailable,
            "CONNECT proxy cannot bind loopback",
        ):
            with providers._claude_connect_proxy({}):
                self.fail("unavailable proxy unexpectedly started")

    def test_claude_proxy_thread_failure_closes_server(self) -> None:
        server = mock.Mock()
        thread = mock.Mock()
        thread.start.side_effect = RuntimeError("thread unavailable")

        with (
            mock.patch.object(
                providers,
                "_ClaudeProxyServer",
                return_value=server,
            ),
            mock.patch.object(providers.threading, "Thread", return_value=thread),
            self.assertRaisesRegex(
                providers.ClaudeLoopbackUnavailable,
                "CONNECT proxy cannot start",
            ),
        ):
            with providers._claude_connect_proxy({}):
                self.fail("unavailable proxy unexpectedly started")

        server.shutdown.assert_not_called()
        server.server_close.assert_called_once_with()
        thread.join.assert_not_called()

    def test_claude_upstream_proxy_respects_bypass_environment(self) -> None:
        for key in ("NO_PROXY", "no_proxy"):
            with self.subTest(key=key):
                self.assertIsNone(
                    providers._upstream_proxy_url(
                        {
                            "HTTPS_PROXY": "http://corporate-proxy:8080",
                            key: ".anthropic.com",
                        },
                        host="api.anthropic.com",
                        port=443,
                    )
                )

    def test_claude_proxy_allows_api_and_oauth_refresh_targets(self) -> None:
        self.assertEqual(
            providers.CLAUDE_PROXY_TARGETS,
            frozenset({("api.anthropic.com", 443)}),
        )
        self.assertEqual(
            providers.CLAUDE_AUTH_PROXY_TARGETS,
            frozenset(
                {
                    ("api.anthropic.com", 443),
                    ("platform.claude.com", 443),
                }
            ),
        )
        self.assertIn(
            providers._parse_connect_target("platform.claude.com:443"),
            providers.CLAUDE_AUTH_PROXY_TARGETS,
        )
        self.assertNotIn(
            providers._parse_connect_target("platform.claude.com:443"),
            providers.CLAUDE_PROXY_TARGETS,
        )

    @mock.patch.object(
        providers,
        "resolve_reviewer_executable",
        return_value=pathlib.Path("/bin/claude"),
    )
    @mock.patch.object(
        providers,
        "CLAUDE_PROBE_SANDBOX",
        pathlib.Path("/usr/bin/true"),
    )
    @mock.patch.object(providers, "run")
    def test_claude_refuses_unverified_safe_mode_semantics(
        self,
        run_command: mock.Mock,
        resolve: mock.Mock,
    ) -> None:
        def resolve_and_validate(_name: str, **kwargs) -> pathlib.Path:
            candidate = pathlib.Path("/bin/claude")
            kwargs["candidate_validator"](candidate)
            return candidate

        resolve.side_effect = resolve_and_validate
        run_command.side_effect = (
            Completed(
                argv=("claude", "--version"),
                returncode=0,
                stdout=b"2.1.202 (Claude Code)\n",
                stderr=b"",
            ),
            Completed(
                argv=("claude", "--help"),
                returncode=0,
                stdout=b"generic help",
                stderr=b"",
            ),
        )

        with self.assertRaisesRegex(ReviewError, "uniquely verifiable --safe-mode"):
            providers._claude_attempt(
                review=self.review,
                model="claude-opus-4-8",
                index=1,
                env={"HOME": "/Users/reviewer"},
            )

        self.assertEqual(run_command.call_count, 2)

    @mock.patch.object(
        providers,
        "CLAUDE_PROBE_SANDBOX",
        pathlib.Path("/usr/bin/true"),
    )
    @mock.patch.object(providers, "run")
    def test_claude_accepts_exact_safe_mode_option_block(
        self,
        run_command: mock.Mock,
    ) -> None:
        run_command.return_value = Completed(
            argv=("claude", "--help"),
            returncode=0,
            stdout=(
                "Usage: claude [options]\nOptions:\n  "
                + providers.CLAUDE_SAFE_MODE_HELP_FORM
                + "\n  --betas <betas...> Beta headers\n"
            ).encode(),
            stderr=b"",
        )

        providers._require_claude_safe_mode(
            pathlib.Path("/bin/claude"),
            {"HOME": str(self.review.container_dir)},
        )

    @mock.patch.object(
        providers,
        "CLAUDE_PROBE_SANDBOX",
        pathlib.Path("/usr/bin/true"),
    )
    @mock.patch.object(providers, "run")
    def test_claude_rejects_safe_mode_option_mutations(
        self,
        run_command: mock.Mock,
    ) -> None:
        form = providers.CLAUDE_SAFE_MODE_HELP_FORM
        for mutated_form in (
            form.replace("plugins, hooks", "plugins with hooks", 1),
            form.replace("auth, model selection", "model selection", 1),
            form.replace("claude_code_safe_mode=1", "claude_code_safe_mode=0", 1),
            form.replace("all customizations", "some customizations", 1),
        ):
            with self.subTest(mutated_form=mutated_form):
                run_command.return_value = Completed(
                    argv=("claude", "--help"),
                    returncode=0,
                    stdout=(
                        "Options:\n  "
                        + mutated_form
                        + "\n  --betas <betas...> Beta headers\n"
                    ).encode(),
                    stderr=b"",
                )

                with self.assertRaisesRegex(
                    ReviewError, "uniquely verifiable --safe-mode"
                ):
                    providers._require_claude_safe_mode(
                        pathlib.Path("/bin/claude"),
                        {"HOME": str(self.review.container_dir)},
                    )

    @mock.patch.object(
        providers,
        "CLAUDE_PROBE_SANDBOX",
        pathlib.Path("/usr/bin/true"),
    )
    @mock.patch.object(providers, "run")
    def test_claude_rejects_duplicate_or_conflicting_safe_mode_descriptions(
        self,
        run_command: mock.Mock,
    ) -> None:
        form = providers.CLAUDE_SAFE_MODE_HELP_FORM
        for help_text in (
            "Options:\n  " + form + "\n  --safe-mode hooks still load\n",
            "Options:\n  "
            + form
            + "\n  hooks still load\n  --betas <betas...> Beta headers\n",
            "Options:\n  "
            + form
            + "\n  --betas <betas...> Unlike --safe-mode, hooks still load\n",
        ):
            with self.subTest(help_text=help_text):
                run_command.return_value = Completed(
                    argv=("claude", "--help"),
                    returncode=0,
                    stdout=help_text.encode(),
                    stderr=b"",
                )

                with self.assertRaisesRegex(
                    ReviewError, "uniquely verifiable --safe-mode"
                ):
                    providers._require_claude_safe_mode(
                        pathlib.Path("/bin/claude"),
                        {"HOME": str(self.review.container_dir)},
                    )


if __name__ == "__main__":
    unittest.main()
