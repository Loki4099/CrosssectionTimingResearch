"""Build an immutable Yahoo/PIT dataset suitable for the frozen baselines."""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd

from momentum_reversal.data import (
    AssetRef,
    DatasetLayout,
    ManifestStore,
    PITMembership,
    ParquetStore,
    PriceProvider,
    PriceRequest,
    SecurityMaster,
    TradingCalendar,
    YFinanceProvider,
    build_universe_audit,
    canonicalize_prices,
    summarize_universe_audit,
    validate_canonical_prices,
    load_daily_risk_free_csv,
)
from momentum_reversal.data.qa import DataQualityError
from momentum_reversal.data.storage import SnapshotExistsError, sha256_file


@dataclass(frozen=True, slots=True)
class DatasetBuildConfig:
    data_root: Path
    dataset_version: str
    snapshot_id: str
    security_master_path: Path
    membership_path: Path
    research_start: pd.Timestamp
    price_start: pd.Timestamp
    end: pd.Timestamp
    pit_source: str
    pit_date_semantics: str
    benchmark_symbol: str
    benchmark_label: str
    benchmark_kind: str
    risk_free_path: Path | None = None
    risk_free_source: str | None = None
    batch_size: int = 50
    repair: bool = False
    calendar_source: str = "XNYS"
    max_snapshot_age_days: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "data_root", Path(self.data_root).resolve())
        object.__setattr__(
            self, "security_master_path", Path(self.security_master_path).resolve()
        )
        object.__setattr__(self, "membership_path", Path(self.membership_path).resolve())
        if self.risk_free_path is not None:
            object.__setattr__(self, "risk_free_path", Path(self.risk_free_path).resolve())
            if not self.risk_free_source or not self.risk_free_source.strip():
                raise ValueError("risk_free_source is required with risk_free_path")
        elif self.risk_free_source is not None:
            raise ValueError("risk_free_path is required with risk_free_source")
        for name in ("research_start", "price_start", "end"):
            value = pd.Timestamp(getattr(self, name))
            if value.tzinfo is not None:
                value = value.tz_localize(None)
            object.__setattr__(self, name, value.normalize())
        if self.price_start >= self.research_start:
            raise ValueError("price_start must precede research_start")
        if self.research_start > self.end:
            raise ValueError("research_start must be on or before end")
        if self.batch_size < 1:
            raise ValueError("batch_size must be positive")
        if not self.pit_source.strip():
            raise ValueError("pit_source must describe the actual membership source")
        if self.pit_date_semantics not in {"effective", "snapshot_asof"}:
            raise ValueError("pit_date_semantics must be effective or snapshot_asof")
        if not self.benchmark_symbol.strip() or not self.benchmark_label.strip():
            raise ValueError("benchmark symbol and label must be explicit")
        if self.benchmark_kind not in {"investable_proxy", "total_return_index"}:
            raise ValueError(
                "benchmark_kind must be investable_proxy or total_return_index"
            )
        if self.calendar_source not in {"XNYS", "observed"}:
            raise ValueError("calendar_source must be XNYS or observed")
        if self.max_snapshot_age_days < 0:
            raise ValueError("max_snapshot_age_days cannot be negative")


@dataclass(frozen=True, slots=True)
class DatasetBuildResult:
    dataset_version: str
    status: str
    manifest_path: Path
    curated_prices_path: Path
    qa_summary_path: Path
    failed_downloads: tuple[str, ...]


class _ProviderFactory(Protocol):
    def __call__(self) -> PriceProvider: ...


