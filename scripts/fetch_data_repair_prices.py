"""Fetch the bounded Yahoo/Tiingo repair queue for candidate data v2.

This script is intentionally narrow: it only retries evaluation-period gaps
and a fixed set of audited rename/query donors.  Tiingo responses are saved per
symbol so a rate-limit stop can be resumed without repeating successful calls.
Credentials are resolved by ``TiingoProvider`` and never persisted.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd

from momentum_reversal.data import (
    AssetRef,
    PriceRequest,
    TiingoProvider,
    YFinanceProvider,
    resolve_tiingo_api_token,
)
from momentum_reversal.pipelines.dataset import download_yfinance_symbols
from momentum_reversal.runtime import resolve_runtime_paths


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = resolve_runtime_paths(cwd=ROOT).data_root
PARENT_VERSION = "sp500-pit-yf-tiingo-prototype-2013warmup-2018eval-2026-v1"
SNAPSHOT_ID = "repair-v2-20260814"
START = pd.Timestamp("2013-01-02")
END = pd.Timestamp("2026-06-30")

# Unfinished exact Tiingo audit plus bounded high-impact gaps and query donors.
TIINGO_SYMBOLS = (
    "AGN", "ARNC", "ATVI", "CTLT", "CXO", "DAY", "DFS", "DISCA",
    "ETFC", "JNPR", "MRO", "NBL", "PEAK", "PXD", "RHT", "RTN",
    "VAR", "WCG", "WRK", "XL", "XLNX",
    "VRSK", "EVHC", "COL", "PARA", "PARAA", "FOX", "FOXA",
    "BFH", "FBIN", "GAP", "DINO", "TNL",
    "STI", "APC", "SBNY", "INFO", "HFC", "LLL", "TSS", "CA", "MON",
    "NFX", "DWDP", "SIVB",
)

QUERY_DONORS = ("BFH", "FBIN", "GAP", "DINO", "TNL", "DAY", "PARAA")
PERMA_NAME_HINTS = {
    "APC": "anadarko",
    "CA": "ca inc",
    "DWDP": "dowdupont",
    "DISCA": "discovery",
    "FOX": "twenty first century fox",
    "FOXA": "twenty first century fox",
    "HCP": "hcp inc",
    "INFO": "ihs markit",
    "LLL": "l3 technologies",
    "MON": "monsanto",
    "NFX": "newfield",
    "PEAK": "healthpeak",
    "PARA": "paramount global",
    "VIAC": "viacomcbs",
    "CBS": "cbs corporation",
    "SIVB": "svb financial",
    "STI": "suntrust",
    "TSS": "total system",
    "WRK": "westrock",
}


def main() -> None:
    parent = DATA_ROOT / "curated" / PARENT_VERSION
    membership = pd.read_parquet(parent / "membership.parquet")
    prices = pd.read_parquet(parent / "prices_daily.parquet")
    valid = prices.groupby("sid")["tr_close"].apply(lambda values: values.notna().any())
    active = _evaluation_sids(membership)
    missing_symbols = sorted(
        sid.removeprefix("yf_ticker::")
        for sid in active
        if not bool(valid.get(sid, False))
    )
    yahoo_symbols = tuple(sorted(set(missing_symbols).union(QUERY_DONORS)))

    rate_limited = _fetch_tiingo(TIINGO_SYMBOLS)
    if not rate_limited:
        _fetch_tiingo_permatickers(PERMA_NAME_HINTS)
    _fetch_yahoo(yahoo_symbols)


def _evaluation_sids(membership: pd.DataFrame) -> set[str]:
    start = pd.Timestamp("2017-12-29")
    end_exclusive = END + pd.Timedelta(days=1)
    effective_to = pd.to_datetime(membership["effective_to"])
    active = (pd.to_datetime(membership["effective_from"]) < end_exclusive) & (
        effective_to.isna() | (effective_to > start)
    )
    return set(membership.loc[active, "sid"].astype(str))


def _fetch_tiingo(symbols: tuple[str, ...]) -> bool:
    directory = DATA_ROOT / "raw" / "tiingo" / SNAPSHOT_ID
    symbol_dir = directory / "symbols"
    symbol_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = directory / "checkpoint.json"
    checkpoint = _read_json(checkpoint_path, default={"symbols": {}})
    provider = TiingoProvider(project_root=ROOT, timeout=45.0)

    rate_limited = False
    for symbol in symbols:
        existing = checkpoint["symbols"].get(symbol, {})
        output = symbol_dir / f"{symbol}.parquet"
        if existing.get("status") == "success" and output.is_file():
            continue
        try:
            frame = provider.fetch_prices(
                PriceRequest((AssetRef(f"tiingo_query::{symbol}", symbol),), START, END)
            ).reset_index()
            frame.to_parquet(output, index=False)
            checkpoint["symbols"][symbol] = {
                "status": "success",
                "rows": int(len(frame)),
                "first_date": str(frame["date"].min().date()),
                "last_date": str(frame["date"].max().date()),
            }
        except Exception as error:  # bounded acquisition audit; message has no credential
            checkpoint["symbols"][symbol] = {
                "status": "failed",
                "error_type": type(error).__name__,
                "message": str(error),
            }
            _write_json(checkpoint_path, checkpoint)
            if "status 429" in str(error):
                rate_limited = True
                break
        _write_json(checkpoint_path, checkpoint)

    frames = [pd.read_parquet(path) for path in sorted(symbol_dir.glob("*.parquet"))]
    perma_dir = directory / "perma_symbols"
    perma_frames = [pd.read_parquet(path) for path in sorted(perma_dir.glob("*.parquet"))] if perma_dir.is_dir() else []
    if perma_frames:
        replacement_sids = set(pd.concat(perma_frames, ignore_index=True)["sid"].astype(str))
        frames = [frame.loc[~frame["sid"].astype(str).isin(replacement_sids)] for frame in frames]
        frames.extend(perma_frames)
    if frames:
        pd.concat(frames, ignore_index=True).sort_values(["date", "sid"]).to_parquet(
            directory / "provider_prices.parquet", index=False
        )
    status_rows = [
        {"symbol": symbol, **payload}
        for symbol, payload in sorted(checkpoint["symbols"].items())
    ]
    pd.DataFrame(status_rows).to_csv(directory / "download_status.csv", index=False)
    return rate_limited


def _fetch_tiingo_permatickers(name_hints: dict[str, str]) -> None:
    directory = DATA_ROOT / "raw" / "tiingo" / SNAPSHOT_ID
    search_dir = directory / "search"
    output_dir = directory / "perma_symbols"
    search_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = directory / "permaticker_checkpoint.json"
    checkpoint = _read_json(checkpoint_path, default={"symbols": {}})
    token = resolve_tiingo_api_token(project_root=ROOT)
    provider = TiingoProvider(api_token=token, timeout=45.0)

    for symbol, hint in name_hints.items():
        output = output_dir / f"{symbol}.parquet"
        if checkpoint["symbols"].get(symbol, {}).get("status") == "success" and output.is_file():
            continue
        try:
            # Searching a recycled ticker returns the current security first.
            # Search the audited issuer name, then require both the historical
            # ticker and issuer-name match before using its stable identifier.
            payload = _tiingo_search(hint, token)
            (search_dir / f"{symbol}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            candidates = [
                row
                for row in payload
                if str(row.get("ticker", "")).upper() == symbol
                and _normalized_name(hint) in _normalized_name(str(row.get("name", "")))
                and str(row.get("permaTicker", "")).strip()
            ]
            if len(candidates) != 1:
                raise RuntimeError(f"expected one historical identity match, found {len(candidates)}")
            perma = str(candidates[0]["permaTicker"])
            frame = provider.fetch_prices(
                PriceRequest((AssetRef(f"tiingo_query::{symbol}", perma),), START, END)
            ).reset_index()
            frame["source_symbol"] = symbol
            frame.to_parquet(output, index=False)
            checkpoint["symbols"][symbol] = {
                "status": "success",
                "permaTicker": perma,
                "name": str(candidates[0].get("name", "")),
                "rows": int(len(frame)),
                "first_date": str(frame["date"].min().date()),
                "last_date": str(frame["date"].max().date()),
            }
        except Exception as error:
            checkpoint["symbols"][symbol] = {
                "status": "failed",
                "error_type": type(error).__name__,
                "message": str(error),
            }
            _write_json(checkpoint_path, checkpoint)
            if "status 429" in str(error):
                break
        _write_json(checkpoint_path, checkpoint)

    # Rebuild the combined provider file with successful permaticker rows taking
    # precedence over ambiguous exact-ticker responses.
    symbol_dir = directory / "symbols"
    frames = [pd.read_parquet(path) for path in sorted(symbol_dir.glob("*.parquet"))]
    perma_frames = [pd.read_parquet(path) for path in sorted(output_dir.glob("*.parquet"))]
    if perma_frames:
        replacement_sids = set(pd.concat(perma_frames, ignore_index=True)["sid"].astype(str))
        frames = [frame.loc[~frame["sid"].astype(str).isin(replacement_sids)] for frame in frames]
        frames.extend(perma_frames)
    if frames:
        pd.concat(frames, ignore_index=True).sort_values(["date", "sid"]).to_parquet(
            directory / "provider_prices.parquet", index=False
        )


def _tiingo_search(symbol: str, token: str) -> list[dict[str, object]]:
    request = Request(
        f"https://api.tiingo.com/tiingo/utilities/search/{quote(symbol, safe='')}",
        headers={"Accept": "application/json", "Authorization": f"Token {token}"},
        method="GET",
    )
    with urlopen(request, timeout=45.0) as response:  # noqa: S310 - fixed Tiingo host
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise RuntimeError("Tiingo search returned a non-list response")
    return payload


def _normalized_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _fetch_yahoo(symbols: tuple[str, ...]) -> None:
    directory = DATA_ROOT / "raw" / "yfinance" / SNAPSHOT_ID
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / "provider_prices.parquet"
    if output.exists():
        return
    provider = YFinanceProvider(repair=False, threads=True)
    try:
        raw, failures, acquisition_ids = download_yfinance_symbols(
            provider, symbols, start=START, end=END, batch_size=20
        )
    except Exception as error:
        # The fixed retry route is exhausted.  Preserve a bounded, explicit
        # failure result instead of repeatedly querying Yahoo.
        pd.DataFrame(
            {
                "symbol": list(symbols),
                "error_type": type(error).__name__,
                "message": "bounded Yahoo repair retry produced no usable rows",
            }
        ).to_csv(directory / "download_failures.csv", index=False)
        _write_json(
            directory / "request.json",
            {
                "start": str(START.date()),
                "end": str(END.date()),
                "symbols": list(symbols),
                "status": "failed_no_usable_rows",
                "auto_adjust": False,
                "actions": True,
                "repair": False,
                "keepna": True,
            },
        )
        return
    symbol_by_acquisition = {value: key for key, value in acquisition_ids.items()}
    frame = raw.reset_index()
    frame["source_symbol"] = frame["sid"].map(symbol_by_acquisition)
    frame["sid"] = "yf_query::" + frame["source_symbol"].astype(str)
    frame.sort_values(["date", "sid"]).to_parquet(output, index=False)
    failures.to_csv(directory / "download_failures.csv", index=False)
    _write_json(
        directory / "request.json",
        {
            "start": str(START.date()),
            "end": str(END.date()),
            "symbols": list(symbols),
            "auto_adjust": False,
            "actions": True,
            "repair": False,
            "keepna": True,
        },
    )


def _read_json(path: Path, *, default: dict[str, object]) -> dict[str, object]:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
