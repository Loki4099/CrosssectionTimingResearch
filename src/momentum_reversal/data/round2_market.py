"""Round 2 market-data normalization and causality-safe calendar helpers.

This module deliberately stops before targets, features, models, or backtests.
It implements the R2A_DATA acquisition/QA boundary frozen in
``docs/20_experiments/R2A_DATA/design.md``.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from pathlib import Path
import tomllib
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile

import numpy as np
import pandas as pd

from .qa import DataQualityError


CBOE_VIX_HISTORY_URL = (
    "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
)
CBOE_VIX_LEGACY_URL = "https://cdn.cboe.com/resources/us/indices/vixarchive.xls"
KEN_FRENCH_DAILY_FACTORS_URL = (
    "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
    "F-F_Research_Data_Factors_daily_CSV.zip"
)


def load_and_validate_r2a_config(
    path: str | Path,
    *,
    project_root: str | Path,
) -> dict[str, Any]:
    """Load the frozen R2A config and verify its document anchors."""

    config_path = Path(path).resolve()
    root = Path(project_root).resolve()
    payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
    if payload.get("batch_id") != "R2A_DATA":
        raise DataQualityError("R2A config has the wrong batch_id")
    for forbidden in ("allow_targets", "allow_features", "allow_models", "allow_backtests"):
        if payload.get(forbidden) is not False:
            raise DataQualityError(f"R2A config must keep {forbidden}=false")
    for path_key, hash_key in (
        ("design_path", "design_sha256"),
        ("program_path", "program_sha256"),
    ):
        anchored = (root / str(payload[path_key])).resolve()
        try:
            anchored.relative_to(root)
        except ValueError:
            raise DataQualityError(f"R2A anchor escapes project root: {anchored}") from None
        if not anchored.is_file():
            raise FileNotFoundError(anchored)
        actual = sha256_file(anchored)
        if actual != str(payload[hash_key]).lower():
            raise DataQualityError(
                f"R2A {path_key} hash mismatch: expected={payload[hash_key]} actual={actual}"
            )
    return payload


def download_public_bytes(url: str, *, timeout: float = 60.0) -> bytes:
    """Download one public file without cookies or credentials."""

    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be positive and finite")
    request = Request(
        url,
        headers={"User-Agent": "momentum-reversal-research/0.1 R2A_DATA"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            status = int(getattr(response, "status", 200))
            payload = response.read()
    except HTTPError as error:
        raise DataQualityError(f"public data request failed with HTTP {error.code}") from None
    except (URLError, TimeoutError, OSError):
        raise DataQualityError("public data request failed") from None
    if not 200 <= status < 300 or not payload:
        raise DataQualityError(f"public data request returned unusable status={status}")
    return payload


def download_tiingo_eod_json(
    *,
    symbol: str,
    start: object,
    end: object,
    project_root: str | Path,
    timeout: float = 60.0,
) -> bytes:
    """Download one Tiingo EOD JSON response while keeping the token private."""

    from urllib.parse import quote, urlencode

    from .tiingo_provider import resolve_tiingo_api_token

    if not symbol.strip():
        raise ValueError("symbol must be non-empty")
    token = resolve_tiingo_api_token(project_root=project_root)
    url = f"https://api.tiingo.com/tiingo/daily/{quote(symbol, safe='')}/prices"
    query = urlencode(
        {
            "startDate": _session_date(start).strftime("%Y-%m-%d"),
            "endDate": _session_date(end).strftime("%Y-%m-%d"),
        }
    )
    request = Request(
        f"{url}?{query}",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Token {token}",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            status = int(getattr(response, "status", 200))
            payload = response.read()
    except HTTPError as error:
        raise DataQualityError(f"Tiingo EOD request failed with HTTP {error.code}") from None
    except (URLError, TimeoutError, OSError):
        raise DataQualityError("Tiingo EOD request failed") from None
    if not 200 <= status < 300 or not payload:
        raise DataQualityError(f"Tiingo EOD request returned unusable status={status}")
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise DataQualityError("Tiingo EOD returned invalid JSON") from None
    if not isinstance(parsed, list) or not parsed:
        raise DataQualityError("Tiingo EOD returned no rows")
    return payload


def normalize_cboe_vix_csv(
    payload: bytes | str,
    *,
    start: object | None = None,
    end: object | None = None,
) -> pd.DataFrame:
    """Normalize Cboe's official VIX history CSV to a unique daily close."""

    text = payload.decode("utf-8-sig", errors="strict") if isinstance(payload, bytes) else payload
    try:
        source = pd.read_csv(io.StringIO(text))
    except (pd.errors.ParserError, UnicodeError) as error:
        raise DataQualityError("Cboe VIX CSV cannot be parsed") from error
    normalized_columns = {str(column).strip().casefold(): column for column in source.columns}
    if "date" not in normalized_columns or "close" not in normalized_columns:
        raise DataQualityError("Cboe VIX CSV must contain DATE and CLOSE")
    frame = pd.DataFrame(
        {
            "session_date": pd.to_datetime(
                source[normalized_columns["date"]], errors="coerce"
            ),
            "vix_close_percent": pd.to_numeric(
                source[normalized_columns["close"]], errors="coerce"
            ),
        }
    )
    if frame["session_date"].isna().any():
        raise DataQualityError("Cboe VIX CSV contains invalid dates")
    frame["session_date"] = frame["session_date"].dt.tz_localize(None).dt.normalize()
    invalid = (
        frame["vix_close_percent"].isna()
        | ~np.isfinite(frame["vix_close_percent"])
        | (frame["vix_close_percent"] < 0)
    )
    if invalid.any():
        raise DataQualityError("Cboe VIX CSV contains invalid closes")
    if frame["session_date"].duplicated().any():
        raise DataQualityError("Cboe VIX CSV contains duplicate dates")
    if start is not None:
        frame = frame.loc[frame["session_date"] >= _session_date(start)]
    if end is not None:
        frame = frame.loc[frame["session_date"] <= _session_date(end)]
    if frame.empty:
        raise DataQualityError("Cboe VIX CSV has no rows in requested interval")
    frame["provider"] = "CBOE"
    return frame.sort_values("session_date", kind="mergesort").reset_index(drop=True)


