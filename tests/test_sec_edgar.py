from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import tempfile
import unittest

import pandas as pd

from momentum_reversal.data.sec_edgar import (
    DEFAULT_SEC_RATE_LIMIT_PER_SECOND,
    GlobalRateLimiter,
    ImmutableFetchStore,
    SECClient,
    SECCooldownError,
    SECParseError,
    SECResponse,
    facts_as_of,
    normalize_sec_ticker,
    parse_browse_edgar_atom_single_cik,
    parse_company_tickers,
    parse_companyfacts,
    parse_submissions,
    submission_history_file_names,
)


def _submission_columns(rows: list[dict[str, object]]) -> dict[str, list[object]]:
    fields = (
        "accessionNumber",
        "form",
        "filingDate",
        "reportDate",
        "acceptanceDateTime",
        "primaryDocument",
    )
    return {field: [row[field] for row in rows] for field in fields}


def _submissions_payloads() -> tuple[dict[str, object], dict[str, object]]:
    original = {
        "accessionNumber": "0000320193-21-000001",
        "form": "10-K",
        "filingDate": "2021-02-01",
        "reportDate": "2020-12-31",
        "acceptanceDateTime": "2021-02-01T21:10:00.000Z",
        "primaryDocument": "original10k.htm",
    }
    amendment = {
        "accessionNumber": "0000320193-21-000002",
        "form": "10-K/A",
        "filingDate": "2021-02-10",
        "reportDate": "2020-12-31",
        "acceptanceDateTime": "2021-02-10T20:05:00.000Z",
        "primaryDocument": "amended10k.htm",
    }
    root = {
        "cik": 320193,
        "filings": {
            "recent": _submission_columns([amendment]),
            "files": [
                {
                    "name": "CIK0000320193-submissions-001.json",
                    "filingFrom": "2010-01-01",
                    "filingTo": "2021-02-01",
                }
            ],
        },
    }
    history = _submission_columns([original])
    return root, history


def _companyfacts_payload() -> dict[str, object]:
    return {
        "cik": 320193,
        "entityName": "Synthetic Company",
        "facts": {
            "us-gaap": {
                "Assets": {
                    "label": "Assets",
                    "description": "Total assets",
                    "units": {
                        "USD": [
                            {
                                "end": "2020-12-31",
                                "val": 100,
                                "accn": "0000320193-21-000001",
                                "fy": 2020,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2021-02-01",
                                "frame": "CY2020Q4I",
                            },
                            {
                                "end": "2020-12-31",
                                "val": 110,
                                "accn": "0000320193-21-000002",
                                "fy": 2020,
                                "fp": "FY",
                                "form": "10-K/A",
                                "filed": "2021-02-10",
                                "frame": "CY2020Q4I",
                            },
                        ]
                    },
                },
                "Revenues": {
                    "label": "Revenue",
                    "description": "Annual revenue",
                    "units": {
                        "USD": [
                            {
                                "start": "2020-01-01",
                                "end": "2020-12-31",
                                "val": 50,
                                "accn": "0000320193-21-000001",
                                "fy": 2020,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2021-02-01",
                                "frame": "CY2020",
                            }
                        ]
                    },
                },
            }
        },
    }


class _FakeClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


