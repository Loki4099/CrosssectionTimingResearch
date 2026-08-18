from __future__ import annotations
import pandas as pd,unittest
from momentum_reversal.pipelines.round8_experiments import build_policy_states,_holm
class Round8ExperimentTests(unittest.TestCase):
 def test_risk_veto_has_priority(self):
  raw=pd.DataFrame({"week_id":["a","b"],"signal_session":pd.to_datetime(["2020-01-03","2020-01-10"]),"execution_session":pd.to_datetime(["2020-01-06","2020-01-13"]),"outer_year":[2020,2020],"predicted_risk":[2.,2.],"threshold_q75":[1.,1.],"alert_high":[True,True],"y5":[1.,1.],"raw_mae13":[.1,.1]})
  attack=pd.concat([pd.DataFrame({"attack_process_id":pid,"week_id":["a","b"],"predicted_attack":[1.,1.],"attack_high":[True,True]}) for pid in ["AX01_RAW_RSP_RECOVERY","AX02_RSP_A4_MONOTONE"]])
  reg=pd.DataFrame({"policy_id":["P00_RSP_Y5_CLEAR","P01_RSP_RAW_RECOVERY","P02_RSP_A4_ISOTONIC"]})
  out=build_policy_states(raw,attack,reg); self.assertTrue(out.state.eq("DEFENSE").all()); self.assertTrue(out.target_spy_weight.eq(.5).all())
 def test_holm(self): self.assertEqual(_holm(pd.Series([.01,.04]).to_numpy()).tolist(),[.02,.04])
if __name__=="__main__": unittest.main()
