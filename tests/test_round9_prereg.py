from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tomllib
import unittest

import pandas as pd

from scripts.build_round9_prereg_lock import MEMBERS, payload


ROOT = Path(__file__).resolve().parents[1]


class Round9PreregTests(unittest.TestCase):
    def test_registry_is_fixed_six_cell_long_only_family(self) -> None:
        frame = pd.read_csv(ROOT / "config/experiments/round9/transfer_registry.csv")
        self.assertEqual(len(frame), 6)
        self.assertEqual(frame.transfer_id.nunique(), 6)
        self.assertEqual(set(frame.top_k), {10, 20, 50})
        self.assertEqual(set(frame.frequency), {"weekly", "monthly"})
        self.assertTrue(frame.portfolio_mode.eq("long_only").all())
        self.assertEqual(int(frame.primary.sum()), 1)
        self.assertEqual(frame.loc[frame.primary, "transfer_id"].item(), "R9__MOM255__TOP20__MONTHLY")

    def test_authorization_keeps_lockbox_and_search_closed(self) -> None:
        program = tomllib.loads((ROOT / "config/experiments/round9/program.toml").read_text(encoding="utf-8"))
        auth = program["authorization"]
        self.assertTrue(auth["union_event_ledger"])
        self.assertTrue(auth["development_mom255_nav"])
        for key in ("lockbox", "model_search", "factor_search", "policy_search", "threshold_search", "position_search", "short_books", "wml"):
            self.assertFalse(auth[key])
        self.assertEqual(program["firewall"]["maximum_strategy_nav_date"], "2021-12-31")

    def test_lock_is_canonical_and_members_match(self) -> None:
        lock_path = ROOT / "config/experiments/round9/PREREG_LOCK.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        self.assertEqual(lock, payload(ROOT))
        self.assertEqual(set(lock["files"]), set(MEMBERS))
        for relative, expected in lock["files"].items():
            self.assertEqual(hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(), expected)


if __name__ == "__main__":
    unittest.main()