class SECClientTests(unittest.TestCase):
    def test_declared_user_agent_global_throttle_and_immutable_idempotence(self) -> None:
        body = b'{"ok":true}'
        calls: list[dict[str, object]] = []

        def transport(
            url: str, *, headers: dict[str, str], timeout: float
        ) -> SECResponse:
            calls.append({"url": url, "headers": dict(headers), "timeout": timeout})
            return SECResponse(
                status=200,
                body=body,
                headers={"Content-Type": "application/json", "ETag": '"v1"'},
                url=url,
            )

        with tempfile.TemporaryDirectory() as temporary:
            store = ImmutableFetchStore(temporary)
            fake_time = _FakeClock()
            client = SECClient(
                user_agent="MomentumResearch research@example.com",
                raw_store=store,
                transport=transport,
                limiter=GlobalRateLimiter(),
                clock=fake_time.clock,
                sleeper=fake_time.sleep,
                now=lambda: datetime(2026, 8, 20, tzinfo=timezone.utc),
            )
            url = "https://data.sec.gov/submissions/CIK0000320193.json"
            first = client.get(url)
            second = client.get(url)

            self.assertEqual(
                client.rate_limit_per_second,
                DEFAULT_SEC_RATE_LIMIT_PER_SECOND,
            )
            self.assertEqual(calls[0]["headers"]["User-Agent"], client.user_agent)
            self.assertEqual(calls[0]["headers"]["Accept-Encoding"], "gzip, deflate")
            self.assertEqual(len(fake_time.sleeps), 1)
            self.assertAlmostEqual(fake_time.sleeps[0], 0.2)
            self.assertEqual(first.record.record_id, second.record.record_id)
            self.assertEqual(first.record.raw_path, second.record.raw_path)
            self.assertEqual(first.record.sha256, hashlib.sha256(body).hexdigest())
            self.assertEqual(first.record.raw_path.read_bytes(), body)
            self.assertEqual(len(store.ledger_records()), 1)
            self.assertEqual(
                len(store.ledger_path.read_text(encoding="utf-8").splitlines()),
                1,
            )
            self.assertEqual(len(list(store.objects_root.rglob("*.bin"))), 1)

    def test_user_agent_must_identify_project_and_contact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "user_agent"):
                SECClient(
                    user_agent="python-urllib",
                    raw_store=ImmutableFetchStore(temporary),
                    transport=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
                )

    def test_403_and_429_raise_cooldown_after_recording_response(self) -> None:
        for status in (403, 429):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temporary:
                url = "https://www.sec.gov/Archives/edgar/test.json"

                def transport(
                    requested_url: str,
                    *,
                    headers: dict[str, str],
                    timeout: float,
                    response_status: int = status,
                ) -> SECResponse:
                    del headers, timeout
                    return SECResponse(
                        status=response_status,
                        body=b"limited",
                        headers={"Retry-After": "600"},
                        url=requested_url,
                    )

                store = ImmutableFetchStore(temporary)
                client = SECClient(
                    user_agent="MomentumResearch research@example.com",
                    raw_store=store,
                    transport=transport,
                    limiter=GlobalRateLimiter(),
                    clock=lambda: 0.0,
                    sleeper=lambda _seconds: None,
                    now=lambda: datetime(2026, 8, 20, tzinfo=timezone.utc),
                )
                with self.assertRaises(SECCooldownError) as captured:
                    client.get(url)
                self.assertEqual(captured.exception.status_code, status)
                self.assertEqual(captured.exception.cooldown_seconds, 600)
                self.assertEqual(len(store.ledger_records()), 1)


