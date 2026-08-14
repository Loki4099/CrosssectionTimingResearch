from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

import pandas as pd

from momentum_reversal.data import TradingCalendar
from momentum_reversal.data.ken_french import convert_ken_french_daily_rf_zip
from momentum_reversal.data.qa import DataQualityError
from momentum_reversal.data.risk_free import (
    align_daily_risk_free,
    load_daily_risk_free_csv,
)


class RiskFreeTests(unittest.TestCase):
    def test_local_csv_requires_decimal_daily_units_and_full_coverage(self) -> None:
        sessions = pd.bdate_range("2024-01-02", periods=3)
        calendar = TradingCalendar(sessions)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rf.csv"
            pd.DataFrame(
                {"date": sessions, "rf_return": [0.0001, 0.0002, 0.0001]}
            ).to_csv(path, index=False)
            result = load_daily_risk_free_csv(
                path,
                calendar,
                research_start=sessions[0],
                end=sessions[-1],
            )
            self.assertEqual(len(result), 3)
            aligned = align_daily_risk_free(result, sessions[1:])
            self.assertEqual(list(aligned.index), list(sessions[1:]))
            self.assertAlmostEqual(aligned.iloc[0], 0.0002)

            pd.DataFrame(
                {"date": sessions, "rf_return": [5.0, 5.0, 5.0]}
            ).to_csv(path, index=False)
            with self.assertRaisesRegex(DataQualityError, "daily-decimal guardrail"):
                load_daily_risk_free_csv(
                    path,
                    calendar,
                    research_start=sessions[0],
                    end=sessions[-1],
                )

    def test_local_csv_missing_research_session_is_rejected(self) -> None:
        sessions = pd.bdate_range("2024-01-02", periods=3)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rf.csv"
            pd.DataFrame(
                {"date": sessions[:2], "rf_return": [0.0001, 0.0001]}
            ).to_csv(path, index=False)
            with self.assertRaisesRegex(DataQualityError, "every research session"):
                load_daily_risk_free_csv(
                    path,
                    TradingCalendar(sessions),
                    research_start=sessions[0],
                    end=sessions[-1],
                )

    def test_ken_french_percent_is_explicitly_divided_by_100(self) -> None:
        content = (
            "This file was created by CMPT_ME_BEME_RETS using the 202401 CRSP database.\n"
            ",Mkt-RF,SMB,HML,RF\n"
            "20240102,0.10,0.01,0.02,0.005\n"
            "20240103,-0.20,0.02,0.01,0.006\n"
            "\nAnnual Factors: January-December\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "french.zip"
            output = Path(temporary) / "rf.csv"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("F-F_Research_Data_Factors_daily.CSV", content)
            convert_ken_french_daily_rf_zip(source, output)
            converted = pd.read_csv(output)
            self.assertAlmostEqual(converted.loc[0, "rf_return"], 0.00005)
            self.assertAlmostEqual(converted.loc[1, "rf_return"], 0.00006)


if __name__ == "__main__":
    unittest.main()

