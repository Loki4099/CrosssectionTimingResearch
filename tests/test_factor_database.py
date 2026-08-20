from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd
import pandas.testing as pdt

from momentum_reversal.data.factor_database import (
    CORE_COLUMNS,
    assert_past_factor_invariance,
    build_factor_coverage_qa,
    build_factor_database,
    validate_factor_database,
    write_factor_database_bundle,
)


SIGNALS = pd.DatetimeIndex(["2024-01-31", "2024-02-29"])


def _membership() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "sid": ["A", "B", "C"],
            "effective_from": ["2020-01-01", "2020-01-01", "2024-02-01"],
            "effective_to": [pd.NaT, "2024-02-01", pd.NaT],
        }
    )


def _registry() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "factor_id": ["MKT", "FUND_V2", "ABSENT"],
            "source_definition_id": ["MKT", "FUND", "ABSENT"],
            "data_family": ["market", "fundamental", "market"],
        }
    )


def _market_panel(*, include_future: bool = False) -> pd.DataFrame:
    rows: list[dict[str, object]] = [
        {
            "signal_date": "2024-01-31",
            "sid": "A",
            "factor_id": "MKT",
            "raw_value": 1.0,
            "score": 1.0,
            "eligible": True,
            "missing_reason": pd.NA,
        },
        {
            "signal_date": "2024-01-31",
            "sid": "B",
            "factor_id": "MKT",
            "raw_value": 1.0,
            "score": 1.0,
            "eligible": True,
            "missing_reason": pd.NA,
        },
        {
            "signal_date": "2024-02-29",
            "sid": "A",
            "factor_id": "MKT",
            "raw_value": 2.0,
            "score": 2.0,
            "eligible": True,
            "missing_reason": pd.NA,
        },
        # Inactive factors are deliberately ignored by the database contract.
        {
            "signal_date": "2024-01-31",
            "sid": "A",
            "factor_id": "NOT_ACTIVE",
            "raw_value": 99.0,
            "score": 99.0,
            "eligible": True,
            "missing_reason": pd.NA,
        },
    ]
    if include_future:
        rows.append(
            {
                "signal_date": "2025-01-31",
                "sid": "A",
                "factor_id": "MKT",
                "raw_value": np.inf,
                "score": np.inf,
                "eligible": True,
                "missing_reason": pd.NA,
            }
        )
    return pd.DataFrame(rows)


def _fundamental_panel() -> pd.DataFrame:
    rows = pd.DataFrame(
        {
            "signal_date": [
                "2024-01-31",
                "2024-01-31",
                "2024-02-29",
                "2024-02-29",
            ],
            "sid": ["A", "B", "A", "C"],
            "factor_id": ["FUND"] * 4,
            "cik": ["0001", "0002", "0001", "0003"],
            "raw_value": [0.05, np.nan, 0.02, 0.09],
            "score": [0.5, np.nan, 0.2, 0.9],
            "definition_status": ["paper_canonical"] * 4,
            "source_accession": ["a1", pd.NA, "a2", "c1"],
            "missing_reason": [pd.NA, "no_available_filing", pd.NA, pd.NA],
            "data_gate": ["pass", "blocked_missing_filing", "pass", "pass"],
        }
    )
    rows["signal_date"] = pd.to_datetime(rows["signal_date"])
    return rows.set_index(["signal_date", "sid", "factor_id"])


def _database(*, future_market_row: bool = False) -> pd.DataFrame:
    return build_factor_database(
        _market_panel(include_future=future_market_row),
        _fundamental_panel(),
        _membership(),
        SIGNALS,
        _registry(),
    )


