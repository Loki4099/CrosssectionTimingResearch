"""Deterministic total-return panels used by offline tests."""

from __future__ import annotations

import numpy as np
import pandas as pd


def make_prices(
    *,
    sessions: int = 330,
    assets: int = 60,
    start: str = "2020-01-02",
) -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=sessions)
    sids = [f"S{i:03d}" for i in range(assets)]
    rows: list[tuple[pd.Timestamp, str, float, float]] = []
    for asset_number, sid in enumerate(sids):
        daily_rate = 0.00005 + asset_number * 0.00001
        closes = 100.0 * np.exp(daily_rate * np.arange(sessions))
        opens = closes * (1.0 - 0.0001 * ((asset_number % 3) - 1))
        for date, open_, close in zip(dates, opens, closes, strict=True):
            rows.append((date, sid, float(open_), float(close)))
    return (
        pd.DataFrame(rows, columns=["date", "sid", "tr_open", "tr_close"])
        .set_index(["date", "sid"])
        .sort_index()
    )


class StaticMembership:
    def __init__(self, members: tuple[str, ...]) -> None:
        self.members = members

    def members_on(self, value: object) -> tuple[str, ...]:
        return self.members


class SnapshotMembership:
    def __init__(self, snapshots: dict[pd.Timestamp, tuple[str, ...]]) -> None:
        self.snapshots = dict(sorted(snapshots.items()))

    def members_on(self, value: object) -> tuple[str, ...]:
        date = pd.Timestamp(value)
        prior = [key for key in self.snapshots if key <= date]
        if not prior:
            raise KeyError(date)
        return self.snapshots[max(prior)]
