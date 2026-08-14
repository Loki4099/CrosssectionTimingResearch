from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from momentum_reversal.data import PITMembership, SecurityMaster
from momentum_reversal.pipelines.public_pit import (
    PublicPITFormatError,
    convert_fja05680_updated_csv,
    write_fja05680_prototype,
)


FIXTURE = Path(__file__).parent / "fixtures" / "fja05680_updated_sample.csv"


class PublicPITConverterTests(unittest.TestCase):
    def test_scoped_conversion_keeps_anchor_and_original_interval_boundaries(self) -> None:
        result = convert_fja05680_updated_csv(
            FIXTURE, research_start="2018-01-02", research_end="2019-12-31"
        )

        snapshot_dates = pd.DatetimeIndex(result.membership_snapshots["date"].unique())
        self.assertEqual(snapshot_dates.min(), pd.Timestamp("2017-12-29"))
        self.assertEqual(snapshot_dates.max(), pd.Timestamp("2019-01-02"))
        self.assertNotIn("yf_ticker::XYZ", set(result.membership_snapshots["sid"]))

        old = result.membership_intervals.query("raw_ticker == 'OLD'").iloc[0]
        self.assertEqual(old["effective_from"], pd.Timestamp("2017-12-29"))
        self.assertEqual(old["effective_to"], pd.Timestamp("2018-01-03"))

        brk = result.membership_intervals.query("raw_ticker == 'BRK.B'")
        self.assertEqual(len(brk), 2)
        universe = PITMembership.from_intervals(result.membership_intervals)
        self.assertIn("yf_ticker::OLD", universe.members_on("2018-01-02"))
        self.assertNotIn("yf_ticker::OLD", universe.members_on("2018-01-03"))

    def test_security_master_preserves_raw_ticker_and_only_changes_dot(self) -> None:
        result = convert_fja05680_updated_csv(
            FIXTURE, research_start="2018-01-02", research_end="2019-12-31"
        )
        row = result.security_master.query("raw_ticker == 'BRK.B'").iloc[0]
        self.assertEqual(row["sid"], "yf_ticker::BRK.B")
        self.assertEqual(row["ticker"], "BRK-B")
        self.assertTrue(pd.isna(row["valid_from"]))
        self.assertTrue(pd.isna(row["valid_to"]))

        master = SecurityMaster(result.security_master)
        asset = master.assets_on(
            "2016-10-01", provider="yfinance", sids=["yf_ticker::BRK.B"]
        )[0]
        self.assertEqual(asset.symbol, "BRK-B")

    def test_audit_and_identity_risk_are_explicit(self) -> None:
        result = convert_fja05680_updated_csv(FIXTURE)

        self.assertEqual(result.snapshot_audit["constituent_count"].tolist(), [3, 3, 2, 3, 4])
        codes = set(result.anomalies["code"])
        self.assertIn("ticker_derived_identity_prototype_only", codes)
        self.assertIn("ticker_reentry_or_reuse_unresolved", codes)
        self.assertIn("yahoo_dot_to_hyphen", codes)
        self.assertTrue(result.metadata["prototype_only"])
        self.assertFalse(result.metadata["formal_run_eligible"])
        expected_hash = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
        self.assertEqual(result.metadata["source_sha256"], expected_hash)

    def test_write_is_immutable_and_hashes_every_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "public-pit"
            written = write_fja05680_prototype(
                FIXTURE,
                destination,
                research_start="2018-01-02",
                research_end="2019-12-31",
            )
            manifest = json.loads(written.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "prototype_only")
            self.assertFalse(manifest["formal_run_eligible"])
            self.assertEqual(len(manifest["files"]), 5)
            for record in manifest["files"]:
                path = destination / record["path"]
                self.assertEqual(
                    hashlib.sha256(path.read_bytes()).hexdigest(), record["sha256"]
                )
            with self.assertRaises(FileExistsError):
                write_fja05680_prototype(FIXTURE, destination)

    def test_duplicate_dates_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bad.csv"
            path.write_text(
                'date,tickers\n2020-01-02,"A,B"\n2020-01-02,"A,C"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(PublicPITFormatError, "duplicate source dates"):
                convert_fja05680_updated_csv(path)


if __name__ == "__main__":
    unittest.main()
