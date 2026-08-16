"""Build the frozen round-one experiment figure set.

The generator intentionally reads the local runtime bundles rather than the
small published summaries.  That keeps every plotted point tied to the frozen
daily NAV and rebalance evidence.  It writes exactly 59 PNG figures plus a
machine-readable image manifest and a short methodology README.

The module also exposes small, side-effect-free helpers used by the test suite.
Importing it never parses command-line arguments or touches the filesystem.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd


RUN_IDS: dict[str, str] = {
    "G00": "g00-frozen-v3-v1",
    "G11": "g11-frozen-v3-v1",
    "G12": "g12-frozen-v3-v2",
    "G13": "g13-frozen-v3-v2",
    "G21": "g21-frozen-v3-v1",
    # G22 v1 is an invalid predecessor and must never be plotted.
    "G22": "g22-frozen-v3-v2",
    "G23": "g23-frozen-v3-v1",
    "G31": "g31-frozen-v3-v1",
    "G32": "g32-frozen-v3-v1",
    "G33": "g33-frozen-v3-v1",
}

EXPERIMENT_GROUPS: tuple[str, ...] = (
    "G11",
    "G12",
    "G13",
    "G21",
    "G22",
    "G23",
    "G31",
    "G32",
    "G33",
)
SCALE_DERISK_GROUPS: tuple[str, ...] = (
    "G11",
    "G12",
    "G13",
    "G31",
    "G32",
    "G33",
)
REVERSAL_GROUPS: frozenset[str] = frozenset(("G21", "G22", "G23"))
PORTFOLIO_MODES: tuple[str, ...] = ("long_only", "long_short")
FREQUENCIES: tuple[str, ...] = ("monthly", "weekly")
SIGNALS: tuple[str, ...] = ("mom_12_1", "mom_255_0", "mom_255_21")
TOP_NS: tuple[int, ...] = (10, 20, 50)
REPRESENTATIVE_SIGNAL = "mom_255_0"
REPRESENTATIVE_TOP_N = 20
EXPECTED_PRIMARY_ROWS: dict[str, int] = {
    group: (72 if group in REVERSAL_GROUPS else 36) for group in RUN_IDS
}
EXPECTED_PNG_COUNT = 59
KNOWN_TREE_SHA256: dict[str, str] = {
    "G00": "32832d1ac0dea2822539803962eab86f426288d0c59e16dbb5a55d7187c1ae20",
    "G31": "f9079b2aaff1640d874f6dba762055e1ce2037ba24c7523d6735a7bc4b9f393a",
    "G32": "64c2ec8c2f7c3dd4b8b60786ea16e22dcd695fe762a7b21284c4d59865a94c3d",
    "G33": "98b01d10c4edd9d0faf2ed8f7f835175ff8fe642ba6c31ded4eaaefc3b8a4c6f",
}

GROUP_LABELS: dict[str, str] = {
    "G11": "G11 | SPY RV21 continuous scale",
    "G12": "G12 | Book RV126 continuous scale",
    "G13": "G13 | Book forecast continuous scale",
    "G21": "G21 | SPY RV21 Q4 reversal",
    "G22": "G22 | Book RV126 Q4 reversal",
    "G23": "G23 | Book forecast Q4 reversal",
    "G31": "G31 | SPY RV21 Q4 derisk",
    "G32": "G32 | Book RV126 Q4 derisk",
    "G33": "G33 | Book forecast Q4 derisk",
}
SIGNAL_LABELS: dict[str, str] = {
    "mom_12_1": "12-1",
    "mom_255_0": "255-0",
    "mom_255_21": "255-21",
}
MODE_LABELS: dict[str, str] = {
    "long_only": "Long-only",
    "long_short": "Long-short",
}

# Okabe-Ito derived palette.  Every identity also has a distinct line style.
COLORS: dict[str, str] = {
    "dynamic": "#0072B2",
    "dynamic_alt": "#CC79A7",
    "g00": "#303030",
    "spy": "#D55E00",
    "tbill": "#009E73",
    "fixed_average": "#E69F00",
    "same_vol": "#56B4E9",
    "exposure": "#0072B2",
    "long_leg": "#009E73",
    "short_leg": "#CC79A7",
    "q4": "#D55E00",
}
VARIANT_STYLES: dict[str, tuple[str, str]] = {
    "base": (COLORS["dynamic"], "-"),
    "rev5": (COLORS["dynamic"], "-"),
    "rev20": (COLORS["dynamic_alt"], "--"),
}
CRISIS_WINDOWS: tuple[tuple[str, str, str, str], ...] = (
    ("2018-09-21", "2018-12-24", "2018 Q4", "#999999"),
    ("2020-02-19", "2020-03-23", "COVID selloff", "#D55E00"),
    ("2020-03-24", "2020-06-30", "COVID rebound", "#009E73"),
    ("2022-01-03", "2022-10-12", "2022 bear", "#999999"),
)


def select_run_id(group: str) -> str:
    """Return the single allowed frozen run ID for *group*."""

    normalized = str(group).upper()
    if normalized not in RUN_IDS:
        raise KeyError(f"Unknown round-one group: {group!r}")
    return RUN_IDS[normalized]


def _as_bool(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series.dtype):
        return series.fillna(False)
    return (
        series.astype("string")
        .str.strip()
        .str.lower()
        .isin({"true", "1", "yes", "y"})
    )


def _frequency_column(frame: pd.DataFrame) -> str:
    for candidate in ("frequency", "rebalance_frequency"):
        if candidate in frame.columns:
            return candidate
    raise ValueError("summary is missing frequency/rebalance_frequency")


def select_primary(summary: pd.DataFrame) -> pd.DataFrame:
    """Select the explicitly frozen primary scenario rows.

    ``is_primary_scenario`` is necessary but not sufficient.  The cost and
    borrow rules are repeated here so a drifted or accidentally broad flag
    cannot silently contaminate the charts.
    """

    required = {
        "is_primary_scenario",
        "cost_bps",
        "portfolio_mode",
        "borrow_fee_annual",
    }
    missing = required.difference(summary.columns)
    if missing:
        raise ValueError(f"summary is missing required columns: {sorted(missing)}")
    frequency_col = _frequency_column(summary)
    frequency = summary[frequency_col].astype("string").str.lower()
    mask = _as_bool(summary["is_primary_scenario"])
    if "valid_scenario" in summary.columns:
        mask &= _as_bool(summary["valid_scenario"])
    expected_cost = frequency.map({"monthly": 5.0, "weekly": 10.0})
    mask &= expected_cost.notna()
    mask &= np.isclose(
        pd.to_numeric(summary["cost_bps"], errors="coerce"), expected_cost
    )
    mode = summary["portfolio_mode"].astype("string").str.lower()
    borrow = pd.to_numeric(summary["borrow_fee_annual"], errors="coerce")
    mask &= mode.isin(PORTFOLIO_MODES)
    mask &= np.where(mode.eq("long_only"), np.isclose(borrow, 0.0), np.isclose(borrow, 0.01))
    result = summary.loc[mask].copy()
    if frequency_col != "frequency":
        result["frequency"] = result[frequency_col].astype("string").str.lower()
    else:
        result["frequency"] = frequency.loc[result.index]
    return result.reset_index(drop=True)


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    """Hash a file without loading it all into memory."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _run_tree_sha256(
    manifest: Mapping[str, object],
    manifest_sha256: str | None = None,
    manifest_bytes: int | None = None,
) -> str:
    files = manifest.get("files", [])
    normalized: list[dict[str, object]] = []
    if isinstance(files, list):
        for item in files:
            if isinstance(item, Mapping):
                normalized.append(
                    {
                        "path": str(item.get("path", "")),
                        "bytes": int(item.get("bytes", 0)),
                        "sha256": str(item.get("sha256", "")),
                    }
                )
    if manifest_sha256:
        normalized.append(
            {
                "path": "manifest.json",
                "bytes": int(manifest_bytes or 0),
                "sha256": manifest_sha256,
            }
        )
    # This is the immutable nine-file tree anchor used by the experiment
    # migration/audit: POSIX relative path, actual bytes, then SHA-256; sorted;
    # newline-separated with no trailing newline.
    payload = "\n".join(
        f"{item['path']}|{item['bytes']}|{item['sha256']}"
        for item in sorted(normalized, key=lambda item: str(item["path"]))
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def verify_manifest(path: str | Path) -> dict[str, object]:
    """Verify all files declared by a frozen run manifest.

    *path* may point either to ``manifest.json`` or to its run directory.  The
    returned mapping includes two underscore-prefixed derived hashes used by
    the generated image manifest.
    """

    candidate = Path(path)
    manifest_path = candidate if candidate.is_file() else candidate / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Frozen run manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError(f"Manifest has no declared files: {manifest_path}")
    failures: list[str] = []
    for item in files:
        if not isinstance(item, Mapping) or "path" not in item:
            failures.append(f"malformed entry: {item!r}")
            continue
        declared_path = str(item["path"])
        source = manifest_path.parent / declared_path
        if not source.is_file():
            failures.append(f"missing {declared_path}")
            continue
        expected_bytes = int(item.get("bytes", -1))
        if expected_bytes >= 0 and source.stat().st_size != expected_bytes:
            failures.append(
                f"size {declared_path}: {source.stat().st_size} != {expected_bytes}"
            )
            continue
        expected_sha = str(item.get("sha256", ""))
        if expected_sha and sha256_file(source) != expected_sha:
            failures.append(f"sha256 {declared_path}")
    if failures:
        raise ValueError(
            f"Frozen run hash/size verification failed for {manifest_path}: "
            + "; ".join(failures)
        )
    result = dict(manifest)
    result["_manifest_sha256"] = sha256_file(manifest_path)
    result["_run_tree_sha256"] = _run_tree_sha256(
        manifest,
        str(result["_manifest_sha256"]),
        manifest_path.stat().st_size,
    )
    return result


def benchmark_nav(
    frame: pd.DataFrame, evaluation_dates: Sequence[object] | pd.Index
) -> pd.Series:
    """Build SPY total-return-proxy wealth with the frozen open/close convention.

    The first evaluation return is first-close / first-open.  All subsequent
    returns are close-to-close; algebraically the wealth series is therefore
    each close divided by the first evaluation open.
    """

    required = {"date", "benchmark_tr_open", "benchmark_tr_close"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"benchmark frame is missing columns: {sorted(missing)}")
    dates = pd.DatetimeIndex(pd.to_datetime(pd.Index(evaluation_dates))).sort_values().unique()
    if len(dates) == 0:
        return pd.Series(dtype=float, name="SPY")
    data = frame.loc[:, list(required)].copy()
    data["date"] = pd.to_datetime(data["date"])
    data = data.drop_duplicates("date", keep="last").set_index("date").sort_index()
    aligned = data.reindex(dates)
    if aligned[["benchmark_tr_open", "benchmark_tr_close"]].isna().any().any():
        missing_dates = aligned.index[
            aligned[["benchmark_tr_open", "benchmark_tr_close"]].isna().any(axis=1)
        ]
        raise ValueError(
            "benchmark is missing evaluation dates: "
            + ", ".join(str(date.date()) for date in missing_dates[:5])
        )
    first_open = float(aligned.iloc[0]["benchmark_tr_open"])
    if not math.isfinite(first_open) or first_open <= 0:
        raise ValueError("benchmark first evaluation open must be positive")
    result = aligned["benchmark_tr_close"].astype(float) / first_open
    result.name = "SPY"
    return result


def _with_inferred_frequency(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Preserve regular synthetic indexes without imposing a calendar on real data."""

    if len(index) >= 3 and index.freq is None:
        inferred = pd.infer_freq(index)
        if inferred is not None:
            try:
                return pd.DatetimeIndex(index, freq=inferred)
            except ValueError:
                pass
    return index


def strategy_nav(frame: pd.DataFrame) -> pd.Series:
    """Return frozen strategy NAV without rebasing away the first-day return."""

    required = {"date", "nav"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"strategy NAV is missing columns: {sorted(missing)}")
    data = frame.loc[:, ["date", "nav"]].copy()
    data["date"] = pd.to_datetime(data["date"])
    if data["date"].duplicated().any():
        raise ValueError("strategy NAV has duplicate dates")
    data = data.sort_values("date")
    index = _with_inferred_frequency(pd.DatetimeIndex(data["date"]))
    result = pd.Series(data["nav"].astype(float).to_numpy(), index=index, name="nav")
    if (result <= 0).any() or not np.isfinite(result).all():
        raise ValueError("strategy NAV must be finite and strictly positive")
    result.name = "nav"
    return result


def relative_wealth(nav: pd.Series, ref: pd.Series) -> pd.Series:
    """Return compounded active wealth ``nav / ref - 1`` on common dates."""

    aligned = pd.concat(
        [pd.Series(nav, name="nav"), pd.Series(ref, name="ref")],
        axis=1,
        join="inner",
    ).dropna()
    if (aligned["ref"] <= 0).any():
        raise ValueError("reference wealth must be strictly positive")
    result = aligned["nav"] / aligned["ref"] - 1.0
    result.name = nav.name if nav.name == ref.name else None
    return result


def _successful_status(status: object) -> bool:
    normalized = str(status).strip().lower()
    return normalized in {
        "executed",
        "successful",
        "success",
        "filled",
        "complete",
        "completed",
    }


def held_allocation(actions: pd.DataFrame, dates: Sequence[object] | pd.Index) -> pd.Series:
    """Reconstruct the causally held risk scalar on evaluation dates.

    Only a successful execution changes the held scalar, starting on that
    execution date.  A skipped action carries the prior value forward.  Signal
    dates never update the series directly.
    """

    date_index = pd.DatetimeIndex(pd.to_datetime(pd.Index(dates))).sort_values().unique()
    if len(date_index) == 0:
        return pd.Series(dtype=float, name="held_allocation")
    if "execution_date" not in actions.columns:
        raise ValueError("actions are missing execution_date")
    status_col = "execution_status" if "execution_status" in actions.columns else "action_status"
    if status_col not in actions.columns:
        raise ValueError("actions are missing execution_status/action_status")
    allocation_col = next(
        (
            column
            for column in (
                "target_risk_allocation",
                "requested_risk_allocation",
                "requested_gross_exposure",
                "filled_risk_allocation",
            )
            if column in actions.columns
        ),
        None,
    )
    if allocation_col is None:
        raise ValueError("actions are missing a risk-allocation column")
    data = actions.loc[:, ["execution_date", status_col, allocation_col]].copy()
    data["execution_date"] = pd.to_datetime(data["execution_date"])
    data[allocation_col] = pd.to_numeric(data[allocation_col], errors="coerce")
    data = data[data[status_col].map(_successful_status) & data[allocation_col].notna()]
    data = data.sort_values("execution_date").drop_duplicates("execution_date", keep="last")
    events = data.set_index("execution_date")[allocation_col]
    result = events.reindex(date_index).ffill().fillna(1.0).astype(float)
    result.name = "held_allocation"
    return result


def gross_exposure(frame: pd.DataFrame, mode: str) -> pd.Series:
    """Return economically relevant gross exposure; never use LS net exposure."""

    normalized_mode = str(mode).lower()
    required = {"long_exposure", "short_exposure"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"exposure frame is missing columns: {sorted(missing)}")
    long_leg = pd.to_numeric(frame["long_exposure"], errors="coerce").abs()
    short_leg = pd.to_numeric(frame["short_exposure"], errors="coerce").abs()
    if normalized_mode == "long_only":
        result = long_leg
    elif normalized_mode == "long_short":
        result = long_leg + short_leg
    else:
        raise ValueError(f"unknown portfolio mode: {mode!r}")
    if "date" in frame.columns:
        index = _with_inferred_frequency(pd.DatetimeIndex(pd.to_datetime(frame["date"])))
        result = pd.Series(result.to_numpy(), index=index)
    result.name = "gross_exposure"
    return result.astype(float)


def _aligned_rf(rf: pd.Series, index: pd.Index) -> pd.Series:
    result = pd.Series(rf, dtype=float).reindex(index)
    if result.isna().any():
        result = result.ffill().bfill()
    if result.isna().any():
        raise ValueError("risk-free series cannot be aligned to return dates")
    return result


def fixed_average_control(
    returns: pd.Series, rf: pd.Series, allocations: pd.Series
) -> pd.Series:
    """Static average-exposure wealth control, retaining the first-day return."""

    base = pd.Series(returns, dtype=float).dropna()
    risk_free = _aligned_rf(rf, base.index)
    allocation_series = pd.Series(allocations, dtype=float).reindex(base.index).ffill().bfill()
    if allocation_series.isna().all():
        raise ValueError("allocation series contains no usable values")
    allocation = float(allocation_series.mean())
    allocation = float(np.clip(allocation, 0.0, 1.0))
    control_return = allocation * base + (1.0 - allocation) * risk_free
    result = (1.0 + control_return).cumprod()
    result.name = "fixed_average"
    result.attrs["allocation"] = allocation
    return result


def same_vol_control(
    returns: pd.Series, rf: pd.Series, target_vol_returns: pd.Series
) -> pd.Series:
    """Static exposure control matching the target strategy's realized volatility."""

    aligned = pd.concat(
        [
            pd.Series(returns, name="base", dtype=float),
            pd.Series(target_vol_returns, name="target", dtype=float),
        ],
        axis=1,
        join="inner",
    ).dropna()
    risk_free = _aligned_rf(rf, aligned.index)
    base_excess = aligned["base"] - risk_free
    target_excess = aligned["target"] - risk_free
    base_vol = float(base_excess.std(ddof=1))
    target_vol = float(target_excess.std(ddof=1))
    if not math.isfinite(base_vol) or base_vol <= 0:
        allocation = 0.0
    else:
        allocation = float(np.clip(target_vol / base_vol, 0.0, 1.0))
    control_return = allocation * aligned["base"] + (1.0 - allocation) * risk_free
    result = (1.0 + control_return).cumprod()
    result.name = "same_vol"
    result.attrs["allocation"] = allocation
    return result


@dataclass
class Round1Data:
    runtime_root: Path
    run_dirs: dict[str, Path]
    manifests: dict[str, dict[str, object]]
    primary: dict[str, pd.DataFrame]
    nav: dict[str, pd.DataFrame]
    benchmark: pd.Series
    risk_free: pd.Series
    benchmark_path: Path
    risk_free_path: Path
    dataset_version: str
    _rebalance_cache: dict[tuple[object, ...], pd.DataFrame] = field(default_factory=dict)

    @property
    def dates(self) -> pd.DatetimeIndex:
        return pd.DatetimeIndex(self.risk_free.index)

    def scenario_nav(self, group: str, row: pd.Series) -> pd.DataFrame:
        frame = self.nav[group]
        mask = _scenario_mask(frame, row)
        result = frame.loc[mask].sort_values("date").copy()
        if len(result) != len(self.dates):
            raise ValueError(
                f"{group} scenario {row['strategy_id']} has {len(result)} NAV rows; "
                f"expected {len(self.dates)}"
            )
        return result

    def matching_g00(self, row: pd.Series) -> pd.Series:
        candidates = self.primary["G00"]
        mask = (
            candidates["signal"].astype(str).eq(str(row["signal"]))
            & pd.to_numeric(candidates["top_n"]).eq(int(row["top_n"]))
            & candidates["frequency"].astype(str).eq(str(row["frequency"]))
            & candidates["portfolio_mode"].astype(str).eq(str(row["portfolio_mode"]))
        )
        matched = candidates.loc[mask]
        if len(matched) != 1:
            raise ValueError(
                "Expected one matched G00 row for "
                f"{row['signal']}/Top{row['top_n']}/{row['frequency']}/"
                f"{row['portfolio_mode']}; found {len(matched)}"
            )
        return matched.iloc[0]

    def scenario_rebalances(self, group: str, row: pd.Series) -> pd.DataFrame:
        key = (
            group,
            str(row["strategy_id"]),
            str(row["portfolio_mode"]),
            str(row["variant_id"]),
            float(row["cost_bps"]),
            float(row["borrow_fee_annual"]),
        )
        cached = self._rebalance_cache.get(key)
        if cached is not None:
            return cached.copy()
        source = self.run_dirs[group] / "artifacts" / "rebalances.parquet"
        available = _parquet_columns(source)
        wanted = [
            "signal_date",
            "execution_date",
            "execution_status",
            "target_risk_allocation",
            "filled_risk_allocation",
            "requested_gross_exposure",
            "target_gross_exposure",
            "high_volatility",
            "group_id",
            "strategy_id",
            "portfolio_mode",
            "variant_id",
            "cost_bps",
            "borrow_fee_annual",
        ]
        columns = [column for column in wanted if column in available]
        filters = [
            ("strategy_id", "==", str(row["strategy_id"])),
            ("portfolio_mode", "==", str(row["portfolio_mode"])),
            ("variant_id", "==", str(row["variant_id"])),
            ("cost_bps", "==", float(row["cost_bps"])),
            ("borrow_fee_annual", "==", float(row["borrow_fee_annual"])),
        ]
        try:
            result = pd.read_parquet(source, columns=columns, filters=filters)
        except (TypeError, ValueError):
            result = pd.read_parquet(source, columns=columns)
            result = result.loc[_scenario_mask(result, row)].copy()
        if result.empty:
            raise ValueError(f"No rebalance rows for {group} {row['strategy_id']}")
        self._rebalance_cache[key] = result.copy()
        return result


def _parquet_columns(path: Path) -> list[str]:
    try:
        import pyarrow.parquet as pq

        return list(pq.ParquetFile(path).schema.names)
    except ImportError as exc:  # pragma: no cover - CLI dependency guard
        raise RuntimeError("pyarrow is required to read frozen parquet bundles") from exc


def _scenario_mask(frame: pd.DataFrame, row: pd.Series) -> pd.Series:
    mask = pd.Series(True, index=frame.index)
    for column in (
        "strategy_id",
        "portfolio_mode",
        "variant_id",
        "cost_bps",
        "borrow_fee_annual",
    ):
        if column not in frame.columns:
            continue
        if column in {"cost_bps", "borrow_fee_annual"}:
            mask &= np.isclose(
                pd.to_numeric(frame[column], errors="coerce"), float(row[column])
            )
        else:
            mask &= frame[column].astype(str).eq(str(row[column]))
    return mask


def _load_primary_nav(run_dir: Path, primary: pd.DataFrame) -> pd.DataFrame:
    source = run_dir / "artifacts" / "nav.parquet"
    available = _parquet_columns(source)
    wanted = [
        "date",
        "nav",
        "daily_return",
        "rf_return",
        "long_exposure",
        "short_exposure",
        "gross_exposure",
        "net_exposure",
        "pnl_long_risk",
        "pnl_short_risk",
        "group_id",
        "strategy_id",
        "portfolio_mode",
        "variant_id",
        "cost_bps",
        "borrow_fee_annual",
    ]
    columns = [column for column in wanted if column in available]
    try:
        raw = pd.read_parquet(
            source,
            columns=columns,
            filters=[
                ("cost_bps", "in", [5.0, 10.0]),
                ("borrow_fee_annual", "in", [0.0, 0.01]),
            ],
        )
    except (TypeError, ValueError):
        raw = pd.read_parquet(source, columns=columns)
    selector_columns = [
        "strategy_id",
        "portfolio_mode",
        "variant_id",
        "cost_bps",
        "borrow_fee_annual",
    ]
    metadata_columns = selector_columns + ["signal", "top_n", "frequency"]
    selector = primary.loc[:, metadata_columns].drop_duplicates()
    result = raw.merge(selector, on=selector_columns, how="inner", validate="many_to_one")
    result["date"] = pd.to_datetime(result["date"])
    result = result.sort_values(selector_columns + ["date"]).reset_index(drop=True)
    expected = len(primary) * result["date"].nunique()
    if len(result) != expected:
        raise ValueError(
            f"Primary NAV row count mismatch in {run_dir}: {len(result)} != {expected}"
        )
    return result


def _dataset_paths(runtime_root: Path, dataset_version: str) -> tuple[Path, Path]:
    curated = runtime_root / "data" / "curated" / dataset_version
    benchmark_path = curated / "benchmark_daily.parquet"
    risk_free_path = curated / "risk_free_daily.parquet"
    if not benchmark_path.is_file() or not risk_free_path.is_file():
        raise FileNotFoundError(
            f"Frozen benchmark/RF files not found under {curated}"
        )
    return benchmark_path, risk_free_path


def load_round1_data(
    runtime_root: str | Path,
    groups: Sequence[str] = EXPERIMENT_GROUPS,
    *,
    verify_hashes: bool = True,
) -> Round1Data:
    runtime = Path(runtime_root).resolve()
    requested = tuple(dict.fromkeys(("G00",) + tuple(str(group).upper() for group in groups)))
    unknown = set(requested).difference(RUN_IDS)
    if unknown:
        raise ValueError(f"Unknown round-one groups: {sorted(unknown)}")
    run_dirs: dict[str, Path] = {}
    manifests: dict[str, dict[str, object]] = {}
    primary: dict[str, pd.DataFrame] = {}
    nav: dict[str, pd.DataFrame] = {}
    for group in requested:
        run_dir = runtime / "results" / "experiments" / group / "runs" / select_run_id(group)
        if not run_dir.is_dir():
            raise FileNotFoundError(f"Frozen run directory not found: {run_dir}")
        run_dirs[group] = run_dir
        if verify_hashes:
            manifest = verify_manifest(run_dir)
        else:
            manifest_path = run_dir / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["_manifest_sha256"] = sha256_file(manifest_path)
            manifest["_run_tree_sha256"] = _run_tree_sha256(
                manifest,
                str(manifest["_manifest_sha256"]),
                manifest_path.stat().st_size,
            )
        if str(manifest.get("run_id")) != select_run_id(group):
            raise ValueError(f"Run ID mismatch in {run_dir / 'manifest.json'}")
        known_tree = KNOWN_TREE_SHA256.get(group)
        if known_tree is not None and str(manifest["_run_tree_sha256"]) != known_tree:
            raise ValueError(
                f"{group} nine-file tree anchor changed: "
                f"{manifest['_run_tree_sha256']} != {known_tree}"
            )
        manifests[group] = manifest
        summary = pd.read_csv(run_dir / "summary.csv")
        selected = select_primary(summary)
        expected_rows = EXPECTED_PRIMARY_ROWS[group]
        if len(selected) != expected_rows:
            raise ValueError(
                f"{group} has {len(selected)} primary scenarios; expected {expected_rows}"
            )
        primary[group] = selected
        nav[group] = _load_primary_nav(run_dir, selected)

    dataset_version = str(manifests["G00"].get("dataset_version", ""))
    if not dataset_version:
        raise ValueError("G00 manifest is missing dataset_version")
    for group, manifest in manifests.items():
        if str(manifest.get("dataset_version")) != dataset_version:
            raise ValueError(f"Dataset mismatch between G00 and {group}")
    benchmark_path, risk_free_path = _dataset_paths(runtime, dataset_version)
    evaluation_dates = pd.DatetimeIndex(nav["G00"]["date"].unique()).sort_values()
    benchmark_frame = pd.read_parquet(benchmark_path)
    spy = benchmark_nav(benchmark_frame, evaluation_dates)
    risk_free_frame = pd.read_parquet(risk_free_path)
    risk_free_frame["date"] = pd.to_datetime(risk_free_frame["date"])
    risk_free = (
        risk_free_frame.drop_duplicates("date", keep="last")
        .set_index("date")["rf_return"]
        .astype(float)
        .reindex(evaluation_dates)
    )
    if risk_free.isna().any():
        raise ValueError("risk_free_daily.parquet does not cover every evaluation date")
    risk_free.name = "T-bill daily return"
    return Round1Data(
        runtime_root=runtime,
        run_dirs=run_dirs,
        manifests=manifests,
        primary=primary,
        nav=nav,
        benchmark=spy,
        risk_free=risk_free,
        benchmark_path=benchmark_path,
        risk_free_path=risk_free_path,
        dataset_version=dataset_version,
    )


def _origin_date(index: pd.Index) -> pd.Timestamp:
    first = pd.Timestamp(index.min())
    if first == pd.Timestamp("2018-01-02"):
        return pd.Timestamp("2017-12-29")
    return first - pd.Timedelta(days=1)


def _prepend_origin(series: pd.Series) -> pd.Series:
    data = pd.Series(series, dtype=float).sort_index()
    origin = pd.Series([1.0], index=pd.DatetimeIndex([_origin_date(data.index)]), name=data.name)
    return pd.concat([origin, data])


def _tbill_nav(data: Round1Data, prepend: bool = True) -> pd.Series:
    result = (1.0 + data.risk_free).cumprod()
    result.name = "T-bill"
    return _prepend_origin(result) if prepend else result


def _scenario_rows(
    data: Round1Data,
    group: str,
    mode: str,
    *,
    frequency: str | None = None,
    signal: str | None = None,
    top_n: int | None = None,
) -> pd.DataFrame:
    frame = data.primary[group]
    mask = frame["portfolio_mode"].astype(str).eq(mode)
    if frequency is not None:
        mask &= frame["frequency"].astype(str).eq(frequency)
    if signal is not None:
        mask &= frame["signal"].astype(str).eq(signal)
    if top_n is not None:
        mask &= pd.to_numeric(frame["top_n"]).eq(top_n)
    return frame.loc[mask].sort_values(
        ["signal", "top_n", "frequency", "variant_id"]
    )


def _path_series(
    data: Round1Data, group: str, row: pd.Series, *, prepend: bool = True
) -> tuple[pd.Series, pd.Series]:
    treatment = strategy_nav(data.scenario_nav(group, row))
    g00_row = data.matching_g00(row)
    reference = strategy_nav(data.scenario_nav("G00", g00_row))
    if prepend:
        treatment = _prepend_origin(treatment)
        reference = _prepend_origin(reference)
    return treatment, reference


def _aggregate_paths(
    data: Round1Data, group: str, mode: str
) -> tuple[pd.DataFrame, pd.DataFrame]:
    treatment: list[pd.Series] = []
    reference: list[pd.Series] = []
    for _, row in _scenario_rows(data, group, mode).iterrows():
        treatment_nav, g00_nav = _path_series(data, group, row)
        treatment.append(treatment_nav)
        reference.append(g00_nav)
    treatment_frame = pd.concat(treatment, axis=1)
    reference_frame = pd.concat(reference, axis=1)
    treatment_frame.columns = range(treatment_frame.shape[1])
    reference_frame.columns = range(reference_frame.shape[1])
    return treatment_frame, reference_frame


def _drawdown(nav: pd.Series) -> pd.Series:
    result = nav / nav.cummax() - 1.0
    result.name = "drawdown"
    return result


def _held_state(
    actions: pd.DataFrame, dates: pd.Index, value_column: str, default: float = 0.0
) -> pd.Series:
    if value_column not in actions.columns:
        return pd.Series(default, index=pd.DatetimeIndex(dates), name=value_column)
    status_col = "execution_status" if "execution_status" in actions.columns else "action_status"
    frame = actions.loc[:, ["execution_date", status_col, value_column]].copy()
    frame["execution_date"] = pd.to_datetime(frame["execution_date"])
    frame[value_column] = pd.to_numeric(frame[value_column], errors="coerce")
    frame = frame[frame[status_col].map(_successful_status) & frame[value_column].notna()]
    frame = frame.sort_values("execution_date").drop_duplicates("execution_date", keep="last")
    events = frame.set_index("execution_date")[value_column]
    result = events.reindex(pd.DatetimeIndex(dates)).ffill().fillna(default).astype(float)
    result.name = value_column
    return result


def _configure_matplotlib() -> None:
    config_root = Path(tempfile.gettempdir()) / "momentum-reversal-matplotlib"
    config_root.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(config_root))
    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 9.0,
            "axes.titlesize": 10.5,
            "axes.labelsize": 9.0,
            "legend.fontsize": 8.0,
            "xtick.labelsize": 8.0,
            "ytick.labelsize": 8.0,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.color": "#D8D8D8",
            "grid.linewidth": 0.55,
            "grid.alpha": 0.65,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "savefig.facecolor": "white",
        }
    )


