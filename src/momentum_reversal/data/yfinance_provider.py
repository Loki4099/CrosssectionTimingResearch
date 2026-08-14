"""yfinance adapter producing the project's canonical total-return schema."""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import tempfile
from typing import Any

import numpy as np
import pandas as pd

from .provider import AssetRef, PriceRequest
from .schema import CANONICAL_PRICE_COLUMNS, DataSchemaError, canonicalize_prices


class ProviderDownloadError(RuntimeError):
    """Raised when a provider response cannot satisfy the data contract."""


_YF_FIELDS = {
    "Open",
    "High",
    "Low",
    "Close",
    "Adj Close",
    "Volume",
    "Dividends",
    "Stock Splits",
}


class YFinanceProvider:
    """Fetch unadjusted Yahoo rows and construct total-return-adjusted OHLC.

    ``downloader`` is injectable so conversion behavior is fully testable
    offline. With no injection, yfinance is imported only when a fetch occurs.
    """

    def __init__(
        self,
        *,
        downloader: Callable[..., pd.DataFrame] | None = None,
        repair: bool = False,
        threads: bool | int = True,
        cache_dir: str | Path | None = None,
    ) -> None:
        self._downloader = downloader
        self.repair = repair
        self.threads = threads
        configured = cache_dir or os.environ.get("YFINANCE_CACHE_DIR")
        self.cache_dir = Path(configured) if configured else (
            Path(tempfile.gettempdir()) / "momentum_reversal_yfinance_cache"
        )
        self._cache_configured = False

    def _download(self, **kwargs: Any) -> pd.DataFrame:
        if self._downloader is not None:
            return self._downloader(**kwargs)
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise ImportError(
                "Install the 'yfinance' optional dependency to acquire Yahoo data"
            ) from exc
        if not self._cache_configured:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            # yfinance stores both timezone metadata and Yahoo cookies in
            # SQLite.  Its default user-cache directory can be read-only in a
            # sandbox, which otherwise makes every ticker appear to fail.
            yf.set_tz_cache_location(str(self.cache_dir.resolve()))
            self._cache_configured = True
        return yf.download(**kwargs)

    def fetch_prices(self, request: PriceRequest) -> pd.DataFrame:
        symbols = [asset.symbol for asset in request.assets]
        # yfinance's end is exclusive; PriceRequest's end is inclusive.
        exclusive_end = request.end + pd.Timedelta(days=1)
        raw = self._download(
            tickers=symbols,
            start=request.start.strftime("%Y-%m-%d"),
            end=exclusive_end.strftime("%Y-%m-%d"),
            auto_adjust=False,
            actions=True,
            repair=self.repair,
            keepna=True,
            group_by="column",
            threads=self.threads,
            progress=False,
        )
        if not isinstance(raw, pd.DataFrame) or raw.empty:
            raise ProviderDownloadError("yfinance returned no price rows")
        return normalize_yfinance_download(raw, request.assets).loc[
            lambda frame: (
                frame.index.get_level_values("date") >= request.start
            )
            & (frame.index.get_level_values("date") <= request.end)
        ]


def normalize_yfinance_download(
    raw: pd.DataFrame, assets: tuple[AssetRef, ...] | list[AssetRef]
) -> pd.DataFrame:
    """Convert a raw ``yf.download(auto_adjust=False)`` result to long form."""

    asset_tuple = tuple(assets)
    if not asset_tuple:
        raise ValueError("assets cannot be empty")

    frames: list[pd.DataFrame] = []
    for asset in asset_tuple:
        symbol_frame = _select_symbol(raw, asset, len(asset_tuple))
        frames.append(_convert_symbol_frame(symbol_frame, asset))
    combined = pd.concat(frames, axis=0)
    return canonicalize_prices(combined, required_columns=("tr_open", "tr_close"))


def _select_symbol(raw: pd.DataFrame, asset: AssetRef, asset_count: int) -> pd.DataFrame:
    if not isinstance(raw.columns, pd.MultiIndex):
        if asset_count != 1:
            raise ProviderDownloadError(
                "multi-asset Yahoo response must have MultiIndex columns"
            )
        return raw.copy()

    for field_level in range(raw.columns.nlevels):
        level_values = set(map(str, raw.columns.get_level_values(field_level)))
        if not (_YF_FIELDS & level_values):
            continue
        symbol_levels = [level for level in range(raw.columns.nlevels) if level != field_level]
        for symbol_level in symbol_levels:
            symbols = raw.columns.get_level_values(symbol_level).astype(str)
            if asset.symbol in set(symbols):
                selected = raw.xs(asset.symbol, axis=1, level=symbol_level, drop_level=True)
                if isinstance(selected.columns, pd.MultiIndex):
                    # Normal yf.download output has exactly two levels. Reject
                    # unknown extra dimensionality instead of guessing.
                    if selected.columns.nlevels != 1:
                        raise ProviderDownloadError("unsupported Yahoo column layout")
                return selected.copy()
    raise ProviderDownloadError(f"Yahoo response has no columns for {asset.symbol}")


def _convert_symbol_frame(frame: pd.DataFrame, asset: AssetRef) -> pd.DataFrame:
    missing = {"Open", "High", "Low", "Close", "Adj Close"}.difference(frame.columns)
    if missing:
        raise DataSchemaError(
            f"Yahoo rows for {asset.symbol} missing required columns: {sorted(missing)}"
        )

    raw_close = pd.to_numeric(frame["Close"], errors="coerce")
    adjusted_close = pd.to_numeric(frame["Adj Close"], errors="coerce")
    factor = adjusted_close.div(raw_close).where(raw_close > 0)
    factor = factor.where(np.isfinite(factor) & (factor > 0))

    converted = pd.DataFrame(index=pd.DatetimeIndex(frame.index))
    for yahoo_name, canonical_name in (
        ("Open", "raw_open"),
        ("High", "raw_high"),
        ("Low", "raw_low"),
        ("Close", "raw_close"),
    ):
        converted[canonical_name] = pd.to_numeric(frame[yahoo_name], errors="coerce")
        converted[canonical_name.replace("raw_", "tr_")] = (
            converted[canonical_name] * factor
        )
    converted["tr_close"] = adjusted_close
    converted["volume"] = _optional_numeric(frame, "Volume", default=np.nan)
    converted["dividends"] = _optional_numeric(frame, "Dividends", default=0.0)
    converted["stock_splits"] = _optional_numeric(frame, "Stock Splits", default=0.0)
    converted["source_symbol"] = asset.symbol
    converted["sid"] = asset.sid
    converted.index.name = "date"
    converted = converted.reset_index().set_index(["date", "sid"])

    ordered = [*CANONICAL_PRICE_COLUMNS, "source_symbol"]
    return converted.loc[:, ordered]


def _optional_numeric(frame: pd.DataFrame, name: str, *, default: float) -> pd.Series:
    if name not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=float)
    return pd.to_numeric(frame[name], errors="coerce")
