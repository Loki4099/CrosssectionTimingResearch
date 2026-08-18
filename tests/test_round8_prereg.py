from __future__ import annotations
import hashlib,json,tomllib,unittest
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
LOCK=ROOT/"config/experiments/round8/PREREG_LOCK.json"
FROZEN_R8_LOCK_SHA256="81e526fd6cc939fd42a6cfd7c1fda3a2303a2a2e33d09be9cb7b5e4e6d12446f"
class Round8PreregTests(unittest.TestCase):
 def test_lock(self):
  self.assertNotEqual(FROZEN_R8_LOCK_SHA256,"TO_BE_FILLED_AFTER_FREEZE"); self.assertEqual(hashlib.sha256(LOCK.read_bytes()).hexdigest(),FROZEN_R8_LOCK_SHA256)
  for rel,val in json.loads(LOCK.read_text(encoding="utf-8"))["files"].items(): self.assertEqual(hashlib.sha256((ROOT/rel).read_bytes()).hexdigest(),val)
 def test_three_bounded_policies(self):
  r=pd.read_csv(ROOT/"config/experiments/round8/policy_registry.csv"); self.assertEqual(r.policy_id.tolist(),["P00_RSP_Y5_CLEAR","P01_RSP_RAW_RECOVERY","P02_RSP_A4_ISOTONIC"]); self.assertEqual(r.formal_incremental_hypothesis.sum(),2)
 def test_firewall(self):
  p=tomllib.loads((ROOT/"config/experiments/round8/program.toml").read_text(encoding="utf-8")); self.assertTrue(p["policies"]["risk_priority"]); self.assertFalse(p["authorization"]["lockbox"]); self.assertFalse(p["authorization"]["mom255_transfer"])
if __name__=="__main__": unittest.main()
