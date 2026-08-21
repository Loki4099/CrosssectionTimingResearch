from __future__ import annotations

import csv
from pathlib import Path
import tomllib
import unittest

from scripts.build_xa01_prereg_lock import build


ROOT = Path(__file__).resolve().parents[1]


class XA01PreregTests(unittest.TestCase):
    def test_registered_grid_and_authorization(self) -> None:
        with (ROOT / "config/experiments/xa01/program.toml").open("rb") as handle:
            program = tomllib.load(handle)
        self.assertEqual(program["paths"]["path_count"], 112)
        self.assertEqual(program["paths"]["frequencies"], ["weekly", "monthly"])
        self.assertEqual(program["paths"]["top_k"], [5, 10, 20, 50])
        self.assertFalse(program["authorization"]["models"])
        self.assertFalse(program["authorization"]["lockbox"])
        self.assertFalse(program["authorization"]["biweekly"])

    def test_exact_factor_universe(self) -> None:
        with (ROOT / "config/experiments/xa01/factor_registry.csv").open(
            newline="", encoding="utf-8"
        ) as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 14)
        ids = {row["factor_id"] for row in rows}
        self.assertIn("XS056_CFO_ACCRUALS_PT", ids)
        self.assertNotIn("XS039_ACCRUALS_V2", ids)
        self.assertNotIn("XS026_VALUE_BM", ids)
        self.assertNotIn("XS030_NET_PAYOUT_YIELD", ids)

    def test_lock_payload_members_are_closed(self) -> None:
        payload = build(ROOT)
        self.assertEqual(len(payload["files"]), 4)
        self.assertFalse(payload["lockbox_authorized"])


if __name__ == "__main__":
    unittest.main()
