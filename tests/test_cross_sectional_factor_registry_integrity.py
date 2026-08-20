from __future__ import annotations

import csv
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_DIR = ROOT / "config" / "research" / "cross_sectional_alpha"


def _read_registry(name: str) -> list[dict[str, str]]:
    with (REGISTRY_DIR / name).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if any(None in row for row in rows):
        raise AssertionError(f"{name} contains a row with too many CSV fields")
    return rows


class CrossSectionalFactorRegistryIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.definitions = _read_registry("factor_definition_registry.csv")
        cls.active = _read_registry("active_factor_registry.csv")

    def test_active_ids_have_one_to_one_definition_rows(self) -> None:
        definition_ids = [row["factor_id"] for row in self.definitions]
        active_ids = [row["factor_id"] for row in self.active]
        source_ids = [row["source_definition_id"] for row in self.active]

        self.assertEqual(len(definition_ids), len(set(definition_ids)))
        self.assertEqual(len(active_ids), len(set(active_ids)))
        self.assertEqual(len(source_ids), len(set(source_ids)))
        self.assertEqual(active_ids, source_ids)
        self.assertTrue(set(active_ids).issubset(definition_ids))
        for factor_id in active_ids:
            self.assertEqual(definition_ids.count(factor_id), 1)

    def test_accrual_versions_preserve_provenance(self) -> None:
        definitions = {row["factor_id"]: row for row in self.definitions}
        active = {row["factor_id"]: row for row in self.active}

        historical = definitions["XS039_ACCRUALS"]
        self.assertEqual(
            historical["canonical_definition"],
            "-[(change current assets - change cash) - "
            "(change current liabilities - change short debt - change taxes payable)] "
            "/ average total assets",
        )
        self.assertNotIn("depreciation", historical["canonical_definition"].lower())
        self.assertNotIn("XS039_ACCRUALS", active)

        strict = definitions["XS039_ACCRUALS_V2"]
        self.assertEqual(strict["primary_paper_id"], "LIT036")
        self.assertEqual(strict["definition_status"], "paper_canonical")
        self.assertIn("depreciation and amortization", strict["canonical_definition"])
        self.assertIn("historical row remains unchanged", strict["project_translation"])
        self.assertEqual(active["XS039_ACCRUALS_V2"]["first_round_eligible"], "true")

        cfo = definitions["XS056_CFO_ACCRUALS_PT"]
        self.assertEqual(
            cfo["canonical_definition"],
            "-(net income - cash flow from operations) / average total assets",
        )
        self.assertEqual(cfo["definition_status"], "project_translation")
        self.assertIn("never a silent fallback", cfo["project_translation"])
        self.assertEqual(
            active["XS056_CFO_ACCRUALS_PT"]["first_round_eligible"], "false"
        )


if __name__ == "__main__":
    unittest.main()
