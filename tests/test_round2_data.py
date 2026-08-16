from __future__ import annotations

import io
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile

import pandas as pd

from momentum_reversal.data.provider import AssetRef
from momentum_reversal.data.qa import DataQualityError
from momentum_reversal.data.round2_market import (
    build_round2_decision_calendar,
    canonical_arrow_sha256,
    load_and_validate_r2a_config,
    normalize_cboe_vix_csv,
    normalize_cboe_vix_legacy_xls,
    normalize_french_daily_rf_zip,
)
from momentum_reversal.data.schema import DataSchemaError
from momentum_reversal.data.tiingo_provider import normalize_tiingo_response
from momentum_reversal.pipelines.round2_data import _read_verified_parent_raw


ROOT = Path(__file__).resolve().parents[1]


class R2AConfigTests(unittest.TestCase):
    def test_frozen_config_anchors_and_forbidden_actions(self) -> None:
        config = load_and_validate_r2a_config(
            ROOT / "config/data/round2/R2A_DATA.toml",
            project_root=ROOT,
        )
        self.assertEqual(config["status"], "design_frozen_data_not_built")
        self.assertEqual(config["long_line"]["status"], "ready_for_acquisition")
        self.assertEqual(config["pit_line"]["status"], "blocked_external_dependency")
        for key in ("allow_targets", "allow_features", "allow_models", "allow_backtests"):
            self.assertFalse(config[key])


class R2ASourceNormalizationTests(unittest.TestCase):
    @staticmethod
    def _tiingo_row() -> dict[str, object]:
        return {
            "date": "2020-08-31T00:00:00.000Z",
            "open": 100.0,
            "high": 110.0,
            "low": 90.0,
            "close": 105.0,
            "volume": 1000,
            "adjOpen": 50.0,
            "adjHigh": 55.0,
            "adjLow": 45.0,
            "adjClose": 52.5,
            "adjVolume": 2000,
            "divCash": 0.0,
            "splitFactor": 2.0,
        }

    def test_tiingo_preserves_raw_and_adjusted_volume(self) -> None:
        result = normalize_tiingo_response(
            [self._tiingo_row()], AssetRef("spy", "SPY")
        )
        row = result.loc[(pd.Timestamp("2020-08-31"), "spy")]
        self.assertEqual(float(row["volume"]), 1000.0)
        self.assertEqual(float(row["adjusted_volume"]), 2000.0)
        self.assertEqual(float(row["stock_splits"]), 2.0)

    def test_tiingo_rejects_missing_adjusted_volume(self) -> None:
        row = self._tiingo_row()
        del row["adjVolume"]
        with self.assertRaisesRegex(DataSchemaError, "adjVolume"):
            normalize_tiingo_response([row], AssetRef("spy", "SPY"))

    def test_cboe_vix_csv_normalizes_case_and_range(self) -> None:
        payload = "DATE,OPEN,HIGH,LOW,CLOSE\n01/02/2018,10,11,9,9.77\n01/03/2018,9,10,8,9.15\n"
        result = normalize_cboe_vix_csv(
            payload, start="2018-01-03", end="2018-01-03"
        )
        self.assertEqual(result["session_date"].tolist(), [pd.Timestamp("2018-01-03")])
        self.assertAlmostEqual(float(result.loc[0, "vix_close_percent"]), 9.15)
        self.assertEqual(result.loc[0, "provider"], "CBOE")

    def test_cboe_vix_rejects_duplicate_dates(self) -> None:
        payload = "DATE,CLOSE\n01/02/2018,9.77\n01/02/2018,9.78\n"
        with self.assertRaisesRegex(DataQualityError, "duplicate"):
            normalize_cboe_vix_csv(payload)

    def test_cboe_legacy_xls_uses_the_named_close_column(self) -> None:
        source = pd.DataFrame(
            {
                "Date": pd.to_datetime(["1999-12-30", "1999-12-31"]),
                "VIX Open": [24.0, 25.03],
                "VIX High": [25.0, 25.2],
                "VIX Low": [23.0, 24.45],
                "VIX Close": [24.7, 24.64],
            }
        )
        with patch("pandas.read_excel", return_value=source) as reader:
            result = normalize_cboe_vix_legacy_xls(
                b"synthetic-xls", start="1999-12-31", end="1999-12-31"
            )
        reader.assert_called_once()
        self.assertEqual(result["session_date"].tolist(), [pd.Timestamp("1999-12-31")])
        self.assertEqual(float(result.loc[0, "vix_close_percent"]), 24.64)

    def test_french_zip_preserves_percent_decimal_and_method_segments(self) -> None:
        csv_text = (
            "This file was created for testing\n"
            ",Mkt-RF,SMB,HML,RF\n"
            "20240531,0.10,0.00,0.00,0.01\n"
            "20240603,0.20,0.00,0.00,0.02\n"
            " Annual Factors: January-December \n"
        )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("F-F_Research_Data_Factors_daily.csv", csv_text)
        result = normalize_french_daily_rf_zip(buffer.getvalue())
        self.assertEqual(result["rf_percent_source"].tolist(), [0.01, 0.02])
        self.assertEqual(result["rf_simple_decimal"].tolist(), [0.0001, 0.0002])
        self.assertEqual(
            result["methodology_segment"].tolist(),
            ["legacy_tbill_through_2024_05", "ice_bofa_1m_tbill_from_2024_06"],
        )
        self.assertTrue(
            result["availability_policy"].eq("next_xnys_open_research_proxy").all()
        )


