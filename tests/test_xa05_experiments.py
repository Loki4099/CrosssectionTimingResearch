import unittest
import pandas as pd
from momentum_reversal.pipelines.xa05_experiments import _episodes
class TestXA05(unittest.TestCase):
    def test_drawdown_episode(self):
        n=pd.Series([1,1.1,.9,.8,1.1,1.2],index=pd.date_range("2020-01-01",periods=6));e=_episodes(n);self.assertEqual(len(e),1);self.assertAlmostEqual(e.depth.iloc[0],.8/1.1-1);self.assertTrue(e.recovered.iloc[0])
if __name__=="__main__":unittest.main()