def _format_time_axis(ax: object, show_labels: bool = True) -> None:
    import matplotlib.dates as mdates

    ax.xaxis.set_major_locator(mdates.YearLocator(2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.tick_params(axis="x", labelbottom=show_labels)
    ax.margins(x=0.005)


def _format_percent_axis(ax: object) -> None:
    from matplotlib.ticker import PercentFormatter

    ax.yaxis.set_major_formatter(PercentFormatter(xmax=1.0, decimals=0))


def _shade_crises(ax: object, labels: bool = False) -> None:
    for start, end, label, color in CRISIS_WINDOWS:
        ax.axvspan(
            pd.Timestamp(start),
            pd.Timestamp(end),
            color=color,
            alpha=0.075,
            linewidth=0,
            zorder=0,
        )
        if labels:
            midpoint = pd.Timestamp(start) + (pd.Timestamp(end) - pd.Timestamp(start)) / 2
            ax.annotate(
                label,
                xy=(midpoint, 0.985),
                xycoords=("data", "axes fraction"),
                xytext=(0, 0),
                textcoords="offset points",
                ha="center",
                va="top",
                rotation=90,
                fontsize=6.5,
                color="#666666",
            )


def _save_figure(fig: object, path: Path, dpi: int = 160) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        path,
        dpi=dpi,
        bbox_inches="tight",
        pad_inches=0.12,
        metadata={"Software": "scripts/build_round1_figures.py"},
        pil_kwargs={"optimize": True, "compress_level": 9},
    )
    import matplotlib.pyplot as plt

    plt.close(fig)


def _figure_reference(data: Round1Data, mode: str) -> tuple[pd.Series, str, str]:
    if mode == "long_only":
        return _prepend_origin(data.benchmark), "SPY TR proxy", "spy"
    return _tbill_nav(data), "T-bill", "tbill"


def plot_overview_nav(data: Round1Data, mode: str, output_path: Path) -> None:
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(3, 3, figsize=(15.5, 11.4), sharex=True)
    external_ref, external_label, external_color = _figure_reference(data, mode)
    for ax, group in zip(axes.flat, EXPERIMENT_GROUPS, strict=True):
        treatment, reference = _aggregate_paths(data, group, mode)
        low = treatment.quantile(0.10, axis=1)
        median = treatment.median(axis=1)
        high = treatment.quantile(0.90, axis=1)
        g00_median = reference.median(axis=1)
        ax.fill_between(
            treatment.index,
            low,
            high,
            color=COLORS["dynamic"],
            alpha=0.13,
            linewidth=0,
            label="Treatment 10-90%",
        )
        ax.plot(median, color=COLORS["dynamic"], lw=1.6, label="Treatment median")
        ax.plot(g00_median, color=COLORS["g00"], lw=1.25, ls="--", label="Matched G00 median")
        ax.plot(external_ref, color=COLORS[external_color], lw=1.0, ls=":", label=external_label)
        ax.set_yscale("log")
        ax.set_title(GROUP_LABELS[group], loc="left", fontweight="normal")
        _format_time_axis(ax, show_labels=group.startswith("G3"))
    axes[1, 0].set_ylabel("Wealth (log; start = 1)")
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=4, frameon=False, bbox_to_anchor=(0.5, 0.965))
    fig.suptitle(
        f"Round-one primary paths | {MODE_LABELS[mode]} absolute wealth",
        y=0.995,
        fontsize=15,
        fontweight="normal",
    )
    fig.text(
        0.5,
        0.018,
        "Pointwise path distribution across frozen primary signal / TopK / frequency paths"
        " (and both reversal variants); not an investable portfolio",
        ha="center",
        color="#555555",
        fontsize=8,
    )
    fig.tight_layout(rect=(0.025, 0.04, 0.995, 0.94), h_pad=1.5, w_pad=1.0)
    _save_figure(fig, output_path)


