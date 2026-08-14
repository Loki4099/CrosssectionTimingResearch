"""Provider-neutral acquisition interfaces."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

import pandas as pd

from .schema import normalize_session_date


@dataclass(frozen=True, slots=True)
class AssetRef:
    """Stable internal security id and the provider-specific query symbol."""

    sid: str
    symbol: str

    def __post_init__(self) -> None:
        if not self.sid.strip() or not self.symbol.strip():
            raise ValueError("sid and symbol must be non-empty")


@dataclass(frozen=True, slots=True)
class PriceRequest:
    """An inclusive date range for one or more assets."""

    assets: tuple[AssetRef, ...]
    start: pd.Timestamp
    end: pd.Timestamp

    def __init__(
        self,
        assets: tuple[AssetRef, ...] | list[AssetRef],
        start: str | date | pd.Timestamp,
        end: str | date | pd.Timestamp,
    ) -> None:
        object.__setattr__(self, "assets", tuple(assets))
        object.__setattr__(self, "start", normalize_session_date(start))
        object.__setattr__(self, "end", normalize_session_date(end))
        if not self.assets:
            raise ValueError("at least one asset is required")
        if len({asset.sid for asset in self.assets}) != len(self.assets):
            raise ValueError("asset sids must be unique within a request")
        if self.start > self.end:
            raise ValueError("start must be on or before end")


@runtime_checkable
class PriceProvider(Protocol):
    """Return canonical long prices indexed by ``(date, sid)``."""

    def fetch_prices(self, request: PriceRequest) -> pd.DataFrame:
        """Fetch an inclusive price interval without filling missing rows."""