def build_yfinance_download_plan(
    security_master: SecurityMaster,
    membership_sids: tuple[str, ...] | list[str],
    *,
    price_start: object,
    end: object,
) -> pd.DataFrame:
    """Return explicit Yahoo ticker intervals for every historical PIT sid."""

    start_date = pd.Timestamp(price_start).normalize()
    end_date = pd.Timestamp(end).normalize()
    requested = set(map(str, membership_sids))
    mappings = security_master.frame
    mappings = mappings[
        mappings["provider"].str.casefold().eq("yfinance")
        & mappings["sid"].isin(requested)
    ].copy()
    mapping_start = mappings["valid_from"].fillna(start_date)
    mapping_end = mappings["valid_to"].fillna(end_date + pd.Timedelta(days=1))
    mappings = mappings.loc[(mapping_start <= end_date) & (mapping_end > start_date)].copy()
    mappings["download_from"] = mappings["valid_from"].fillna(start_date).clip(
        lower=start_date
    )
    mappings["download_to_exclusive"] = mappings["valid_to"].fillna(
        end_date + pd.Timedelta(days=1)
    ).clip(upper=end_date + pd.Timedelta(days=1))
    mappings["symbol"] = mappings["ticker"].astype(str).str.strip()
    missing = sorted(requested.difference(mappings["sid"]))
    if missing:
        raise DataQualityError(
            "PIT members lack an overlapping yfinance ticker mapping: "
            f"{missing[:25]}{' ...' if len(missing) > 25 else ''}"
        )
    if (mappings["symbol"] == "").any():
        bad = sorted(mappings.loc[mappings["symbol"] == "", "sid"].unique())
        raise DataQualityError(f"blank yfinance ticker mappings for sids: {bad}")

    # Yahoo's adjusted histories are normalized independently by query symbol.
    # A ticker change therefore cannot be joined merely because the two date
    # ranges are adjacent: doing so can manufacture a large false return at the
    # boundary.  The prototype deliberately refuses to infer an undocumented
    # link factor.  Use a provider with a permanent security id, or curate one
    # already-linked provider series under a single query symbol.
    symbols_per_sid = mappings.groupby("sid")["symbol"].nunique()
    multi_symbol_sids = sorted(symbols_per_sid[symbols_per_sid > 1].index.astype(str))
    if multi_symbol_sids:
        raise DataQualityError(
            "yfinance cannot safely join multiple ticker histories for one sid "
            "without an externally audited link factor; affected sids: "
            f"{multi_symbol_sids[:25]}{' ...' if len(multi_symbol_sids) > 25 else ''}"
        )

    # Even when each sid has one symbol, mappings may not overlap within a sid.
    for sid, group in mappings.sort_values("download_from").groupby("sid"):
        previous_end: pd.Timestamp | None = None
        for row in group.itertuples(index=False):
            current_start = pd.Timestamp(row.download_from)
            current_end = pd.Timestamp(row.download_to_exclusive)
            if previous_end is not None and current_start < previous_end:
                raise DataQualityError(f"overlapping yfinance mappings for sid={sid}")
            previous_end = current_end

    # Tickers can be reused by unrelated companies.  Yahoo exposes the query
    # symbol, not a permanent security id, so even non-overlapping mappings are
    # ambiguous: one downloaded history may silently represent only one of the
    # companies.  Refuse all cross-sid reuse rather than guessing by date.
    sids_per_symbol = mappings.groupby("symbol")["sid"].nunique()
    reused_symbols = sorted(sids_per_symbol[sids_per_symbol > 1].index.astype(str))
    if reused_symbols:
        raise DataQualityError(
            "yfinance symbols cannot be mapped to multiple stable sids without "
            "a permanent provider identifier; reused symbols: "
            f"{reused_symbols[:25]}{' ...' if len(reused_symbols) > 25 else ''}"
        )
    return mappings.loc[
        :, ["sid", "symbol", "download_from", "download_to_exclusive"]
    ].sort_values(["symbol", "download_from", "sid"], ignore_index=True)