def plot_overview_active(data: Round1Data, mode: str, output_path: Path) -> None:
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(3, 3, figsize=(15.5, 11.4), sharex=True)
    for ax, group in zip(axes.flat, EXPERIMENT_GROUPS, strict=True):
        treatment, reference = _aggregate_paths(data, group, mode)
        active = treatment / reference - 1.0
        low = active.quantile(0.10, axis=1)
        median = active.median(axis=1)
        high = active.quantile(0.90, axis=1)
        ax.fill_between(
            active.index,
            low,
            high,
            color=COLORS["dynamic"],
            alpha=0.14,
            linewidth=0,
            label="10-90%",
        )
        ax.plot(median, color=COLORS["dynamic"], lw=1.6, label="Median")
        ax.axhline(0.0, color=COLORS["g00"], lw=0.85, ls="--")
        _format_percent_axis(ax)
        _format_time_axis(ax, show_labels=group.startswith("G3"))
        ax.set_title(GROUP_LABELS[group], loc="left", fontweight="normal")
    axes[1, 0].set_ylabel("Active wealth vs matched G00")
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=2, frameon=False, bbox_to_anchor=(0.5, 0.965))
    fig.suptitle(
        f"Round-one primary paths | {MODE_LABELS[mode]} matched active wealth",
        y=0.995,
        fontsize=15,
        fontweight="normal",
    )
    fig.text(
        0.5,
        0.018,
        "Active wealth = treatment NAV / exactly matched G00 NAV - 1; NAV differences are not subtracted",
        ha="center",
        color="#555555",
        fontsize=8,
    )
    fig.tight_layout(rect=(0.025, 0.04, 0.995, 0.94), h_pad=1.5, w_pad=1.0)
    _save_figure(fig, output_path)


