from __future__ import annotations

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
    def test_private_catalog_carries_public_jwt_legacy_compatibility(self) -> None:
        override_bytes = OVERRIDE_CATALOG.read_bytes()
        self.assertEqual(GENERATED_CATALOG.read_bytes(), override_bytes)
        catalog = json.loads(override_bytes)

        self.assertEqual(catalog["schema_version"], 1)
        self.assertEqual(catalog["authoring_pool"]["version"], "joey-private-v1")
        authoring_tokens = catalog["authoring_pool"]["tokens"]
        self.assertEqual(len(authoring_tokens), 10)
        self.assertEqual(
            {token["rule"] for token in authoring_tokens},
            {"generic-secret-assignment"},
        )

        exemptions = {item["id"]: item for item in catalog["legacy_exemptions"]}
        self.assertEqual(len(exemptions), 3)
        legacy_values = [
            value for exemption in exemptions.values() for value in exemption["values"]
        ]
        self.assertEqual(len(legacy_values), 18)
        self.assertEqual(
            sum(value["source_occurrences"] for value in legacy_values),
            39,
        )
        self.assertEqual(
            {value["rule"] for value in legacy_values},
            {"generic-secret-assignment", "github-token", "jwt"},
        )

        jwt_exemption = exemptions["codex-workflow-hygiene-jwt"]
        self.assertEqual(
            jwt_exemption["repository"],
            "Joey-Tools/codex-workflow-hygiene",
        )
        self.assertEqual(
            jwt_exemption["verified_master_tip"],
            "95befb966cd93e0161ecb45099c124eac56cb52f",
        )
        self.assertEqual(jwt_exemption["match"], "non-increasing-global-count")
        self.assertEqual(len(jwt_exemption["values"]), 1)
        jwt_value = jwt_exemption["values"][0]
        self.assertEqual(jwt_value["id"], "session-retrospective-redaction-jwt")
        self.assertEqual(jwt_value["rule"], "jwt")
        self.assertEqual(
            jwt_value["containing_commit"],
            "49dc9bb8af8256b5c39ff870ec6b682ff08bd5a8",
        )
        self.assertEqual(jwt_value["source_occurrences"], 1)


if __name__ == "__main__":
    unittest.main()
