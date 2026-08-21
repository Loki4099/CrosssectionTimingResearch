from pathlib import Path
import unittest
from scripts.build_xa05_prereg_lock import LOCK,build,canonical,validate
ROOT=Path(__file__).resolve().parents[1]
class TestXA05Prereg(unittest.TestCase):
    def test_design(self):validate(ROOT)
    def test_lock(self):self.assertEqual((ROOT/LOCK).read_bytes(),canonical(build(ROOT)))
if __name__=="__main__":unittest.main()