def _plot_nav_panel(
    ax: object,
    data: Round1Data,
    group: str,
    rows: pd.DataFrame,
    mode: str,
) -> tuple[pd.Series, list[tuple[pd.Series, str, str]]]:
    first_row = rows.iloc[0]
    _, g00_nav = _path_series(data, group, first_row)
    treatments: list[tuple[pd.Series, str, str]] = []
    for _, row in rows.iterrows():
        treatment_nav, _ = _path_series(data, group, row)
        variant = str(row["variant_id"])
        color, line_style = VARIANT_STYLES[variant]
        label = "Treatment" if variant == "base" else variant
        ax.plot(treatment_nav, color=color, ls=line_style, lw=1.5, label=label)
        treatments.append((treatment_nav, variant, color))
    ax.plot(g00_nav, color=COLORS["g00"], ls="--", lw=1.25, label="Matched G00")
    external, external_label, color_key = _figure_reference(data, mode)
    ax.plot(external, color=COLORS[color_key], ls=":", lw=1.0, label=external_label)
    ax.set_yscale("log")
    return g00_nav, treatments


def plot_representative(
    data: Round1Data, group: str, mode: str, output_path: Path
) -> None:
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(4, 2, figsize=(15.2, 13.5), sharex="col")
    for column, frequency in enumerate(FREQUENCIES):
        rows = _scenario_rows(
            data,
            group,
            mode,
            frequency=frequency,
            signal=REPRESENTATIVE_SIGNAL,
            top_n=REPRESENTATIVE_TOP_N,
        )
        expected_variants = 2 if group in REVERSAL_GROUPS else 1
        if len(rows) != expected_variants:
            raise ValueError(
                f"Expected {expected_variants} representative rows for {group}/{mode}/{frequency}; "
                f"found {len(rows)}"
            )
        nav_ax, active_ax, dd_ax, action_ax = axes[:, column]
        g00_nav, treatments = _plot_nav_panel(nav_ax, data, group, rows, mode)
        nav_ax.set_title(f"{frequency.title()} | absolute wealth", loc="left")
        for treatment_nav, variant, color in treatments:
            label = "Treatment" if variant == "base" else variant
            style = VARIANT_STYLES[variant][1]
            active_ax.plot(
                relative_wealth(treatment_nav, g00_nav),
                color=color,
                ls=style,
                lw=1.45,
                label=label,
            )
            dd_ax.plot(
                _drawdown(treatment_nav),
                color=color,
                ls=style,
                lw=1.35,
                label=label,
            )
        active_ax.axhline(0.0, color=COLORS["g00"], lw=0.85, ls="--")
        active_ax.set_title("Matched active wealth", loc="left")
        _format_percent_axis(active_ax)
        dd_ax.plot(_drawdown(g00_nav), color=COLORS["g00"], ls="--", lw=1.15, label="Matched G00")
        dd_ax.set_title("Drawdown", loc="left")
        _format_percent_axis(dd_ax)

        first_row = rows.iloc[0]
        actions = data.scenario_rebalances(group, first_row)
        scenario_daily = data.scenario_nav(group, first_row)
        if group in REVERSAL_GROUPS:
            q4_state = _held_state(actions, data.dates, "high_volatility", default=0.0)
            action_ax.step(
                q4_state.index,
                q4_state,
                where="post",
                color=COLORS["q4"],
                lw=1.25,
                label="Q4 reversal active",
            )
            action_ax.fill_between(
                q4_state.index,
                0.0,
                q4_state,
                step="post",
                color=COLORS["q4"],
                alpha=0.12,
                linewidth=0,
            )
            action_ax.set_ylim(-0.05, 1.08)
            action_ax.set_yticks([0.0, 1.0], ["Momentum", "Reversal"])
            action_ax.set_title("Executed Q4 action state", loc="left")
        else:
            held = held_allocation(actions, data.dates)
            realized = gross_exposure(scenario_daily, mode).reindex(data.dates)
            action_ax.step(
                held.index,
                held,
                where="post",
                color=COLORS["exposure"],
                lw=1.3,
                label="Held risk scalar",
            )
            action_ax.plot(
                realized,
                color=COLORS["g00"],
                lw=0.75,
                alpha=0.70,
                label="Realized gross",
            )
            if mode == "long_short":
                indexed = scenario_daily.set_index(pd.to_datetime(scenario_daily["date"]))
                action_ax.plot(
                    indexed["long_exposure"].abs(),
                    color=COLORS["long_leg"],
                    lw=0.65,
                    alpha=0.65,
                    label="Long leg",
                )
                action_ax.plot(
                    indexed["short_exposure"].abs(),
                    color=COLORS["short_leg"],
                    lw=0.65,
                    alpha=0.65,
                    label="|Short leg|",
                )
            action_ax.set_ylim(bottom=0.0)
            action_ax.set_title("Causal risk exposure (net is not used)", loc="left")

        for row_index, ax in enumerate(axes[:, column]):
            _shade_crises(ax, labels=(row_index == 0))
            _format_time_axis(ax, show_labels=(row_index == 3))
            if row_index in (0, 1, 2, 3):
                ax.legend(loc="best", frameon=False, ncol=2)

    axes[0, 0].set_ylabel("Wealth (log; start = 1)")
    axes[1, 0].set_ylabel("Treatment / G00 - 1")
    axes[2, 0].set_ylabel("Drawdown")
    axes[3, 0].set_ylabel("Exposure / state")
    fig.suptitle(
        f"{GROUP_LABELS[group]} | {MODE_LABELS[mode]} | 255-0 / Top20",
        fontsize=15,
        y=0.995,
        fontweight="normal",
    )
    fig.text(
        0.5,
        0.012,
        "State and held allocation update on successful execution dates; skipped executions carry the prior state",
        ha="center",
        color="#555555",
        fontsize=8,
    )
    fig.tight_layout(rect=(0.035, 0.03, 0.995, 0.97), h_pad=1.25, w_pad=1.1)
    _save_figure(fig, output_path)