def download_yfinance_symbols(
    provider: PriceProvider,
    symbols: tuple[str, ...] | list[str],
    *,
    start: object,
    end: object,
    batch_size: int,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, str]]:
    """Download unique symbols, retrying failed batches one symbol at a time."""

    unique_symbols = tuple(sorted(set(map(str, symbols))))
    acquisition_ids = {
        symbol: f"yf_acquisition_{position:06d}"
        for position, symbol in enumerate(unique_symbols, start=1)
    }
    successes: list[pd.DataFrame] = []
    failure_rows: list[dict[str, str]] = []

    for offset in range(0, len(unique_symbols), batch_size):
        batch = unique_symbols[offset : offset + batch_size]
        assets = tuple(AssetRef(acquisition_ids[symbol], symbol) for symbol in batch)
        try:
            fetched = provider.fetch_prices(PriceRequest(assets, start, end))
            validate_canonical_prices(fetched)
            returned = set(fetched.index.get_level_values("sid").astype(str))
            absent = [asset for asset in assets if asset.sid not in returned]
            if absent:
                raise DataQualityError(
                    f"provider omitted acquisition ids: {[asset.sid for asset in absent]}"
                )
            successes.append(fetched)
        except Exception as batch_error:  # provider/network errors need per-symbol isolation
            # A local cache/database failure affects the entire process, not
            # individual symbols.  Retrying every member would only multiply
            # the same error hundreds of times.
            if isinstance(batch_error, sqlite3.OperationalError):
                raise DataQualityError(
                    f"systemic Yahoo cache/database failure: {batch_error}"
                ) from batch_error
            if len(assets) == 1:
                failure_rows.append(
                    {
                        "symbol": assets[0].symbol,
                        "error_type": type(batch_error).__name__,
                        "message": str(batch_error),
                    }
                )
                continue
            for asset in assets:
                try:
                    fetched = provider.fetch_prices(PriceRequest((asset,), start, end))
                    validate_canonical_prices(fetched)
                    successes.append(fetched)
                except Exception as error:
                    failure_rows.append(
                        {
                            "symbol": asset.symbol,
                            "error_type": type(error).__name__,
                            "message": str(error),
                        }
                    )

    if not successes:
        raise DataQualityError("no Yahoo symbol produced any price rows")
    raw = canonicalize_prices(pd.concat(successes).sort_index())
    validate_canonical_prices(raw)
    # A successful response containing only NaN adjusted closes is still a
    # failed security acquisition and must be visible in the manifest.
    valid_counts = raw["tr_close"].notna().groupby(level="sid").sum()
    failed_symbols = {row["symbol"] for row in failure_rows}
    for symbol, acquisition_sid in acquisition_ids.items():
        if symbol in failed_symbols:
            continue
        if int(valid_counts.get(acquisition_sid, 0)) == 0:
            failure_rows.append(
                {
                    "symbol": symbol,
                    "error_type": "NoValidPrices",
                    "message": "provider returned no non-null Adj Close observations",
                }
            )
    failures = pd.DataFrame.from_records(
        failure_rows, columns=["symbol", "error_type", "message"]
    )
    return raw, failures, acquisition_ids


def map_downloads_to_stable_sids(
    raw: pd.DataFrame,
    plan: pd.DataFrame,
    acquisition_ids: dict[str, str],
) -> pd.DataFrame:
    """Clip provider ticker histories to mappings and replace temporary ids."""

    mapped: list[pd.DataFrame] = []
    for row in plan.itertuples(index=False):
        acquisition_sid = acquisition_ids[row.symbol]
        try:
            symbol_frame = raw.xs(acquisition_sid, level="sid", drop_level=False).copy()
        except KeyError:
            continue
        dates = symbol_frame.index.get_level_values("date")
        keep = (dates >= pd.Timestamp(row.download_from)) & (
            dates < pd.Timestamp(row.download_to_exclusive)
        )
        symbol_frame = symbol_frame.loc[keep].reset_index()
        symbol_frame["sid"] = str(row.sid)
        mapped.append(symbol_frame.set_index(["date", "sid"]))
    if not mapped:
        raise DataQualityError("ticker mapping produced no stable-sid price rows")
    prices = canonicalize_prices(pd.concat(mapped).sort_index())
    validate_canonical_prices(prices)
    return prices


