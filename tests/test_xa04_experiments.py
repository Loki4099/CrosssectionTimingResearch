from __future__ import annotations
import unittest
import numpy as np
import pandas as pd
from momentum_reversal.pipelines.xa04_experiments import _bh,_leaf_audit

class XA04ExperimentTests(unittest.TestCase):
    def test_bh(self)->None:
        got=_bh(pd.Series([.01,.04,.20]));self.assertTrue(np.allclose(got,[.03,.06,.20]))
    def test_leaf_audit_accepts_balanced_dates(self)->None:
        class Booster:
            def predict(self,x,pred_leaf=False):return np.tile((np.arange(len(x))%2)[:,None],(1,2))
        class Model:
            booster_=Booster()
        dates=pd.Series(pd.date_range("2014-01-03",periods=104,freq="7D").repeat(2))
        matrix=pd.DataFrame({"x":np.arange(len(dates))});weights=np.repeat(.5,len(dates))
        p={"models":{"lightgbm_leaf_min_unique_dates_weekly":26,"lightgbm_leaf_min_neff_dates_weekly":13.0,"lightgbm_leaf_max_single_year_mass":.80}}
        self.assertTrue(_leaf_audit(Model(),matrix,dates,weights,"weekly",p)["passed"].all())
if __name__=="__main__":unittest.main()