def plot_atlas(
    data: Round1Data,
    group: str,
    mode: str,
    frequency: str,
    output_path: Path,
) -> None:
    import matplotlib.pyplot as plt
    from matplotlib.gridspec import GridSpec, GridSpecFromSubplotSpec

    fig = plt.figure(figsize=(15.8, 16.2))
    outer = GridSpec(3, 3, figure=fig, hspace=0.27, wspace=0.19)
    legend_handles = None
    legend_labels = None
    for row_index, signal in enumerate(SIGNALS):
        for column_index, top_n in enumerate(TOP_NS):
            inner = GridSpecFromSubplotSpec(
                2,
                1,
                subplot_spec=outer[row_index, column_index],
                height_ratios=(2.05, 1.0),
                hspace=0.04,
            )
            nav_ax = fig.add_subplot(inner[0])
            active_ax = fig.add_subplot(inner[1], sharex=nav_ax)
            rows = _scenario_rows(
                data,
                group,
                mode,
                frequency=frequency,
                signal=signal,
                top_n=top_n,
            )
            if rows.empty:
                raise ValueError(f"No atlas scenario for {group}/{mode}/{frequency}/{signal}/Top{top_n}")
            g00_nav, treatments = _plot_nav_panel(nav_ax, data, group, rows, mode)
            nav_ax.set_yscale("log")
            for treatment_nav, variant, color in treatments:
                label = "Treatment" if variant == "base" else variant
                active_ax.plot(
                    relative_wealth(treatment_nav, g00_nav),
                    color=color,
                    ls=VARIANT_STYLES[variant][1],
                    lw=1.1,
                    label=label,
                )
            active_ax.axhline(0.0, color=COLORS["g00"], lw=0.7, ls="--")
            _format_percent_axis(active_ax)
            nav_ax.set_title(
                f"{SIGNAL_LABELS[signal]} | Top{top_n}", loc="left", fontsize=10
            )
            nav_ax.tick_params(axis="x", labelbottom=False)
            _format_time_axis(active_ax, show_labels=(row_index == 2))
            if row_index == 1 and column_index == 0:
                nav_ax.set_ylabel("Wealth")
                active_ax.set_ylabel("vs G00")
            if row_index == 0 and column_index == 0:
                legend_handles, legend_labels = nav_ax.get_legend_handles_labels()
    if legend_handles is not None and legend_labels is not None:
        fig.legend(
            legend_handles,
            legend_labels,
            loc="upper center",
            ncol=4,
            frameon=False,
            bbox_to_anchor=(0.5, 0.967),
        )
    fig.suptitle(
        f"{GROUP_LABELS[group]} | {MODE_LABELS[mode]} | {frequency.title()} primary-path atlas",
        fontsize=15,
        y=0.995,
        fontweight="normal",
    )
    fig.text(
        0.5,
        0.012,
        "Each cell: absolute wealth (upper) and treatment / exactly matched G00 - 1 (lower)",
        ha="center",
        color="#555555",
        fontsize=8,
    )
    fig.subplots_adjust(left=0.055, right=0.99, bottom=0.04, top=0.94)
    _save_figure(fig, output_path)