def build_yfinance_dataset(
    config: DatasetBuildConfig,
    *,
    provider: PriceProvider | None = None,
    calendar: TradingCalendar | None = None,
) -> DatasetBuildResult:
    """Acquire, map, audit and freeze one dataset version.

    Membership is never inferred from prices or today's constituents.
    Incomplete Yahoo coverage is frozen as ``invalid_data`` rather than
    silently changing the PIT universe.
    """

    if not config.security_master_path.is_file():
        raise FileNotFoundError(config.security_master_path)
    if not config.membership_path.is_file():
        raise FileNotFoundError(config.membership_path)

    layout = DatasetLayout(config.data_root).create()
    snapshot_dir = layout.raw_snapshot_dir("yfinance", config.snapshot_id)
    curated_dir = layout.curated_dir(config.dataset_version)
    manifest_path = layout.manifest_path(config.dataset_version)
    for path, label in (
        (snapshot_dir, "raw snapshot"),
        (curated_dir, "curated dataset"),
        (manifest_path, "dataset manifest"),
    ):
        if path.exists():
            raise SnapshotExistsError(f"{label} already exists: {path}")

    membership = PITMembership.from_csv(config.membership_path)
    _validate_pit_date_semantics(membership, config.pit_date_semantics)
    security_master = SecurityMaster.from_csv(config.security_master_path)
    plan = build_yfinance_download_plan(
        security_master,
        membership.all_sids,
        price_start=config.price_start,
        end=config.end,
    )
    downloader = provider or YFinanceProvider(repair=config.repair)
    raw, failures, acquisition_ids = download_yfinance_symbols(
        downloader,
        tuple(plan["symbol"]),
        start=config.price_start,
        end=config.end,
        batch_size=config.batch_size,
    )
    prices = map_downloads_to_stable_sids(raw, plan, acquisition_ids)
    benchmark_request = PriceRequest(
        (AssetRef("benchmark", config.benchmark_symbol),),
        config.price_start,
        config.end,
    )
    try:
        benchmark_prices = downloader.fetch_prices(benchmark_request)
    except Exception as error:
        raise DataQualityError(
            f"benchmark acquisition failed for {config.benchmark_symbol}: {error}"
        ) from error
    benchmark_daily = _benchmark_frame(
        benchmark_prices,
        label=config.benchmark_label,
        symbol=config.benchmark_symbol,
    )

    if calendar is None:
        if config.calendar_source == "XNYS":
            calendar = TradingCalendar.from_exchange_calendars(
                config.price_start, config.end, calendar_name="XNYS"
            )
        else:
            calendar = TradingCalendar.from_prices(prices)
    calendar_frame = _calendar_frame(calendar)
    risk_free_daily = None
    if config.risk_free_path is not None:
        risk_free_daily = load_daily_risk_free_csv(
            config.risk_free_path,
            calendar,
            research_start=config.research_start,
            end=config.end,
        )
    signal_dates = _research_signal_dates(
        calendar, config.research_start, config.end
    )
    if not signal_dates:
        raise DataQualityError("research interval contains no executable signal dates")
    audit = build_universe_audit(
        prices,
        membership,
        signal_dates,
        calendar,
    )
    summary = _complete_signal_date_summary(
        summarize_universe_audit(audit), signal_dates
    )
    if audit.empty or int(audit["is_member"].sum()) == 0:
        raise DataQualityError("PIT file has no members on research signal dates")

    empty_membership_signal_dates = int(summary["member_count"].eq(0).sum())
    missing_signal_close = int(
        audit["exclusion_reason"].eq("missing_signal_close").sum()
    )
    incomplete_history = int((~audit["has_signal_history"]).sum())
    incomplete_mom_255_0_history = int(
        (~audit["has_mom_255_0_history"]).sum()
    )
    incomplete_mom_255_21_history = int(
        (~audit["has_mom_255_21_history"]).sum()
    )
    incomplete_mom_12_1_history = int(
        (~audit["has_mom_12_1_history"]).sum()
    )
    abnormal_member_count_signals = int(
        (summary["member_count"].lt(480) | summary["member_count"].gt(520)).sum()
    )
    stale_snapshot_signals = 0
    if membership.storage_format == "snapshots":
        stale_snapshot_signals = int(
            summary["membership_snapshot_age_days"]
            .gt(config.max_snapshot_age_days)
            .sum()
        )
    if not failures.empty or missing_signal_close or empty_membership_signal_dates:
        status = "invalid_data"
    elif stale_snapshot_signals or incomplete_history or abnormal_member_count_signals:
        status = "review"
    else:
        status = "valid"

    store = ParquetStore(layout)
    written: list[Path] = []
    raw_path = store.write_raw_snapshot(
        raw,
        provider="yfinance",
        snapshot_id=config.snapshot_id,
        filename="provider_prices.parquet",
    )
    written.append(raw_path)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    failure_path = snapshot_dir / "download_failures.csv"
    _write_csv_immutable(failures, failure_path)
    written.append(failure_path)
    for source, name in (
        (config.security_master_path, "security_master.csv"),
        (config.membership_path, "membership.csv"),
    ):
        target = snapshot_dir / name
        _copy_immutable(source, target)
        written.append(target)
    if config.risk_free_path is not None:
        risk_free_source_copy = snapshot_dir / "risk_free_daily.csv"
        _copy_immutable(config.risk_free_path, risk_free_source_copy)
        written.append(risk_free_source_copy)

    prices_path = store.write_curated_prices(
        prices, dataset_version=config.dataset_version
    )
    written.append(prices_path)
    for frame, name in (
        (membership.to_frame(), "membership"),
        (security_master.frame, "security_master"),
        (calendar_frame, "calendar"),
        (audit, "universe_at_signal"),
        (summary, "qa_summary"),
        (benchmark_daily, "benchmark_daily"),
        *(
            ((risk_free_daily, "risk_free_daily"),)
            if risk_free_daily is not None
            else ()
        ),
    ):
        written.append(
            store.write_curated_table(
                frame, dataset_version=config.dataset_version, table_name=name
            )
        )

    manifest_path = ManifestStore(layout).write(
        config.dataset_version,
        {
            "status": status,
            "research_tier": "prototype",
            "formal_eligible": False,
            "formal_blockers": [
                (
                    "yfinance does not provide an integrated permanent security id "
                    "and delisting lifecycle guarantee"
                ),
                (
                    "external PIT membership provenance requires independent "
                    "verification"
                ),
                *(
                    ["benchmark is not the primary S&P 500 Total Return index"]
                    if config.benchmark_kind != "total_return_index"
                    else []
                ),
            ],
            "provider": "yfinance",
            "provider_version": _package_version("yfinance"),
            "snapshot_id": config.snapshot_id,
            "request": {
                "price_start": str(config.price_start.date()),
                "research_start": str(config.research_start.date()),
                "end": str(config.end.date()),
                "batch_size": config.batch_size,
                "auto_adjust": False,
                "actions": True,
                "repair": config.repair,
                "keepna": True,
            },
            "adjustment_method": (
                "tr_close=Adj Close; tr_open/high/low=raw OHLC*(Adj Close/Close)"
            ),
            "pit": {
                "source": config.pit_source,
                "date_semantics": config.pit_date_semantics,
                "storage_format": membership.storage_format,
                "sid_count": len(membership.all_sids),
                "membership_is_never_inferred_from_prices": True,
                "max_snapshot_age_days": config.max_snapshot_age_days,
            },
            "calendar_source": config.calendar_source,
            "benchmark": {
                "symbol": config.benchmark_symbol,
                "label": config.benchmark_label,
                "kind": config.benchmark_kind,
                "price_field": "total_return_adjusted_ohlc",
                "is_primary_sp500_total_return": (
                    config.benchmark_kind == "total_return_index"
                ),
            },
            "risk_free": (
                {
                    "source": config.risk_free_source,
                    "provided": True,
                    "input_sha256": sha256_file(config.risk_free_path),
                    "curated_table": "risk_free_daily",
                    "input_columns": ["date", "rf_return"],
                    "units": "decimal_return_per_exchange_session",
                    "annualized_yield": False,
                    "percent_units": False,
                    "coverage_start": str(risk_free_daily["date"].min().date()),
                    "coverage_end": str(risk_free_daily["date"].max().date()),
                    "row_count": len(risk_free_daily),
                }
                if risk_free_daily is not None
                else {
                    "source": "zero_assumption",
                    "provided": False,
                    "units": "zero_daily_return_assumption",
                }
            ),
            "coverage": {
                "price_rows": len(prices),
                "price_sids": int(
                    prices.index.get_level_values("sid").nunique()
                ),
                "signal_dates": len(signal_dates),
                "failed_download_symbols": len(failures),
                "empty_membership_signal_dates": empty_membership_signal_dates,
                "missing_member_signal_closes": missing_signal_close,
                "incomplete_member_histories": incomplete_history,
                "incomplete_mom_255_0_histories": incomplete_mom_255_0_history,
                "incomplete_mom_255_21_histories": incomplete_mom_255_21_history,
                "incomplete_mom_12_1_histories": incomplete_mom_12_1_history,
                "stale_snapshot_signal_dates": stale_snapshot_signals,
                "abnormal_member_count_signal_dates": abnormal_member_count_signals,
                "minimum_member_count": int(summary["member_count"].min()),
                "maximum_member_count": int(summary["member_count"].max()),
            },
        },
        referenced_files=written,
    )
    return DatasetBuildResult(
        dataset_version=config.dataset_version,
        status=status,
        manifest_path=manifest_path,
        curated_prices_path=prices_path,
        qa_summary_path=curated_dir / "qa_summary.parquet",
        failed_downloads=(
            tuple(failures["symbol"].astype(str)) if not failures.empty else ()
        ),
    )