class SECIdentityParserTests(unittest.TestCase):
    def test_company_tickers_and_atom_cik_normalization(self) -> None:
        payload = {
            "0": {"cik_str": 1067983, "ticker": " brk.b ", "title": "Berkshire"},
            "1": {"cik_str": 320193, "ticker": "aapl", "title": "Apple"},
        }
        frame = parse_company_tickers(json.dumps(payload).encode("utf-8"))
        self.assertEqual(frame["ticker"].tolist(), ["AAPL", "BRK-B"])
        self.assertEqual(frame.loc[frame["ticker"].eq("AAPL"), "cik"].item(), "0000320193")
        self.assertEqual(normalize_sec_ticker("bf/b"), "BF-B")

        atom = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <company-info><cik>320193</cik></company-info>
          <entry><cik>0000320193</cik></entry>
        </feed>
        """
        self.assertEqual(parse_browse_edgar_atom_single_cik(atom), "0000320193")

    def test_atom_rejects_ambiguous_ciks(self) -> None:
        atom = """<feed xmlns="http://www.w3.org/2005/Atom">
          <entry><cik>320193</cik></entry><entry><cik>1067983</cik></entry>
        </feed>"""
        with self.assertRaisesRegex(SECParseError, "one unique CIK"):
            parse_browse_edgar_atom_single_cik(atom)


class SECFilingAndFactParserTests(unittest.TestCase):
    def test_root_and_history_form_an_accession_ledger_with_amendment(self) -> None:
        root, history = _submissions_payloads()
        self.assertEqual(
            submission_history_file_names(root),
            ("CIK0000320193-submissions-001.json",),
        )
        ledger = parse_submissions(
            root,
            {"CIK0000320193-submissions-001.json": history},
        )

        self.assertEqual(
            ledger["accession"].tolist(),
            ["0000320193-21-000001", "0000320193-21-000002"],
        )
        self.assertEqual(ledger["form"].tolist(), ["10-K", "10-K/A"])
        self.assertEqual(ledger["is_amendment"].tolist(), [False, True])
        self.assertEqual(ledger["cik"].unique().tolist(), ["0000320193"])
        self.assertEqual(
            ledger.loc[1, "primary_document"],
            "amended10k.htm",
        )
        self.assertEqual(ledger.loc[1, "primaryDocument"], "amended10k.htm")
        self.assertEqual(
            ledger.loc[1, "acceptanceDateTime"], ledger.loc[1, "accepted_at"]
        )
        self.assertEqual(ledger.loc[1, "reportDate"], ledger.loc[1, "report_date"])
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(ledger["accepted_at"]))
        self.assertEqual(str(ledger["accepted_at"].dt.tz), "UTC")

    def test_companyfacts_join_acceptance_and_future_amendment_never_backwrites(self) -> None:
        root, history = _submissions_payloads()
        ledger = parse_submissions(root, [history])
        facts = parse_companyfacts(_companyfacts_payload(), ledger)

        self.assertEqual(len(facts), 3)
        self.assertFalse(facts["accepted_at"].isna().any())
        amended_asset = facts.query("concept == 'Assets' and is_amendment").iloc[0]
        self.assertEqual(amended_asset["value"], 110.0)
        self.assertEqual(
            amended_asset["accepted_at"],
            pd.Timestamp("2021-02-10T20:05:00Z"),
        )

        before = facts_as_of(facts, "2021-02-05T00:00:00Z")
        after = facts_as_of(facts, "2021-02-11T00:00:00Z")
        before_again = facts_as_of(facts, "2021-02-05T00:00:00Z")

        self.assertEqual(before.query("concept == 'Assets'")["value"].item(), 100.0)
        self.assertEqual(after.query("concept == 'Assets'")["value"].item(), 110.0)
        self.assertEqual(after.query("concept == 'Revenues'")["value"].item(), 50.0)
        pd.testing.assert_frame_equal(before, before_again)
        self.assertEqual(
            facts.query("concept == 'Assets'")["value"].tolist(),
            [100.0, 110.0],
        )

    def test_submissions_form_is_authoritative_and_fact_form_is_audited(self) -> None:
        root, history = _submissions_payloads()
        payload = _companyfacts_payload()
        payload["facts"]["us-gaap"]["Assets"]["units"]["USD"][1]["form"] = (
            "10-K"
        )
        ledger = parse_submissions(root, [history])
        facts = parse_companyfacts(payload, ledger)
        amended = facts.loc[
            facts["accession"].eq("0000320193-21-000002")
        ].iloc[0]
        self.assertEqual(amended["fact_form"], "10-K")
        self.assertEqual(amended["filing_form"], "10-K/A")
        self.assertEqual(amended["form"], "10-K/A")
        self.assertTrue(amended["form_mismatch"])

    def test_companyfacts_fail_closed_when_accession_has_no_acceptance_event(self) -> None:
        root, _history = _submissions_payloads()
        ledger = parse_submissions(root)
        with self.assertRaisesRegex(SECParseError, "missing from filing ledger"):
            parse_companyfacts(_companyfacts_payload(), ledger)

    def test_exact_orphan_alias_uses_unique_ledger_vintage(self) -> None:
        root, history = _submissions_payloads()
        ledger = parse_submissions(root, [history])
        payload = _companyfacts_payload()
        known = payload["facts"]["us-gaap"]["Assets"]["units"]["USD"][0]
        orphan = copy.deepcopy(known)
        orphan["accn"] = "0000320193-21-999999"
        # CRL/CRM-like duplicate: observation identity is unchanged while SEC
        # display metadata differs under the orphan accession.
        orphan["fy"] = 1999
        orphan["fp"] = "Q4"
        orphan["frame"] = "CY1999Q4I"
        payload["facts"]["us-gaap"]["Assets"]["units"]["USD"].append(orphan)

        facts = parse_companyfacts(payload, ledger)

        self.assertEqual(len(facts), 3)
        self.assertNotIn("0000320193-21-999999", set(facts["accession"]))
        retained = facts.loc[
            facts["accession"].eq("0000320193-21-000001")
            & facts["concept"].eq("Assets")
        ].iloc[0]
        self.assertEqual(retained["fy"], "2020")
        self.assertEqual(retained["frame"], "CY2020Q4I")
        self.assertEqual(
            retained["accepted_at"], pd.Timestamp("2021-02-01T21:10:00Z")
        )
        audit = facts.attrs["companyfacts_orphan_duplicate_resolution"]
        self.assertEqual(audit["orphan_duplicate_resolved_count"], 1)
        self.assertEqual(audit["cik"], "0000320193")
        self.assertEqual(
            audit["accessions"][0]["orphan_accession"],
            "0000320193-21-999999",
        )
        self.assertEqual(
            audit["accessions"][0]["ledger_accession"],
            "0000320193-21-000001",
        )

    def test_orphan_alias_core_drift_or_multiple_candidates_fails_closed(self) -> None:
        root, history = _submissions_payloads()
        ledger = parse_submissions(root, [history])
        payload = _companyfacts_payload()
        known = payload["facts"]["us-gaap"]["Assets"]["units"]["USD"][0]
        orphan = copy.deepcopy(known)
        orphan["accn"] = "0000320193-21-999999"
        orphan["val"] = 100.01
        payload["facts"]["us-gaap"]["Assets"]["units"]["USD"].append(orphan)
        with self.assertRaisesRegex(SECParseError, "exact duplicate observation"):
            parse_companyfacts(payload, ledger)

        payload = _companyfacts_payload()
        known = payload["facts"]["us-gaap"]["Assets"]["units"]["USD"][0]
        orphan = copy.deepcopy(known)
        orphan["accn"] = "0000320193-21-999999"
        orphan["filed"] = "2021-02-02"
        payload["facts"]["us-gaap"]["Assets"]["units"]["USD"].append(orphan)
        with self.assertRaisesRegex(SECParseError, "exact duplicate observation"):
            parse_companyfacts(payload, ledger)

        payload = _companyfacts_payload()
        duplicate = copy.deepcopy(
            payload["facts"]["us-gaap"]["Assets"]["units"]["USD"][0]
        )
        duplicate["accn"] = "0000320193-21-000002"
        payload["facts"]["us-gaap"]["Assets"]["units"]["USD"][1] = duplicate
        orphan = copy.deepcopy(duplicate)
        orphan["accn"] = "0000320193-21-999999"
        payload["facts"]["us-gaap"]["Assets"]["units"]["USD"].append(orphan)
        with self.assertRaisesRegex(SECParseError, "multiple exact filing ledger"):
            parse_companyfacts(payload, ledger)


if __name__ == "__main__":
    unittest.main()