def plot_timing_value(data: Round1Data, output_path: Path) -> None:
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 3, figsize=(15.6, 8.8), sharex=True)
    group_grid = (("G11", "G12", "G13"), ("G31", "G32", "G33"))
    for row_index, group_row in enumerate(group_grid):
        for column_index, group in enumerate(group_row):
            ax = axes[row_index, column_index]
            rows = _scenario_rows(data, group, "long_only")
            if len(rows) != 18:
                raise ValueError(f"Expected 18 timing-value paths for {group}; found {len(rows)}")
            versus_average: list[pd.Series] = []
            versus_same_vol: list[pd.Series] = []
            for _, scenario in rows.iterrows():
                dynamic_daily = data.scenario_nav(group, scenario)
                g00_row = data.matching_g00(scenario)
                g00_daily = data.scenario_nav("G00", g00_row)
                dynamic_nav = strategy_nav(dynamic_daily)
                dynamic_return = dynamic_daily.set_index("date")["daily_return"].astype(float)
                base_return = g00_daily.set_index("date")["daily_return"].astype(float)
                actions = data.scenario_rebalances(group, scenario)
                held = held_allocation(actions, data.dates)
                fixed = fixed_average_control(base_return, data.risk_free, held)
                same_vol = same_vol_control(base_return, data.risk_free, dynamic_return)
                versus_average.append(
                    relative_wealth(_prepend_origin(dynamic_nav), _prepend_origin(fixed))
                )
                versus_same_vol.append(
                    relative_wealth(_prepend_origin(dynamic_nav), _prepend_origin(same_vol))
                )
            average_frame = pd.concat(versus_average, axis=1)
            same_vol_frame = pd.concat(versus_same_vol, axis=1)
            for frame, color, label, line_style in (
                (average_frame, COLORS["fixed_average"], "Dynamic / fixed-average", "-"),
                (same_vol_frame, COLORS["same_vol"], "Dynamic / fixed-same-vol", "--"),
            ):
                low = frame.quantile(0.10, axis=1)
                median = frame.median(axis=1)
                high = frame.quantile(0.90, axis=1)
                ax.fill_between(
                    frame.index,
                    low,
                    high,
                    color=color,
                    alpha=0.12,
                    linewidth=0,
                )
                ax.plot(median, color=color, ls=line_style, lw=1.45, label=label)
            ax.axhline(0.0, color=COLORS["g00"], lw=0.8, ls="--")
            _shade_crises(ax, labels=(row_index == 0))
            _format_percent_axis(ax)
            _format_time_axis(ax, show_labels=(row_index == 1))
            ax.set_title(GROUP_LABELS[group], loc="left")
            ax.legend(loc="best", frameon=False)
    axes[0, 0].set_ylabel("Dynamic / static control - 1")
    axes[1, 0].set_ylabel("Dynamic / static control - 1")
    fig.suptitle(
        "Timing value diagnostic | All 18 long-only primary paths",
        fontsize=15,
        y=0.995,
        fontweight="normal",
    )
    fig.text(
        0.5,
        0.016,
        "Pointwise median and 10-90% path band of dynamic / static-control wealth - 1; "
        "positive values indicate timing value beyond exposure reduction",
        ha="center",
        color="#555555",
        fontsize=8,
    )
    fig.tight_layout(rect=(0.035, 0.045, 0.995, 0.955), h_pad=1.45, w_pad=1.0)
    _save_figure(fig, output_path)