def _complete_signal_date_summary(
    summary: pd.DataFrame, signal_dates: tuple[pd.Timestamp, ...]
) -> pd.DataFrame:
    """Keep zero-member signal dates visible instead of dropping their groups."""

    expected = pd.DatetimeIndex(signal_dates, name="signal_date")
    completed = summary.set_index("signal_date").reindex(expected)
    count_columns = (
        "member_count",
        "mom_255_0_history_complete_count",
        "mom_255_21_history_complete_count",
        "mom_12_1_history_complete_count",
        "history_complete_count",
        "eligible_count",
        "execution_open_count",
    )
    for column in count_columns:
        if column not in completed:
            completed[column] = 0
        completed[column] = completed[column].fillna(0).astype(int)
    for column in (
        "mom_255_0_history_coverage",
        "mom_255_21_history_coverage",
        "mom_12_1_history_coverage",
        "history_coverage",
        "execution_open_coverage",
    ):
        if column not in completed:
            completed[column] = 0.0
        completed[column] = completed[column].fillna(0.0).astype(float)
    return completed.reset_index()


def _validate_pit_date_semantics(
    membership: PITMembership, declared_semantics: str
) -> None:
    """Reject a declaration that does not match the membership table schema."""

    expected_format = {
        "effective": "intervals",
        "snapshot_asof": "snapshots",
    }.get(declared_semantics)
    if expected_format is None:
        raise ValueError("unknown PIT date semantics")
    if membership.storage_format != expected_format:
        raise DataQualityError(
            f"pit_date_semantics={declared_semantics!r} requires {expected_format} "
            f"membership, received {membership.storage_format}"
        )


