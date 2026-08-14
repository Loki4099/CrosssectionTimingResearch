"""Stable security identities and provider-symbol mappings."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .provider import AssetRef
from .schema import DataSchemaError, normalize_session_date


SECURITY_MASTER_COLUMNS = (
    "sid",
    "provider",
    "provider_sid",
    "ticker",
    "name",
    "valid_from",
    "valid_to",
)


class SecurityMaster:
    """Versionable provider symbol mappings keyed by stable internal ``sid``."""

    def __init__(self, frame: pd.DataFrame) -> None:
        required = {"sid", "provider", "ticker"}
        missing = required.difference(frame.columns)
        if missing:
            raise DataSchemaError(f"security master missing columns: {sorted(missing)}")

        data = frame.copy()
        for column, default in (
            ("provider_sid", ""),
            ("name", ""),
            ("valid_from", pd.NaT),
            ("valid_to", pd.NaT),
        ):
            if column not in data:
                data[column] = default
        data = data.loc[:, SECURITY_MASTER_COLUMNS]
        for column in ("sid", "provider", "provider_sid", "ticker", "name"):
            data[column] = data[column].fillna("").astype(str).str.strip()
        if (data[["sid", "provider", "ticker"]] == "").any().any():
            raise DataSchemaError("sid, provider and ticker cannot be blank")
        data["valid_from"] = _optional_mapping_dates(data["valid_from"], "valid_from")
        data["valid_to"] = _optional_mapping_dates(data["valid_to"], "valid_to")
        invalid = data["valid_from"].notna() & data["valid_to"].notna() & (
            data["valid_to"] <= data["valid_from"]
        )
        if invalid.any():
            raise DataSchemaError("security mapping valid_to must be after valid_from")
        if data.duplicated().any():
            raise DataSchemaError("duplicate security master rows")
        self._frame = data.sort_values(["provider", "sid", "valid_from"], na_position="first")

    @classmethod
    def from_csv(cls, path: str | Path) -> "SecurityMaster":
        return cls(pd.read_csv(path))

    @property
    def frame(self) -> pd.DataFrame:
        return self._frame.copy()

    def assets_on(
        self,
        value: object,
        *,
        provider: str,
        sids: set[str] | tuple[str, ...] | list[str] | None = None,
    ) -> tuple[AssetRef, ...]:
        date = normalize_session_date(value)
        active = self._frame["provider"].eq(provider)
        active &= self._frame["valid_from"].isna() | (self._frame["valid_from"] <= date)
        active &= self._frame["valid_to"].isna() | (date < self._frame["valid_to"])
        if sids is not None:
            requested = set(map(str, sids))
            active &= self._frame["sid"].isin(requested)
        selected = self._frame.loc[active].copy()
        duplicates = selected["sid"].duplicated(keep=False)
        if duplicates.any():
            duplicate_sids = sorted(selected.loc[duplicates, "sid"].unique())
            raise DataSchemaError(
                f"multiple active {provider} symbols for sids: {duplicate_sids}"
            )
        if sids is not None:
            missing = sorted(set(map(str, sids)).difference(selected["sid"]))
            if missing:
                raise KeyError(f"no active {provider} mapping for sids: {missing}")
        symbols = selected["provider_sid"].where(
            selected["provider_sid"].ne(""), selected["ticker"]
        )
        return tuple(
            AssetRef(sid=row.sid, symbol=symbol)
            for row, symbol in zip(selected.itertuples(index=False), symbols, strict=True)
        )


def _optional_mapping_dates(values: pd.Series, label: str) -> pd.Series:
    """Parse nullable mapping dates while rejecting non-empty invalid values."""

    present = values.notna() & values.astype("string").str.strip().ne("")
    parsed = pd.to_datetime(values.where(present), errors="coerce")
    invalid = present & parsed.isna()
    if invalid.any():
        sample = values.loc[invalid].astype(str).tolist()[:5]
        raise DataSchemaError(f"invalid security mapping {label} values: {sample}")
    if getattr(parsed.dt, "tz", None) is not None:
        parsed = parsed.dt.tz_localize(None)
    return parsed.dt.normalize()
