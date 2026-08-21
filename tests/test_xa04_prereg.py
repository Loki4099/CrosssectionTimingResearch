from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

from scripts.build_xa04_prereg_lock import LOCK, build, canonical, validate


ROOT = Path(__file__).resolve().parents[1]


class XA04PreregTests(unittest.TestCase):
    def test_registered_design_is_coherent(self) -> None:
        validate(ROOT)

    def test_lock_is_canonical_and_current(self) -> None:
        path = ROOT / LOCK
        self.assertTrue(path.exists())
        expected = canonical(build(ROOT))
        self.assertEqual(path.read_bytes(), expected)
        payload = json.loads(expected)
        self.assertEqual(payload["member_count"], 7)
        for rel, item in payload["files"].items():
            raw = (ROOT / rel).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), item["sha256"])
            self.assertEqual(len(raw), item["size_bytes"])


if __name__ == "__main__":
    unittest.main()