def _calendar_frame(calendar: TradingCalendar) -> pd.DataFrame:
    weekly = set(calendar.last_sessions_of_week())
    monthly = set(calendar.last_sessions_of_month())
    sessions = calendar.sessions
    return pd.DataFrame(
        {
            "session_date": sessions,
            "week_last_session": sessions.isin(weekly),
            "month_last_session": sessions.isin(monthly),
            "next_session": pd.Series(sessions, index=sessions).shift(-1).to_numpy(),
        }
    )


def _benchmark_frame(
    prices: pd.DataFrame, *, label: str, symbol: str
) -> pd.DataFrame:
    try:
        frame = prices.xs("benchmark", level="sid").copy()
    except KeyError as error:
        raise DataQualityError("benchmark provider response has no benchmark rows") from error
    series = pd.to_numeric(frame["tr_close"], errors="coerce").dropna()
    open_ = pd.to_numeric(frame["tr_open"], errors="coerce")
    valid = series.index.intersection(open_.dropna().index)
    if len(valid) < 2:
        raise DataQualityError("benchmark has fewer than two valid total-return OHLC rows")
    output = pd.DataFrame(
        {
            "date": valid,
            "benchmark_tr_open": open_.reindex(valid).to_numpy(),
            "benchmark_tr_close": series.reindex(valid).to_numpy(),
            "benchmark_label": label,
            "provider_symbol": symbol,
        }
    )
    return output


def _research_signal_dates(
    calendar: TradingCalendar, research_start: pd.Timestamp, end: pd.Timestamp
) -> tuple[pd.Timestamp, ...]:
    candidates = set(calendar.last_sessions_of_week()).union(
        calendar.last_sessions_of_month()
    )
    return tuple(
        sorted(
            date
            for date in candidates
            if research_start <= date <= end
            and calendar.sessions.searchsorted(date, side="right")
            < len(calendar.sessions)
        )
    )


def _write_csv_immutable(frame: pd.DataFrame, path: Path) -> None:
    if path.exists():
        raise SnapshotExistsError(f"file already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def _copy_immutable(source: Path, destination: Path) -> None:
    if destination.exists():
        raise SnapshotExistsError(f"file already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _package_version(package: str) -> str:
    try:
        from importlib.metadata import version

        return version(package)
    except Exception:
        return "unavailable"
