from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
OVERRIDE_CATALOG = (
    REPO_ROOT
    / "personal_codex"
    / "private-overrides"
    / "review-orchestration-playbook"
    / "synthetic-token-catalog.json"
)
GENERATED_CATALOG = (
    REPO_ROOT
    / "personal_codex"
    / "skills"
    / "review-orchestration-playbook"
    / "scripts"
    / "review_runtime"
    / "synthetic-token-catalog.json"
)


class PrivateSyntheticCatalogTest(unittest.TestCase):
    def test_private_catalog_excludes_retired_jwt_legacy_compatibility(self) -> None:
        override_bytes = OVERRIDE_CATALOG.read_bytes()
        self.assertEqual(GENERATED_CATALOG.read_bytes(), override_bytes)
        catalog = json.loads(override_bytes)

        self.assertEqual(catalog["schema_version"], 1)
        self.assertEqual(catalog["authoring_pool"]["version"], "joey-private-v2")
        authoring_tokens = catalog["authoring_pool"]["tokens"]
        self.assertEqual(len(authoring_tokens), 11)
        self.assertEqual(
            {token["rule"] for token in authoring_tokens},
            {"generic-secret-assignment"},
        )

        exemptions = {item["id"]: item for item in catalog["legacy_exemptions"]}
        self.assertEqual(len(exemptions), 2)
        legacy_values = [
            value for exemption in exemptions.values() for value in exemption["values"]
        ]
        self.assertEqual(len(legacy_values), 17)
        self.assertEqual(
            sum(value["source_occurrences"] for value in legacy_values),
            38,
        )
        self.assertEqual(
            {value["rule"] for value in legacy_values},
            {"generic-secret-assignment", "github-token"},
        )
        self.assertNotIn("codex-workflow-hygiene-jwt", exemptions)

    def test_private_catalog_does_not_reuse_public_example_values(self) -> None:
        catalog = json.loads(OVERRIDE_CATALOG.read_bytes())
        public_examples = (
            "codex_public_synth_v1_access_a",
            "codex_public_synth_v1_access_b",
            "codex_public_synth_v1_access_expired",
            "codex_public_synth_v1_refresh_a",
            "codex_public_synth_v1_refresh_b",
            "codex_public_synth_v1_refresh_consumed",
            "codex_public_synth_v1_id_a",
            "codex_public_synth_v1_id_b",
            "codex_public_synth_v1_api_key_a",
            "codex_public_synth_v1_bearer_a",
        )
        public_digests = {
            hashlib.sha256(value.encode("ascii")).hexdigest()
            for value in public_examples
        }
        private_values = [
            token["value"].encode("ascii")
            for token in catalog["authoring_pool"]["tokens"]
        ]
        private_values.extend(
            base64.b64decode(value["value_base64"], validate=True)
            for exemption in catalog["legacy_exemptions"]
            for value in exemption["values"]
        )
        private_digests = {
            hashlib.sha256(value).hexdigest() for value in private_values
        }

        self.assertTrue(public_digests.isdisjoint(private_digests))


if __name__ == "__main__":
    unittest.main()