def normalize_cboe_vix_legacy_xls(
    payload: bytes,
    *,
    start: object | None = None,
    end: object | None = None,
) -> pd.DataFrame:
    """Normalize Cboe's official 1990-2003 VIX XLS reconciliation file."""

    try:
        source = pd.read_excel(
            io.BytesIO(payload), sheet_name="OHLC", header=1, engine="xlrd"
        )
    except (ImportError, ValueError, OSError) as error:
        raise DataQualityError("Cboe legacy VIX XLS cannot be parsed") from error
    normalized_columns = {str(column).strip().casefold(): column for column in source.columns}
    if "date" not in normalized_columns or "vix close" not in normalized_columns:
        raise DataQualityError("Cboe legacy VIX XLS must contain Date and VIX Close")
    frame = pd.DataFrame(
        {
            "session_date": pd.to_datetime(
                source[normalized_columns["date"]], errors="coerce"
            ),
            "vix_close_percent": pd.to_numeric(
                source[normalized_columns["vix close"]], errors="coerce"
            ),
        }
    ).dropna(subset=["session_date", "vix_close_percent"])
    frame["session_date"] = frame["session_date"].dt.tz_localize(None).dt.normalize()
    invalid = ~np.isfinite(frame["vix_close_percent"]) | (frame["vix_close_percent"] < 0)
    if invalid.any():
        raise DataQualityError("Cboe legacy VIX XLS contains invalid closes")
    if frame["session_date"].duplicated().any():
        raise DataQualityError("Cboe legacy VIX XLS contains duplicate dates")
    if start is not None:
        frame = frame.loc[frame["session_date"] >= _session_date(start)]
    if end is not None:
        frame = frame.loc[frame["session_date"] <= _session_date(end)]
    if frame.empty:
        raise DataQualityError("Cboe legacy VIX XLS has no rows in requested interval")
    frame["provider"] = "CBOE"
    return frame.sort_values("session_date", kind="mergesort").reset_index(drop=True)


def normalize_french_daily_rf_zip(
    payload: bytes,
    *,
    start: object | None = None,
    end: object | None = None,
) -> pd.DataFrame:
    """Parse French daily factors and retain source-percent and decimal RF."""

    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if len(csv_names) != 1:
                raise DataQualityError(
                    f"expected one CSV in French ZIP, found {csv_names}"
                )
            text = archive.read(csv_names[0]).decode("utf-8-sig", errors="strict")
    except zipfile.BadZipFile as error:
        raise DataQualityError("French daily RF source is not a valid ZIP") from error

    rows: list[dict[str, object]] = []
    header_seen = False
    rf_position = -1
    for raw_row in csv.reader(io.StringIO(text)):
        cells = [cell.strip() for cell in raw_row]
        if not cells:
            continue
        if cells[0].casefold() in {"date", ""} and any(
            cell.casefold() == "rf" for cell in cells
        ):
            header_seen = True
            rf_position = next(
                index for index, cell in enumerate(cells) if cell.casefold() == "rf"
            )
            continue
        if not header_seen or not cells[0].isdigit() or len(cells[0]) != 8:
            if header_seen and rows:
                break
            continue
        if rf_position >= len(cells):
            raise DataQualityError("French daily RF row has too few columns")
        try:
            date = pd.to_datetime(cells[0], format="%Y%m%d", errors="raise")
            percent = float(cells[rf_position])
        except (TypeError, ValueError) as error:
            raise DataQualityError(f"invalid French daily RF row: {cells}") from error
        if not math.isfinite(percent) or percent <= -100:
            raise DataQualityError(f"invalid French daily RF percent: {cells}")
        rows.append(
            {
                "session_date": date.normalize(),
                "rf_percent_source": percent,
                "rf_simple_decimal": percent / 100.0,
            }
        )
    if not rows:
        raise DataQualityError("French daily RF ZIP contains no daily rows")
    frame = pd.DataFrame(rows)
    if frame["session_date"].duplicated().any():
        raise DataQualityError("French daily RF contains duplicate dates")
    if start is not None:
        frame = frame.loc[frame["session_date"] >= _session_date(start)]
    if end is not None:
        frame = frame.loc[frame["session_date"] <= _session_date(end)]
    if frame.empty:
        raise DataQualityError("French daily RF has no rows in requested interval")
    frame["rf_log"] = np.log1p(frame["rf_simple_decimal"].to_numpy(dtype=float))
    frame["source"] = "Kenneth R. French Data Library"
    frame["methodology_segment"] = np.where(
        frame["session_date"] <= pd.Timestamp("2024-05-31"),
        "legacy_tbill_through_2024_05",
        "ice_bofa_1m_tbill_from_2024_06",
    )
    frame["availability_policy"] = "next_xnys_open_research_proxy"
    return frame.sort_values("session_date", kind="mergesort").reset_index(drop=True)


