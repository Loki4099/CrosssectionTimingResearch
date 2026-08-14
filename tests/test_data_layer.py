from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from momentum_reversal.data.calendar import TradingCalendar
from momentum_reversal.data.corporate_actions import CorporateActionLedger
from momentum_reversal.data.membership import PITMembership
from momentum_reversal.data.provider import AssetRef, PriceRequest
from momentum_reversal.data.qa import (
    DataQualityError,
    build_universe_audit,
    require_execution_prices,
    summarize_universe_audit,
)
from momentum_reversal.data.schema import (
    DataSchemaError,
    canonicalize_prices,
    validate_canonical_prices,
)
from momentum_reversal.data.security_master import SecurityMaster
from momentum_reversal.data.storage import (
    DatasetLayout,
    ManifestStore,
    SnapshotExistsError,
    sha256_file,
)
from momentum_reversal.data.tradability import (
    TRADABILITY_OHLC_COLUMNS,
    TradabilityOverrideLedger,
)
from momentum_reversal.data.yfinance_provider import YFinanceProvider


FIXTURES = Path(__file__).parent / "fixtures"


class SchemaTests(unittest.TestCase):
    def test_canonicalize_long_prices(self) -> None:
        raw = pd.DataFrame(
            {
                "date": ["2020-01-03", "2020-01-02"],
                "sid": ["B", "A"],
                "tr_open": [20.0, 10.0],
                "tr_close": [21.0, 11.0],
            }
        )
        result = canonicalize_prices(raw)
        self.assertEqual(result.index.names, ["date", "sid"])
        self.assertTrue(result.index.is_monotonic_increasing)

    def test_duplicate_and_impossible_ohlc_fail(self) -> None:
        duplicate = pd.DataFrame(
            {
                "date": ["2020-01-02", "2020-01-02"],
                "sid": ["A", "A"],
                "tr_open": [10.0, 10.0],
                "tr_close": [11.0, 11.0],
            }
        )
        with self.assertRaises(DataSchemaError):
            canonicalize_prices(duplicate)

        bad = pd.DataFrame(
            {
                "date": ["2020-01-02"],
                "sid": ["A"],
                "tr_open": [10.0],
                "tr_high": [9.0],
                "tr_low": [8.0],
                "tr_close": [11.0],
            }
        )
        with self.assertRaises(DataSchemaError):
            validate_canonical_prices(bad)

    def test_cash_liquidation_is_an_explicit_supported_research_action(self) -> None:
        ledger = CorporateActionLedger(
            pd.DataFrame(
                [
                    {
                        "action_id": "terminal-research-cash",
                        "action_type": "cash_liquidation",
                        "legal_effective_date": "2020-01-03",
                        "apply_session": "2020-01-06",
                        "apply_phase": "pre_open",
                        "source_sid": "sec::OLD",
                        "target_sid": "",
                        "cash_per_source_share": 12.5,
                        "currency": "USD",
                        "target_shares_per_source_share": 0.0,
                        "fractional_treatment": "not_applicable",
                        "evidence_url": "https://example.test/audit-record",
                        "notes": "bounded research approximation",
                    }
                ]
            )
        )

        row = ledger.to_frame().iloc[0]
        self.assertEqual(row["action_type"], "cash_liquidation")
        self.assertEqual(float(row["cash_per_source_share"]), 12.5)

    def test_ohlc_validator_tolerates_machine_precision_boundary(self) -> None:
        adjusted = pd.DataFrame(
            {
                "date": ["2016-11-07"],
                "sid": ["CMS"],
                "tr_open": [30.687513],
                "tr_high": [31.203709 - 3.6e-15],
                "tr_low": [30.358345],
                "tr_close": [31.203709],
            }
        )
        validate_canonical_prices(adjusted)

        adjusted.loc[0, "tr_high"] = adjusted.loc[0, "tr_close"] - 1e-6
        with self.assertRaises(DataSchemaError):
            validate_canonical_prices(adjusted)