class R2ACalendarAndHashTests(unittest.TestCase):
    def test_calendar_uses_actual_holiday_sessions_and_next_open(self) -> None:
        result = build_round2_decision_calendar(
            start="2017-12-29", end="2018-02-15"
        )
        first = result.iloc[0]
        self.assertEqual(first["signal_session"], pd.Timestamp("2017-12-29"))
        self.assertEqual(first["execution_session"], pd.Timestamp("2018-01-02"))
        self.assertIn("execution_not_monday", first["holiday_flags"])
        self.assertEqual(first["next_1w_execution"], pd.Timestamp("2018-01-08"))
        self.assertEqual(first["next_4w_execution"], pd.Timestamp("2018-01-29"))
        self.assertEqual(str(first["signal_timestamp_et"].tz), "America/New_York")

    def test_calendar_explicitly_supports_the_full_1993_start(self) -> None:
        result = build_round2_decision_calendar(
            start="1993-01-29", end="1993-04-30"
        )
        self.assertEqual(result.iloc[0]["signal_session"], pd.Timestamp("1993-01-29"))
        self.assertEqual(result.iloc[0]["execution_session"], pd.Timestamp("1993-02-01"))

    def test_canonical_arrow_hash_is_order_invariant_and_rejects_duplicates(self) -> None:
        frame = pd.DataFrame(
            {
                "session_date": pd.to_datetime(["2020-01-03", "2020-01-02"]),
                "value": [2.0, 1.0],
            }
        )
        first = canonical_arrow_sha256(frame, primary_key=["session_date"])
        second = canonical_arrow_sha256(
            frame.iloc[::-1].reset_index(drop=True), primary_key=["session_date"]
        )
        self.assertEqual(first, second)
        duplicate = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
        with self.assertRaisesRegex(DataQualityError, "not unique"):
            canonical_arrow_sha256(duplicate, primary_key=["session_date"])

    def test_reused_raw_requires_parent_manifest_bytes_and_hash(self) -> None:
        from tempfile import TemporaryDirectory
        import hashlib

        with TemporaryDirectory() as directory:
            source = Path(directory)
            raw = source / "raw"
            raw.mkdir()
            path = raw / "source.bin"
            path.write_bytes(b"frozen-source")
            manifest = {
                "files": [
                    {
                        "path": "raw/source.bin",
                        "size_bytes": len(b"frozen-source"),
                        "sha256": hashlib.sha256(b"frozen-source").hexdigest(),
                    }
                ]
            }
            self.assertEqual(
                _read_verified_parent_raw(source, manifest, "raw/source.bin"),
                b"frozen-source",
            )
            path.write_bytes(b"tampered")
            with self.assertRaisesRegex(DataQualityError, "byte count mismatch"):
                _read_verified_parent_raw(source, manifest, "raw/source.bin")


if __name__ == "__main__":
    unittest.main()