def expected_image_paths(output_root: Path) -> list[Path]:
    paths = [
        output_root / "overview-nav-long-only.png",
        output_root / "overview-nav-long-short.png",
        output_root / "overview-active-long-only.png",
        output_root / "overview-active-long-short.png",
        output_root / "timing-value.png",
    ]
    paths.extend(
        output_root / group / f"representative-{mode.replace('_', '-')}.png"
        for group in EXPERIMENT_GROUPS
        for mode in PORTFOLIO_MODES
    )
    paths.extend(
        output_root
        / group
        / f"atlas-{mode.replace('_', '-')}-{frequency}.png"
        for group in EXPERIMENT_GROUPS
        for mode in PORTFOLIO_MODES
        for frequency in FREQUENCIES
    )
    if len(paths) != EXPECTED_PNG_COUNT or len(set(paths)) != EXPECTED_PNG_COUNT:
        raise AssertionError("internal figure path contract is not exactly 59 unique PNGs")
    return paths


def _source_record(
    data: Round1Data,
    image_path: Path,
    output_root: Path,
    category: str,
    groups: Sequence[str],
    selector: str,
) -> dict[str, object]:
    unique_groups = tuple(dict.fromkeys(groups))
    runs = [select_run_id(group) for group in unique_groups]
    trees = [str(data.manifests[group]["_run_tree_sha256"]) for group in unique_groups]
    manifest_hashes = [
        str(data.manifests[group]["_manifest_sha256"]) for group in unique_groups
    ]
    source_pairs: list[tuple[str, str]] = []
    for group in unique_groups:
        manifest = data.manifests[group]
        for item in manifest.get("files", []):
            if isinstance(item, Mapping) and str(item.get("path")) in {
                "summary.csv",
                "artifacts/nav.parquet",
                "artifacts/rebalances.parquet",
            }:
                source_pairs.append(
                    (
                        f"{group}/{item.get('path')}",
                        str(item.get("sha256", "")),
                    )
                )
    source_pairs.extend(
        [
            ("benchmark_daily.parquet", sha256_file(data.benchmark_path)),
            ("risk_free_daily.parquet", sha256_file(data.risk_free_path)),
        ]
    )
    source_pairs = sorted(set(source_pairs))
    source_payload = json.dumps(source_pairs, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    combined_tree_payload = "|".join(
        f"{group}:{tree}" for group, tree in zip(unique_groups, trees, strict=True)
    ).encode("utf-8")
    return {
        "image_path": image_path.relative_to(output_root).as_posix(),
        "category": category,
        "group_id": ";".join(unique_groups),
        "run_id": ";".join(runs),
        "run_tree_sha256": hashlib.sha256(combined_tree_payload).hexdigest(),
        "run_tree_members": ";".join(
            f"{group}:{tree}"
            for group, tree in zip(unique_groups, trees, strict=True)
        ),
        "source_manifest_sha256": ";".join(
            f"{group}:{manifest_hash}"
            for group, manifest_hash in zip(
                unique_groups, manifest_hashes, strict=True
            )
        ),
        "source_selector": selector,
        "source_files": ";".join(path for path, _ in source_pairs),
        "source_sha256": hashlib.sha256(source_payload).hexdigest(),
        "dataset_version": data.dataset_version,
        "image_bytes": image_path.stat().st_size,
        "image_sha256": sha256_file(image_path),
    }


def _write_readme(output_root: Path) -> None:
    content = """# Round-one figure set

This directory is generated by `scripts/build_round1_figures.py` from the local
frozen runtime bundles.  It contains exactly 59 high-resolution PNG figures:

- 4 root-level overviews: absolute NAV and matched active wealth for long-only
  and long-short;
- 18 representative-path diagnostics under the nine `Gxx/` directories (nine
  groups by two portfolio modes);
- 36 all-primary-path atlases in the same `Gxx/` directories (nine groups by
  two modes by two rebalance frequencies); and
- 1 long-only timing-value diagnostic (`timing-value.png`).

## Frozen selection

Only `summary.is_primary_scenario` rows that also satisfy the explicit primary
contract are plotted: monthly 5 bps, weekly 10 bps, long-only borrow 0%, and
long-short borrow 1%.  Invalid scenarios are rejected.  G22 uses only
`g22-frozen-v3-v2`; the invalid v1 predecessor is excluded.

## Wealth definitions

- Strategy NAV is the frozen daily NAV.  It is **not** rebased on the first
  evaluation close, so the 2018-01-02 open-to-close return is retained.  A
  2017-12-29 origin at 1 is prepended for visual clarity.
- SPY is the frozen total-return proxy.  Its first return is first-close divided
  by first-open; later returns are close-to-close.
- Matched active wealth is `treatment NAV / exactly matched G00 NAV - 1`.
  Long-horizon NAV levels are never subtracted.
- Long-short figures use matched G00 and T-bill as the primary references.  Net
  exposure is not used as a risk measure; gross, long, and absolute short
  exposures are used instead.

## Timing and exposure conventions

Held allocation and Q4 action state change only on a successful execution date.
Skipped executions carry the prior state.  This prevents a signal-close state
from being shifted backward into the return it could not yet have affected.

The timing-value panel covers all 18 long-only primary paths in each of the six
scale/derisk groups.  For every path it compares dynamic wealth directly with
two static G00/T-bill mixtures: one fixed at the dynamic rule's average held
allocation and one fixed to match its realized excess-return volatility.  The
figure shows the pointwise median and 10-90% path bands of dynamic / control - 1.
These controls are diagnostics, not replayed trading strategies: they do not
reconstruct the dynamic strategy's exact turnover-cost path.

`manifest.csv` records each image's frozen run IDs, derived run-tree hash,
source selector and source hash, byte size, and SHA-256.
"""
    (output_root / "README.md").write_text(content, encoding="utf-8", newline="\n")


def _write_image_manifest(
    output_root: Path, data: Round1Data, records: list[dict[str, object]]
) -> None:
    frame = pd.DataFrame(records).sort_values("image_path").reset_index(drop=True)
    if len(frame) != EXPECTED_PNG_COUNT or frame["image_path"].nunique() != EXPECTED_PNG_COUNT:
        raise ValueError(f"Image manifest must contain exactly {EXPECTED_PNG_COUNT} unique rows")
    frame.to_csv(output_root / "manifest.csv", index=False, lineterminator="\n")


def validate_output(output_root: str | Path) -> dict[str, object]:
    root = Path(output_root)
    expected = expected_image_paths(root)
    actual = set(root.rglob("*.png"))
    expected_set = set(expected)
    if actual != expected_set:
        missing_set = sorted(str(path) for path in expected_set.difference(actual))
        extra_set = sorted(str(path) for path in actual.difference(expected_set))
        raise ValueError(
            "Output PNG set differs from the exact 59-file contract; "
            f"missing={missing_set[:5]}, extra={extra_set[:5]}"
        )
    missing = [path for path in expected if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "Missing generated figures: " + ", ".join(str(path) for path in missing[:5])
        )
    manifest_path = root / "manifest.csv"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Image manifest not found: {manifest_path}")
    manifest = pd.read_csv(manifest_path)
    if len(manifest) != EXPECTED_PNG_COUNT or manifest["image_path"].nunique() != EXPECTED_PNG_COUNT:
        raise ValueError("manifest.csv does not contain exactly 59 unique image rows")
    for row in manifest.itertuples(index=False):
        image_path = root / str(row.image_path)
        if not image_path.is_file():
            raise FileNotFoundError(f"Manifest image not found: {image_path}")
        if image_path.stat().st_size != int(row.image_bytes):
            raise ValueError(f"Image size mismatch: {image_path}")
        if sha256_file(image_path) != str(row.image_sha256):
            raise ValueError(f"Image SHA-256 mismatch: {image_path}")
    return {
        "png_count": len(expected),
        "total_png_bytes": sum(path.stat().st_size for path in expected),
        "manifest_sha256": sha256_file(manifest_path),
    }


def build_round1_figures(data: Round1Data, output_root: str | Path) -> dict[str, object]:
    _configure_matplotlib()
    output = Path(output_root).resolve()
    output.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    all_groups = ("G00",) + EXPERIMENT_GROUPS
    primary_selector = (
        "is_primary_scenario=true;valid_scenario=true;monthly=5bps;weekly=10bps;"
        "LO_borrow=0%;LS_borrow=1%"
    )

    overview_specs = (
        ("long_only", "nav", output / "overview-nav-long-only.png"),
        ("long_short", "nav", output / "overview-nav-long-short.png"),
        ("long_only", "active", output / "overview-active-long-only.png"),
        ("long_short", "active", output / "overview-active-long-short.png"),
    )
    for mode, kind, path in overview_specs:
        if kind == "nav":
            plot_overview_nav(data, mode, path)
        else:
            plot_overview_active(data, mode, path)
        records.append(
            _source_record(
                data,
                path,
                output,
                f"overview-{kind}",
                all_groups,
                f"{primary_selector};portfolio_mode={mode};aggregate=median,p10,p90",
            )
        )

    for group in EXPERIMENT_GROUPS:
        for mode in PORTFOLIO_MODES:
            path = output / group / f"representative-{mode.replace('_', '-')}.png"
            plot_representative(data, group, mode, path)
            records.append(
                _source_record(
                    data,
                    path,
                    output,
                    "representative",
                    ("G00", group),
                    f"{primary_selector};portfolio_mode={mode};signal={REPRESENTATIVE_SIGNAL};"
                    f"top_n={REPRESENTATIVE_TOP_N};frequency=monthly,weekly",
                )
            )

    for group in EXPERIMENT_GROUPS:
        for mode in PORTFOLIO_MODES:
            for frequency in FREQUENCIES:
                path = (
                    output
                    / group
                    / f"atlas-{mode.replace('_', '-')}-{frequency}.png"
                )
                plot_atlas(data, group, mode, frequency, path)
                records.append(
                    _source_record(
                        data,
                        path,
                        output,
                        "atlas",
                        ("G00", group),
                        f"{primary_selector};portfolio_mode={mode};frequency={frequency};"
                        "signal=all;top_n=all",
                    )
                )

    timing_path = output / "timing-value.png"
    plot_timing_value(data, timing_path)
    records.append(
        _source_record(
            data,
            timing_path,
            output,
            "timing-value",
            ("G00",) + SCALE_DERISK_GROUPS,
            f"{primary_selector};portfolio_mode=long_only;frequency=all;"
            "signal=all;top_n=all;paths_per_group=18;"
            "controls=fixed-average,fixed-same-vol",
        )
    )

    expected = set(expected_image_paths(output))
    actual = set(output.rglob("*.png"))
    if actual != expected:
        missing = sorted(str(path) for path in expected.difference(actual))
        extra = sorted(str(path) for path in actual.difference(expected))
        raise ValueError(
            f"Generated PNG set differs from the 59-file contract; "
            f"missing={missing[:5]}, extra={extra[:5]}"
        )
    _write_readme(output)
    _write_image_manifest(output, data, records)
    return validate_output(output)


def validate_sources(data: Round1Data) -> dict[str, object]:
    expected_groups = set(data.primary)
    if "G00" not in expected_groups:
        raise ValueError("G00 reference was not loaded")
    for group, primary in data.primary.items():
        if len(primary) != EXPECTED_PRIMARY_ROWS[group]:
            raise ValueError(f"Unexpected primary selector count for {group}")
        if data.nav[group]["date"].nunique() != len(data.dates):
            raise ValueError(f"Evaluation date mismatch for {group}")
    return {
        "groups": sorted(expected_groups),
        "evaluation_sessions": len(data.dates),
        "evaluation_start": str(data.dates.min().date()),
        "evaluation_end": str(data.dates.max().date()),
        "dataset_version": data.dataset_version,
        "primary_scenarios": {
            group: len(frame) for group, frame in sorted(data.primary.items())
        },
    }


def _default_runtime_root() -> Path:
    repository_root = Path(__file__).resolve().parents[1]
    try:
        from momentum_reversal.runtime import resolve_runtime_paths
    except ImportError:
        source_root = repository_root / "src"
        if str(source_root) not in sys.path:
            sys.path.insert(0, str(source_root))
        try:
            from momentum_reversal.runtime import resolve_runtime_paths
        except ImportError:
            return Path.home() / "QuantWork" / "MomentumRversionMethod-runtime"
    resolved = resolve_runtime_paths(cwd=repository_root)
    return resolved.runtime_root or (
        Path.home() / "QuantWork" / "MomentumRversionMethod-runtime"
    )


def _default_output_root() -> Path:
    return Path(__file__).resolve().parents[1] / "docs" / "figures" / "round1"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build or validate the frozen round-one experiment figures."
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=_default_runtime_root(),
        help="Local MomentumRversionMethod-runtime root.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=_default_output_root(),
        help="Destination directory for the 59 PNGs, manifest.csv, and README.md.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Verify frozen sources and, when present, the generated image set without writing.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    started = time.perf_counter()
    try:
        data = load_round1_data(args.runtime_root, verify_hashes=True)
        source_status = validate_sources(data)
        if args.validate_only:
            result: dict[str, object] = {"sources": source_status}
            if (args.output_root / "manifest.csv").is_file():
                result["output"] = validate_output(args.output_root)
        else:
            result = {
                "sources": source_status,
                "output": build_round1_figures(data, args.output_root),
            }
        result["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:  # pragma: no cover - CLI error boundary
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