class FactorDatabaseTests(unittest.TestCase):
    def test_complete_key_space_source_mapping_and_stable_ranking(self) -> None:
        database = _database()

        self.assertEqual(len(database), 12)
        self.assertEqual(tuple(database.columns[: len(CORE_COLUMNS)]), CORE_COLUMNS)
        self.assertFalse(
            database.duplicated(["signal_date", "sid", "factor_id"]).any()
        )
        self.assertEqual(set(database["factor_id"]), {"MKT", "FUND_V2", "ABSENT"})
        self.assertNotIn("NOT_ACTIVE", set(database["factor_id"]))

        jan_market = database.loc[
            (database["signal_date"] == SIGNALS[0])
            & (database["factor_id"] == "MKT")
        ].set_index("sid")
        self.assertEqual(int(jan_market.loc["A", "rank"]), 1)
        self.assertEqual(int(jan_market.loc["B", "rank"]), 2)
        self.assertEqual(jan_market.loc["A", "percentile"], 1.0)
        self.assertEqual(jan_market.loc["B", "percentile"], 0.5)

        fundamental = database.loc[
            (database["signal_date"] == SIGNALS[0])
            & (database["sid"] == "A")
            & (database["factor_id"] == "FUND_V2")
        ].iloc[0]
        self.assertEqual(fundamental["source_factor_id"], "FUND")
        self.assertEqual(fundamental["source_panel"], "fundamental")
        self.assertEqual(fundamental["raw_value"], 0.05)
        self.assertEqual(fundamental["score"], 0.5)
        self.assertEqual(fundamental["cik"], "0001")

        absent = database.loc[database["factor_id"] == "ABSENT"]
        self.assertFalse(absent["eligible"].any())
        self.assertTrue(absent["raw_value"].isna().all())
        self.assertTrue(absent["rank"].isna().all())
        self.assertTrue(absent["percentile"].isna().all())
        self.assertTrue(absent["missing_reason"].eq("no_source_factor_row").all())

        missing_member_row = database.loc[
            (database["signal_date"] == SIGNALS[1])
            & (database["sid"] == "C")
            & (database["factor_id"] == "MKT")
        ].iloc[0]
        self.assertEqual(missing_member_row["missing_reason"], "no_source_factor_row")

    def test_legacy_score_only_fundamental_panel_copies_score_to_raw_value(self) -> None:
        legacy = _fundamental_panel().drop(columns="raw_value")
        database = build_factor_database(
            _market_panel(),
            legacy,
            _membership(),
            SIGNALS,
            _registry(),
        )
        row = database.loc[
            (database["signal_date"] == SIGNALS[0])
            & (database["sid"] == "A")
            & (database["factor_id"] == "FUND_V2")
        ].iloc[0]
        self.assertEqual(row["raw_value"], row["score"])

    def test_coverage_qa_is_emitted_by_factor_date_year_and_reason(self) -> None:
        qa = build_factor_coverage_qa(_database())

        self.assertEqual(set(qa), {"factor", "date", "year", "missing_reason"})
        factor = qa["factor"].set_index("factor_id")
        self.assertEqual(int(factor.loc["MKT", "total_rows"]), 4)
        self.assertEqual(int(factor.loc["MKT", "eligible_rows"]), 3)
        self.assertEqual(factor.loc["MKT", "coverage_rate"], 0.75)
        self.assertEqual(factor.loc["ABSENT", "coverage_rate"], 0.0)
        self.assertEqual(len(qa["date"]), 6)
        self.assertEqual(len(qa["year"]), 3)

        reasons = qa["missing_reason"].set_index(
            ["factor_id", "missing_reason"]
        )
        self.assertEqual(
            int(reasons.loc[("ABSENT", "no_source_factor_row"), "missing_rows"]),
            4,
        )
        self.assertEqual(
            int(reasons.loc[("FUND_V2", "no_available_filing"), "missing_rows"]),
            1,
        )

    def test_future_rows_do_not_change_the_requested_past_database(self) -> None:
        baseline = _database(future_market_row=False)
        future_augmented = _database(future_market_row=True)

        pdt.assert_frame_equal(baseline, future_augmented)
        self.assertTrue(
            assert_past_factor_invariance(
                baseline, future_augmented, through_date=SIGNALS[-1]
            )
        )

        changed = future_augmented.copy()
        row = changed.index[
            (changed["signal_date"] == SIGNALS[0])
            & (changed["sid"] == "A")
            & (changed["factor_id"] == "MKT")
        ][0]
        changed.loc[row, "score"] = -123.0
        with self.assertRaises(AssertionError):
            assert_past_factor_invariance(
                baseline, changed, through_date=SIGNALS[-1]
            )

    def test_duplicate_keys_and_infinity_are_hard_failures(self) -> None:
        duplicate_market = pd.concat(
            [_market_panel(), _market_panel().iloc[[0]]], ignore_index=True
        )
        with self.assertRaisesRegex(ValueError, "key must be unique"):
            build_factor_database(
                duplicate_market,
                _fundamental_panel(),
                _membership(),
                SIGNALS,
                _registry(),
            )

        invalid_market = _market_panel()
        invalid_market.loc[0, "score"] = np.inf
        with self.assertRaisesRegex(ValueError, "infinity"):
            build_factor_database(
                invalid_market,
                _fundamental_panel(),
                _membership(),
                SIGNALS,
                _registry(),
            )

        database = _database()
        database.loc[0, "raw_value"] = -np.inf
        with self.assertRaisesRegex(ValueError, "infinity"):
            validate_factor_database(database)

    def test_atomic_parquet_bundle_and_content_manifest(self) -> None:
        database = _database()
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            manifest = write_factor_database_bundle(database, directory)

            manifest_path = directory / "factor_content_manifest.json"
            self.assertTrue(manifest_path.is_file())
            on_disk = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(on_disk, manifest)
            self.assertEqual(len(manifest["files"]), 5)
            self.assertEqual(len(manifest["content_sha256"]), 64)

            entries = {item["logical_name"]: item for item in manifest["files"]}
            factor_entry = entries["factor_values"]
            factor_path = directory / factor_entry["path"]
            self.assertEqual(factor_entry["rows"], len(database))
            self.assertEqual(factor_entry["size_bytes"], factor_path.stat().st_size)
            self.assertEqual(
                factor_entry["sha256"],
                hashlib.sha256(factor_path.read_bytes()).hexdigest(),
            )
            schema_names = [item["name"] for item in factor_entry["schema"]]
            self.assertEqual(schema_names[: len(CORE_COLUMNS)], list(CORE_COLUMNS))
            self.assertEqual(len(list(directory.glob("*.parquet"))), 5)
            self.assertFalse(any(path.name.startswith(".") for path in directory.iterdir()))


if __name__ == "__main__":
    unittest.main()
