"""Point-in-time universe loaders.

Interval membership uses half-open intervals: ``effective_from <= date <
effective_to``. A blank ``effective_to`` remains active indefinitely.
Snapshot membership uses the latest snapshot at or before the requested date.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Collection
from pathlib import Path
from typing import Protocol, runtime_checkable

import pandas as pd

from .schema import DataSchemaError, normalize_session_date


@runtime_checkable
class PITUniverse(Protocol):
    def members_on(self, value: object) -> Collection[str]:
        """Return eligible stable security ids as known on ``value``."""


class PITMembership:
    """In-memory point-in-time index membership."""

    def __init__(
        self,
        *,
        intervals: pd.DataFrame | None = None,
        snapshots: dict[pd.Timestamp, tuple[str, ...]] | None = None,
    ) -> None:
        if (intervals is None) == (snapshots is None):
            raise ValueError("provide exactly one of intervals or snapshots")
        self._intervals = intervals
        self._snapshots = snapshots
        self._snapshot_dates = tuple(sorted(snapshots)) if snapshots is not None else ()

    @property
    def intervals(self) -> pd.DataFrame | None:
        return None if self._intervals is None else self._intervals.copy()

    @property
    def snapshot_dates(self) -> tuple[pd.Timestamp, ...]:
        return self._snapshot_dates

    @property
    def storage_format(self) -> str:
        return "snapshots" if self._snapshots is not None else "intervals"

    @property
    def all_sids(self) -> tuple[str, ...]:
        if self._snapshots is not None:
            return tuple(
                sorted({sid for members in self._snapshots.values() for sid in members})
            )
        assert self._intervals is not None
        return tuple(sorted(self._intervals["sid"].unique().astype(str)))

    def to_frame(self) -> pd.DataFrame:
        """Return the normalized long table used to construct this universe."""

        if self._intervals is not None:
            return self._intervals.copy()
        assert self._snapshots is not None
        rows = [
            {"date": date, "sid": sid}
            for date in self._snapshot_dates
            for sid in self._snapshots[date]
        ]
        return pd.DataFrame(rows, columns=["date", "sid"])

    @classmethod
    def from_intervals(cls, frame: pd.DataFrame) -> "PITMembership":
        required = {"sid", "effective_from", "effective_to"}
        missing = required.difference(frame.columns)
        if missing:
            raise DataSchemaError(f"membership intervals missing columns: {sorted(missing)}")
        data = frame.loc[:, ["sid", "effective_from", "effective_to"]].copy()
        data["sid"] = data["sid"].astype(str).str.strip()
        if (data["sid"] == "").any():
            raise DataSchemaError("membership sid cannot be blank")
        data["effective_from"] = _required_dates(
            data["effective_from"], "membership effective_from"
        )
        data["effective_to"] = _optional_dates(
            data["effective_to"], "membership effective_to"
        )
        invalid = data["effective_to"].notna() & (
            data["effective_to"] <= data["effective_from"]
        )
        if invalid.any():
            raise DataSchemaError("effective_to must be after effective_from")
        if data.duplicated().any():
            raise DataSchemaError("duplicate membership interval rows")

        for sid, group in data.sort_values("effective_from").groupby("sid"):
            starts = group["effective_from"].to_numpy()
            previous_ends = group["effective_to"].shift().to_numpy()
            overlap = pd.notna(previous_ends) & (starts < previous_ends)
            if overlap.any() or (group["effective_to"].isna().iloc[:-1]).any():
                raise DataSchemaError(f"overlapping membership intervals for sid={sid}")
        return cls(intervals=data.sort_values(["effective_from", "sid"]).reset_index(drop=True))

    @classmethod
    def from_snapshots(cls, frame: pd.DataFrame) -> "PITMembership":
        required = {"date", "sid"}
        missing = required.difference(frame.columns)
        if missing:
            raise DataSchemaError(f"membership snapshots missing columns: {sorted(missing)}")
        data = frame.loc[:, ["date", "sid"]].copy()
        data["date"] = _required_dates(data["date"], "membership snapshot date")
        data["sid"] = data["sid"].astype(str).str.strip()
        if (data["sid"] == "").any():
            raise DataSchemaError("membership sid cannot be blank")
        if data.duplicated().any():
            raise DataSchemaError("duplicate membership snapshot rows")
        snapshots = {
            pd.Timestamp(value): tuple(sorted(group["sid"].tolist()))
            for value, group in data.groupby("date", sort=True)
        }
        if not snapshots:
            raise DataSchemaError("membership snapshot file is empty")
        return cls(snapshots=snapshots)

    @classmethod
    def from_csv(cls, path: str | Path) -> "PITMembership":
        frame = pd.read_csv(path)
        if {"effective_from", "effective_to"}.issubset(frame.columns):
            return cls.from_intervals(frame)
        if "date" in frame.columns:
            return cls.from_snapshots(frame)
        raise DataSchemaError(
            "membership CSV must contain sid/date snapshots or "
            "sid/effective_from/effective_to intervals"
        )

    def members_on(self, value: object) -> tuple[str, ...]:
        query_date = normalize_session_date(value)
        if self._snapshots is not None:
            position = bisect_right(self._snapshot_dates, query_date) - 1
            if position < 0:
                raise KeyError(f"no membership snapshot on or before {query_date.date()}")
            return self._snapshots[self._snapshot_dates[position]]

        assert self._intervals is not None
        active = (self._intervals["effective_from"] <= query_date) & (
            self._intervals["effective_to"].isna()
            | (query_date < self._intervals["effective_to"])
        )
        return tuple(sorted(self._intervals.loc[active, "sid"].tolist()))


def _required_dates(values: pd.Series, label: str) -> pd.Series:
    parsed = _optional_dates(values, label)
    if parsed.isna().any():
        raise DataSchemaError(f"{label} cannot be blank")
    return parsed


def _optional_dates(values: pd.Series, label: str) -> pd.Series:
    """Parse nullable date labels without turning typos into open intervals."""

    present = values.notna() & values.astype("string").str.strip().ne("")
    parsed = pd.to_datetime(values.where(present), errors="coerce")
    invalid = present & parsed.isna()
    if invalid.any():
        sample = values.loc[invalid].astype(str).tolist()[:5]
        raise DataSchemaError(f"invalid {label} values: {sample}")
    if getattr(parsed.dt, "tz", None) is not None:
        parsed = parsed.dt.tz_localize(None)
    return parsed.dt.normalize()
