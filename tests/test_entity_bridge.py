from __future__ import annotations

import unittest

import pandas as pd

from momentum_reversal.data.entity_bridge import (
    build_entity_cik_intervals,
    build_sec_name_candidates,
    build_security_alias_table,
    company_name_score,
    member_session_mapping_coverage,
    normalize_ticker,
    parse_sec_cik_lookup,
    resolve_entity_bridge,
    ticker_variants,
)


class EntityBridgeTests(unittest.TestCase):
    def test_sec_cik_lookup_and_exact_name_candidate(self) -> None:
        lookup = parse_sec_cik_lookup(
            "AETNA INC /PA/:1122304:\nOTHER:NAME CORP:12345:\n"
        )
        names = pd.DataFrame(
            {
                "sid": ["sec::AET"],
                "issuer_name": ["Aetna Inc"],
                "source": ["tiingo_metadata"],
            }
        )
        candidates = build_sec_name_candidates(names, lookup)
        self.assertEqual(candidates.iloc[0]["cik10"], "0001122304")
        self.assertEqual(float(candidates.iloc[0]["score"]), 1.0)

    def setUp(self) -> None:
        self.master = pd.DataFrame(
            {
                "sid": ["sec::META", "sec::OLD", "sec::BRK-B"],
                "ticker": ["META", "OLD", "BRK-B"],
            }
        )
        self.lineage = pd.DataFrame(
            {
                "canonical_sid": ["sec::META", "sec::OLD", "sec::BRK-B"],
                "source_sid": [
                    "yf_ticker::FB|yf_ticker::META",
                    "yf_ticker::OLD",
                    "yf_ticker::BRK.B",
                ],
                "identity_status": ["rename", "same", "same"],
            }
        )
        self.membership = pd.DataFrame(
            {
                "sid": ["sec::META", "sec::OLD", "sec::BRK-B"],
                "effective_from": pd.to_datetime(
                    ["2020-01-01", "2020-01-01", "2020-01-02"]
                ),
                "effective_to": pd.to_datetime([None, "2020-01-03", None]),
            }
        )

    def test_ticker_normalization_preserves_share_class_and_adds_variants(self) -> None:
        self.assertEqual(normalize_ticker("yf_ticker::brk/b"), "BRK-B")
        self.assertEqual(set(ticker_variants("BRK.B")), {"BRK.B", "BRK-B", "BRK_B"})

    def test_alias_table_includes_rename_chain(self) -> None:
        result = build_security_alias_table(
            self.master, self.lineage, self.membership
        ).set_index("sid")
        self.assertIn("FB", result.loc["sec::META", "ticker_aliases"].split("|"))
        self.assertIn("META", result.loc["sec::META", "ticker_aliases"].split("|"))
        self.assertIn("BRK.B", result.loc["sec::BRK-B", "ticker_aliases"].split("|"))

    def test_alias_span_remains_open_when_latest_membership_is_open(self) -> None:
        membership = pd.concat(
            [
                self.membership,
                pd.DataFrame(
                    {
                        "sid": ["sec::OLD"],
                        "effective_from": [pd.Timestamp("2021-01-01")],
                        "effective_to": [pd.NaT],
                    }
                ),
            ],
            ignore_index=True,
        )
        result = build_security_alias_table(
            self.master, self.lineage, membership
        ).set_index("sid")
        self.assertTrue(pd.isna(result.loc["sec::OLD", "membership_to"]))
        self.assertEqual(result.loc["sec::OLD", "membership_intervals"], 2)

    def test_conflicting_ticker_candidates_are_not_silently_selected(self) -> None:
        aliases = build_security_alias_table(self.master, self.lineage, self.membership)
        candidates = pd.DataFrame(
            [
                {"ticker": "FB", "cik10": "1", "source": "current"},
                {"ticker": "META", "cik10": "2", "source": "browse"},
                {"ticker": "BRK.B", "cik10": "3", "source": "browse"},
            ]
        )
        result = resolve_entity_bridge(aliases, candidates).set_index("sid")
        self.assertEqual(result.loc["sec::META", "review_status"], "review_ticker_conflict")
        self.assertTrue(pd.isna(result.loc["sec::META", "cik10"]))
        self.assertEqual(result.loc["sec::BRK-B", "cik10"], "0000000003")

    def test_name_match_requires_absolute_score_and_margin(self) -> None:
        aliases = build_security_alias_table(self.master, self.lineage, self.membership)
        ticker_candidates = pd.DataFrame(columns=["ticker", "cik10", "source"])
        name_candidates = pd.DataFrame(
            [
                {"sid": "sec::OLD", "cik10": "10", "score": 0.97, "source": "name", "matched_name": "Old Corp"},
                {"sid": "sec::OLD", "cik10": "11", "score": 0.80, "source": "name", "matched_name": "Old Holdings"},
            ]
        )
        result = resolve_entity_bridge(
            aliases, ticker_candidates, name_candidates=name_candidates
        ).set_index("sid")
        self.assertEqual(result.loc["sec::OLD", "cik10"], "0000000010")
        self.assertEqual(result.loc["sec::OLD", "review_status"], "verified_name_match")

    def test_manual_override_has_priority(self) -> None:
        aliases = build_security_alias_table(self.master, self.lineage, self.membership)
        candidates = pd.DataFrame(
            [{"ticker": "OLD", "cik10": "20", "source": "browse"}]
        )
        overrides = pd.DataFrame(
            [{"sid": "sec::OLD", "cik10": "21", "mapping_basis": "review", "review_status": "verified_override"}]
        )
        result = resolve_entity_bridge(
            aliases, candidates, overrides=overrides
        ).set_index("sid")
        self.assertEqual(result.loc["sec::OLD", "cik10"], "0000000021")

    def test_legal_successor_overrides_create_nonoverlapping_pit_intervals(self) -> None:
        aliases = build_security_alias_table(self.master, self.lineage, self.membership)
        candidates = pd.DataFrame(
            [{"ticker": "META", "cik10": "99", "source": "current"}]
        )
        overrides = pd.DataFrame(
            [
                {
                    "sid": "sec::META",
                    "cik10": "1",
                    "effective_from": "2020-01-01",
                    "effective_to": "2022-01-01",
                },
                {
                    "sid": "sec::META",
                    "cik10": "2",
                    "effective_from": "2022-01-01",
                    "effective_to": "",
                },
            ]
        )
        bridge = resolve_entity_bridge(aliases, candidates, overrides=overrides)
        summary = bridge.set_index("sid").loc["sec::META"]
        self.assertEqual(summary["cik10"], "0000000002")
        self.assertEqual(summary["review_status"], "verified_interval_override")
        intervals = build_entity_cik_intervals(bridge, overrides)
        meta = intervals.loc[intervals["sid"].eq("sec::META")]
        self.assertEqual(meta["cik10"].tolist(), ["0000000001", "0000000002"])
        coverage = member_session_mapping_coverage(
            meta,
            pd.DataFrame(
                {
                    "sid": ["sec::META"],
                    "effective_from": [pd.Timestamp("2020-01-01")],
                    "effective_to": [pd.NaT],
                }
            ),
            pd.date_range("2020-01-01", "2023-01-01", freq="D"),
            start=pd.Timestamp("2020-01-01"),
            end=pd.Timestamp("2023-01-01"),
        )
        self.assertEqual(
            coverage["mapped_member_sessions"], coverage["member_sessions"]
        )

    def test_member_session_coverage_uses_intervals(self) -> None:
        aliases = build_security_alias_table(self.master, self.lineage, self.membership)
        candidates = pd.DataFrame(
            [
                {"ticker": "META", "cik10": "1", "source": "browse"},
                {"ticker": "BRK-B", "cik10": "3", "source": "browse"},
            ]
        )
        bridge = resolve_entity_bridge(aliases, candidates)
        coverage = member_session_mapping_coverage(
            bridge,
            self.membership,
            pd.date_range("2020-01-01", "2020-01-05", freq="D"),
            start=pd.Timestamp("2020-01-01"),
            end=pd.Timestamp("2020-01-05"),
        )
        self.assertEqual(coverage["member_sessions"], 11)
        self.assertEqual(coverage["mapped_member_sessions"], 9)

    def test_company_name_score_is_suffix_insensitive(self) -> None:
        self.assertEqual(company_name_score("Aetna Inc.", "AETNA CORPORATION"), 1.0)


if __name__ == "__main__":
    unittest.main()