class TradabilityOverrideTests(unittest.TestCase):
    @staticmethod
    def _prices() -> pd.DataFrame:
        frame = pd.DataFrame(
            {
                "date": ["2020-01-02", "2020-01-03", "2020-01-03", "2020-01-06"],
                "sid": ["A", "A", "B", "B"],
                "volume": [100, 110, 200, 210],
                "source": ["tiingo", "tiingo", "yahoo", "yahoo"],
                "source_symbol": ["A", "A", "B", "B"],
            }
        )
        for offset, column in enumerate(TRADABILITY_OHLC_COLUMNS):
            frame[column] = np.array([10.0, 11.0, 20.0, 21.0]) + offset / 10
        return frame

    @staticmethod
    def _override(
        override_id: str,
        sid: str,
        start_date: str,
        end_date: str,
        interval_type: str,
    ) -> dict[str, str]:
        return {
            "override_id": override_id,
            "sid": sid,
            "start_date": start_date,
            "end_date": end_date,
            "interval_type": interval_type,
            "reason": "audited_price_outlier",
            "evidence": "bounded_test_audit",
            "notes": "",
        }

    def test_closed_and_half_open_masks_preserve_volume_and_source(self) -> None:
        prices = self._prices()
        ledger = TradabilityOverrideLedger(
            pd.DataFrame(
                [
                    self._override("a-half-open", "A", "2020-01-02", "2020-01-03", "half_open"),
                    self._override("b-closed", "B", "2020-01-03", "2020-01-03", "closed"),
                ]
            )
        )

        masked, audit = ledger.apply(prices)
        original = canonicalize_prices(
            prices,
            required_columns=TRADABILITY_OHLC_COLUMNS,
        )

        self.assertTrue(
            masked.loc[(pd.Timestamp("2020-01-02"), "A"), list(TRADABILITY_OHLC_COLUMNS)]
            .isna()
            .all()
        )
        self.assertTrue(
            masked.loc[(pd.Timestamp("2020-01-03"), "B"), list(TRADABILITY_OHLC_COLUMNS)]
            .isna()
            .all()
        )
        self.assertFalse(
            masked.loc[(pd.Timestamp("2020-01-03"), "A"), list(TRADABILITY_OHLC_COLUMNS)]
            .isna()
            .any()
        )
        pd.testing.assert_series_equal(masked["volume"], original["volume"])
        pd.testing.assert_series_equal(masked["source"], original["source"])
        pd.testing.assert_series_equal(masked["source_symbol"], original["source_symbol"])
        self.assertEqual(len(audit), 2)
        self.assertTrue(audit["masked_non_null_ohlc_count"].eq(8).all())

    def test_overlapping_intervals_fail_closed(self) -> None:
        overlapping = pd.DataFrame(
            [
                self._override("first", "A", "2020-01-02", "2020-01-03", "closed"),
                self._override("second", "A", "2020-01-03", "2020-01-06", "closed"),
            ]
        )
        with self.assertRaisesRegex(DataSchemaError, "overlapping"):
            TradabilityOverrideLedger(overlapping)

    def test_csv_loader_and_unmatched_override_fail_closed(self) -> None:
        unmatched = pd.DataFrame(
            [self._override("missing", "C", "2020-01-02", "2020-01-02", "closed")]
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tradability.csv"
            unmatched.to_csv(path, index=False)
            ledger = TradabilityOverrideLedger.from_csv(path)

        with self.assertRaisesRegex(DataSchemaError, "matched no price rows"):
            ledger.apply(self._prices())


class MembershipTests(unittest.TestCase):
    def test_invalid_interval_date_cannot_become_open_ended(self) -> None:
        frame = pd.DataFrame(
            {
                "sid": ["A"],
                "effective_from": ["2020-01-01"],
                "effective_to": ["not-a-date"],
            }
        )
        with self.assertRaisesRegex(DataSchemaError, "effective_to"):
            PITMembership.from_intervals(frame)

    def test_snapshot_loader_uses_latest_past_snapshot(self) -> None:
        membership = PITMembership.from_csv(FIXTURES / "membership_snapshots.csv")
        self.assertEqual(membership.members_on("2020-01-09"), ("A", "B"))
        self.assertEqual(membership.members_on("2020-01-10"), ("B", "C"))
        with self.assertRaises(KeyError):
            membership.members_on("2020-01-02")

    def test_interval_loader_uses_half_open_boundaries(self) -> None:
        membership = PITMembership.from_csv(FIXTURES / "membership_intervals.csv")
        self.assertEqual(membership.members_on("2020-01-09"), ("A", "B"))
        self.assertEqual(membership.members_on("2020-01-10"), ("B", "C"))

    def test_overlapping_intervals_fail(self) -> None:
        overlapping = pd.DataFrame(
            {
                "sid": ["A", "A"],
                "effective_from": ["2020-01-01", "2020-01-05"],
                "effective_to": ["2020-01-10", "2020-01-20"],
            }
        )
        with self.assertRaises(DataSchemaError):
            PITMembership.from_intervals(overlapping)


class CalendarTests(unittest.TestCase):
    def test_last_actual_session_and_next_session_across_holiday(self) -> None:
        sessions = pd.to_datetime(
            ["2020-06-29", "2020-06-30", "2020-07-01", "2020-07-02", "2020-07-06"]
        )
        calendar = TradingCalendar(sessions)
        weekly = calendar.last_sessions_of_week()
        self.assertIn(pd.Timestamp("2020-07-02"), weekly)
        self.assertEqual(
            calendar.next_session("2020-07-02"), pd.Timestamp("2020-07-06")
        )
        self.assertEqual(
            list(calendar.previous_sessions("2020-07-02", 3)),
            list(pd.to_datetime(["2020-06-30", "2020-07-01", "2020-07-02"])),
        )


class YFinanceProviderTests(unittest.TestCase):
    def test_total_return_conversion_and_inclusive_request(self) -> None:
        dates = pd.to_datetime(["2020-01-02", "2020-01-03"])
        fields = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
        columns = pd.MultiIndex.from_product([fields, ["AAA", "BBB"]])
        values = np.zeros((2, len(columns)), dtype=float)
        raw = pd.DataFrame(values, index=dates, columns=columns)
        for symbol, scale in (("AAA", 1.0), ("BBB", 2.0)):
            raw[("Open", symbol)] = [90.0 * scale, 99.0 * scale]
            raw[("High", symbol)] = [110.0 * scale, 112.0 * scale]
            raw[("Low", symbol)] = [80.0 * scale, 95.0 * scale]
            raw[("Close", symbol)] = [100.0 * scale, 110.0 * scale]
            raw[("Adj Close", symbol)] = [50.0 * scale, 55.0 * scale]
            raw[("Volume", symbol)] = [1000.0, 1200.0]

        calls: list[dict[str, object]] = []

        def fake_download(**kwargs: object) -> pd.DataFrame:
            calls.append(kwargs)
            return raw

        provider = YFinanceProvider(downloader=fake_download, threads=False)
        request = PriceRequest(
            [AssetRef("sid-a", "AAA"), AssetRef("sid-b", "BBB")],
            "2020-01-02",
            "2020-01-03",
        )
        result = provider.fetch_prices(request)
        self.assertEqual(calls[0]["end"], "2020-01-04")
        self.assertFalse(bool(calls[0]["auto_adjust"]))
        self.assertAlmostEqual(
            result.loc[(pd.Timestamp("2020-01-02"), "sid-a"), "tr_open"], 45.0
        )
        self.assertAlmostEqual(
            result.loc[(pd.Timestamp("2020-01-03"), "sid-b"), "tr_close"], 110.0
        )
        self.assertAlmostEqual(
            result.loc[(pd.Timestamp("2020-01-03"), "sid-b"), "raw_close"], 220.0
        )


class SecurityMasterTests(unittest.TestCase):
    def test_invalid_mapping_date_cannot_become_unbounded(self) -> None:
        frame = pd.DataFrame(
            {
                "sid": ["A"],
                "provider": ["yfinance"],
                "ticker": ["AAA"],
                "valid_from": ["not-a-date"],
            }
        )
        with self.assertRaisesRegex(DataSchemaError, "valid_from"):
            SecurityMaster(frame)

    def test_date_effective_symbol_mapping(self) -> None:
        master = SecurityMaster(
            pd.DataFrame(
                {
                    "sid": ["A", "A"],
                    "provider": ["yfinance", "yfinance"],
                    "ticker": ["OLD", "NEW"],
                    "valid_from": ["2010-01-01", "2020-01-01"],
                    "valid_to": ["2020-01-01", None],
                }
            )
        )
        self.assertEqual(master.assets_on("2019-01-01", provider="yfinance")[0].symbol, "OLD")
        self.assertEqual(master.assets_on("2020-01-01", provider="yfinance")[0].symbol, "NEW")


class QATests(unittest.TestCase):
    def setUp(self) -> None:
        sessions = pd.bdate_range("2018-01-02", periods=300)
        self.calendar = TradingCalendar(sessions)
        rows = []
        for date in sessions:
            rows.append((date, "A", 10.0, 10.0))
        for date in sessions[50:]:
            rows.append((date, "B", 20.0, 20.0))
        self.prices = pd.DataFrame(
            rows, columns=["date", "sid", "tr_open", "tr_close"]
        )
        self.membership = PITMembership.from_snapshots(
            pd.DataFrame(
                {"date": [sessions[-2], sessions[-2]], "sid": ["A", "B"]}
            )
        )
        self.signal_date = sessions[-2]
        self.execution_date = sessions[-1]

    def test_coverage_audit_does_not_use_future_open_for_eligibility(self) -> None:
        # Remove B's execution open while retaining complete close history only
        # from Jan 3 onward. A remains fully eligible.
        self.prices.loc[
            (self.prices["date"] == self.execution_date)
            & (self.prices["sid"] == "A"),
            "tr_open",
        ] = np.nan
        audit = build_universe_audit(
            self.prices,
            self.membership,
            [self.signal_date],
            self.calendar,
        ).set_index("sid")
        self.assertTrue(bool(audit.loc["A", "eligible"]))
        self.assertFalse(bool(audit.loc["A", "has_execution_open"]))
        self.assertFalse(bool(audit.loc["B", "eligible"]))
        summary = summarize_universe_audit(audit.reset_index())
        self.assertEqual(int(summary.loc[0, "member_count"]), 2)

    def test_calendar_month_endpoint_is_a_separate_formation_check(self) -> None:
        signal_date = self.signal_date
        denominator_period = signal_date.to_period("M") - 12
        month_ends = pd.Series(
            self.calendar.last_sessions_of_month(),
            index=self.calendar.last_sessions_of_month().to_period("M"),
        )
        denominator = pd.Timestamp(month_ends.loc[denominator_period])
        broken = self.prices.copy()
        broken.loc[
            (broken["date"] == denominator) & broken["sid"].eq("A"), "tr_close"
        ] = np.nan
        audit = build_universe_audit(
            broken, self.membership, [signal_date], self.calendar
        ).set_index("sid")
        self.assertTrue(bool(audit.loc["A", "has_mom_255_0_history"]))
        self.assertTrue(bool(audit.loc["A", "has_mom_255_21_history"]))
        self.assertFalse(bool(audit.loc["A", "has_mom_12_1_history"]))
        summary = summarize_universe_audit(audit.reset_index())
        self.assertEqual(float(summary.loc[0, "mom_12_1_history_coverage"]), 0.0)

    def test_missing_selected_execution_open_is_fatal(self) -> None:
        with self.assertRaises(DataQualityError):
            require_execution_prices(
                self.prices, self.calendar.sessions[0], ["A", "B"]
            )


class StorageTests(unittest.TestCase):
    def test_manifest_hash_and_immutability(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            layout = DatasetLayout(temporary)
            source = Path(temporary) / "raw-source.csv"
            source.write_text("a,b\n1,2\n", encoding="utf-8")
            store = ManifestStore(layout)
            path = store.write(
                "yf-20200101-v1",
                {"provider": "yfinance"},
                referenced_files=[source],
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["files"][0]["sha256"], sha256_file(source))
            with self.assertRaises(SnapshotExistsError):
                store.write("yf-20200101-v1", {})


if __name__ == "__main__":
    unittest.main()