def build_round2_decision_calendar(
    *,
    start: object,
    end: object,
    calendar_name: str = "XNYS",
) -> pd.DataFrame:
    """Build weekly signal/execution mappings without calculating outcomes."""

    try:
        import exchange_calendars as xcals
    except ImportError as error:  # pragma: no cover - optional dependency
        raise ImportError("exchange_calendars is required for R2A calendar") from error

    start_date = _session_date(start)
    end_date = _session_date(end)
    if start_date > end_date:
        raise ValueError("start must not be after end")
    # exchange_calendars otherwise instantiates a rolling default window that
    # can begin only ~20 years before today.  R2A requires the full 1993 span.
    calendar = xcals.get_calendar(calendar_name, start=start_date, end=end_date)
    sessions = pd.DatetimeIndex(
        calendar.sessions_in_range(start_date, end_date)
    ).tz_localize(None).normalize()
    if sessions.empty:
        raise DataQualityError("R2A calendar interval contains no sessions")
    weekly = pd.Series(sessions, index=sessions).groupby(sessions.to_period("W-FRI")).last()
    signal_sessions = pd.DatetimeIndex(weekly.to_numpy())
    execution_sessions: list[pd.Timestamp] = []
    kept_signals: list[pd.Timestamp] = []
    for signal in signal_sessions:
        position = sessions.searchsorted(signal, side="right")
        if position >= len(sessions):
            continue
        execution = sessions[position]
        if execution > end_date:
            continue
        kept_signals.append(signal)
        execution_sessions.append(execution)
    if not kept_signals:
        raise DataQualityError("R2A calendar contains no executable weekly signals")

    frame = pd.DataFrame(
        {
            "week_id": [f"R2W{index:05d}" for index in range(1, len(kept_signals) + 1)],
            "signal_session": kept_signals,
            "execution_session": execution_sessions,
        }
    )
    frame["signal_timestamp_et"] = [
        calendar.session_close(pd.Timestamp(date)).tz_convert("America/New_York")
        for date in frame["signal_session"]
    ]
    frame["execution_timestamp_et"] = [
        calendar.session_open(pd.Timestamp(date)).tz_convert("America/New_York")
        for date in frame["execution_session"]
    ]
    frame["next_1w_execution"] = frame["execution_session"].shift(-1)
    frame["next_4w_execution"] = frame["execution_session"].shift(-4)
    frame["signal_weekday"] = frame["signal_session"].dt.day_name()
    frame["execution_weekday"] = frame["execution_session"].dt.day_name()
    frame["holiday_flags"] = [
        ";".join(
            flag
            for flag, condition in (
                ("signal_not_friday", signal.weekday() != 4),
                ("execution_not_monday", execution.weekday() != 0),
            )
            if condition
        )
        for signal, execution in zip(
            frame["signal_session"], frame["execution_session"], strict=True
        )
    ]
    try:
        import importlib.metadata as metadata

        version = metadata.version("exchange-calendars")
    except Exception:  # pragma: no cover - packaging edge
        version = "unknown"
    frame["calendar_package_version"] = version
    return frame


def canonical_arrow_sha256(
    frame: pd.DataFrame,
    *,
    primary_key: tuple[str, ...] | list[str],
) -> str:
    """Hash stable-sort Arrow IPC bytes for deterministic content identity."""

    keys = list(primary_key)
    missing = [column for column in keys if column not in frame]
    if missing:
        raise DataQualityError(f"canonical hash missing primary-key columns: {missing}")
    if frame.duplicated(keys).any():
        raise DataQualityError(f"canonical hash primary key is not unique: {keys}")
    stable = frame.sort_values(keys, kind="mergesort").reset_index(drop=True)
    try:
        import pyarrow as pa
        import pyarrow.ipc as ipc
    except ImportError as error:  # pragma: no cover - optional dependency
        raise ImportError("pyarrow is required for canonical R2A hashing") from error
    table = pa.Table.from_pandas(stable, preserve_index=False)
    sink = pa.BufferOutputStream()
    with ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return hashlib.sha256(sink.getvalue().to_pybytes()).hexdigest()


def sha256_file(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _session_date(value: object) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_localize(None)
    return timestamp.normalize()
