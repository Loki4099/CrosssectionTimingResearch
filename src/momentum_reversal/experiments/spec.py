"""Typed, deterministic experiment specifications loaded from TOML.

The specification layer contains no backtest logic.  It freezes the common
research axes and expands each experiment group into stable strategy IDs that
can be consumed by either the long-only or signed-weight engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from itertools import product
from pathlib import Path
import re
import tomllib
from typing import Any, Mapping

from momentum_reversal.factors import MomentumDefinition


SPEC_SCHEMA_VERSION = 1
_GROUP_ID = re.compile(r"(?:G(?:00|[1-3][1-3])|XS\d{2})")
_FREQUENCIES = frozenset({"weekly", "monthly"})


class PortfolioMode(StrEnum):
    LONG_ONLY = "long_only"
    LONG_SHORT = "long_short"


class RiskAction(StrEnum):
    NONE = "none"
    CONTINUOUS_SCALE = "continuous_scale"
    HIGH_VOL_REVERSAL = "high_vol_reversal"
    HIGH_VOL_DERISK = "high_vol_derisk"
    CROSS_SECTIONAL_VOL = "cross_sectional_vol"


class RiskSource(StrEnum):
    NONE = "none"
    SPY_HIST = "spy_hist"
    BOOK_HIST = "book_hist"
    BOOK_FORECAST = "book_forecast"
    INDIVIDUAL_HIST_VOL = "individual_hist_vol"


@dataclass(frozen=True, slots=True)
class ProgramSpec:
    """Common axes and accounting conventions shared by every group."""

    path: Path
    schema_version: int
    program_id: str
    signals: tuple[MomentumDefinition, ...]
    top_n: tuple[int, ...]
    frequencies: tuple[str, ...]
    portfolio_modes: tuple[PortfolioMode, ...]
    raw: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class StrategySpec:
    """One signal path before cost and borrow-fee reporting scenarios."""

    group_id: str
    signal: MomentumDefinition
    top_n: int
    frequency: str
    portfolio_mode: PortfolioMode
    variant_id: str = ""

    @property
    def strategy_id(self) -> str:
        parts = [
            self.group_id,
            self.signal.value,
            f"top{self.top_n}",
            self.frequency,
            self.portfolio_mode.value,
        ]
        if self.variant_id:
            parts.append(self.variant_id)
        return "__".join(parts)

    @property
    def parent_id(self) -> str | None:
        if self.group_id == "G00":
            return None
        return "__".join(
            [
                "G00",
                self.signal.value,
                f"top{self.top_n}",
                self.frequency,
                self.portfolio_mode.value,
            ]
        )


@dataclass(frozen=True, slots=True)
class GroupSpec:
    """A registered control, nine-grid, or cross-sectional experiment group."""

    path: Path
    program: ProgramSpec
    schema_version: int
    group_id: str
    name: str
    wave: int
    status: str
    risk_action: RiskAction
    risk_source: RiskSource
    legacy_aliases: tuple[str, ...]
    reversal_lookbacks: tuple[int, ...]
    individual_vol_windows: tuple[int, ...]
    raw: Mapping[str, Any]

    @property
    def spec_id(self) -> str:
        return self.group_id

    @property
    def variant_ids(self) -> tuple[str, ...]:
        if self.reversal_lookbacks:
            return tuple(f"rev{lookback}" for lookback in self.reversal_lookbacks)
        if self.individual_vol_windows:
            return tuple(f"rv{window}" for window in self.individual_vol_windows)
        return ("",)

    @property
    def strategy_count(self) -> int:
        return (
            len(self.program.signals)
            * len(self.program.top_n)
            * len(self.program.frequencies)
            * len(self.program.portfolio_modes)
            * len(self.variant_ids)
        )

    def strategies(self) -> tuple[StrategySpec, ...]:
        values = product(
            self.program.signals,
            self.program.top_n,
            self.program.frequencies,
            self.program.portfolio_modes,
            self.variant_ids,
        )
        return tuple(
            StrategySpec(
                group_id=self.group_id,
                signal=signal,
                top_n=top_n,
                frequency=frequency,
                portfolio_mode=mode,
                variant_id=variant,
            )
            for signal, top_n, frequency, mode, variant in values
        )

    def resolved_config(self) -> dict[str, Any]:
        """Return a self-contained machine configuration for bundle provenance."""

        return {
            "schema_version": SPEC_SCHEMA_VERSION,
            "program": _plain(self.program.raw),
            "group": _plain(self.raw),
            "resolved": {
                "spec_id": self.spec_id,
                "strategy_count": self.strategy_count,
                "strategy_ids": [item.strategy_id for item in self.strategies()],
            },
        }

    def resolved_toml(self) -> str:
        return toml_dumps(self.resolved_config())

    @property
    def resolved_sha256(self) -> str:
        return sha256(self.resolved_toml().encode("utf-8")).hexdigest()


def load_program_spec(path: str | Path) -> ProgramSpec:
    source = Path(path)
    raw = _load_toml(source)
    _require_schema(raw, source)
    grid = _table(raw, "grid", source)
    signals = tuple(
        MomentumDefinition(value) for value in _string_list(grid, "signals", source)
    )
    top_n = tuple(_positive_int_list(grid, "top_n", source))
    frequencies = tuple(_string_list(grid, "frequencies", source))
    if not frequencies or any(value not in _FREQUENCIES for value in frequencies):
        raise ValueError(f"{source}: frequencies must be weekly/monthly")
    modes = tuple(
        PortfolioMode(value)
        for value in _string_list(grid, "portfolio_modes", source)
    )
    if set(modes) != set(PortfolioMode):
        raise ValueError(f"{source}: both long_only and long_short are required")
    _require_unique(signals, "signals", source)
    _require_unique(top_n, "top_n", source)
    _require_unique(frequencies, "frequencies", source)
    _require_unique(modes, "portfolio_modes", source)
    program_id = str(raw.get("program_id", "")).strip()
    if not program_id:
        raise ValueError(f"{source}: program_id is required")
    return ProgramSpec(
        path=source,
        schema_version=SPEC_SCHEMA_VERSION,
        program_id=program_id,
        signals=signals,
        top_n=top_n,
        frequencies=frequencies,
        portfolio_modes=modes,
        raw=raw,
    )


def load_group_spec(
    path: str | Path, *, program: ProgramSpec | None = None
) -> GroupSpec:
    source = Path(path)
    if program is None:
        program = load_program_spec(source.parent / "program.toml")
    raw = _load_toml(source)
    _require_schema(raw, source)
    group_id = str(raw.get("group_id", "")).strip().upper()
    if not _GROUP_ID.fullmatch(group_id):
        raise ValueError(f"{source}: invalid group_id {group_id!r}")
    name = str(raw.get("name", "")).strip()
    status = str(raw.get("status", "")).strip()
    wave = raw.get("wave")
    if not name or not status or not isinstance(wave, int) or wave < 0:
        raise ValueError(f"{source}: name, non-negative wave, and status are required")
    grid = raw.get("grid", {})
    if not isinstance(grid, dict):
        raise ValueError(f"{source}: grid must be a TOML table")
    reversal = tuple(_optional_positive_int_list(grid, "reversal_lookbacks", source))
    individual = tuple(
        _optional_positive_int_list(grid, "individual_vol_windows", source)
    )
    if reversal and individual:
        raise ValueError(f"{source}: only one group-specific variant axis is allowed")
    action = RiskAction(str(raw.get("risk_action", "")))
    risk_source = RiskSource(str(raw.get("risk_source", "")))
    if action is RiskAction.HIGH_VOL_REVERSAL and not reversal:
        raise ValueError(f"{source}: reversal groups require reversal_lookbacks")
    if action is not RiskAction.HIGH_VOL_REVERSAL and reversal:
        raise ValueError(f"{source}: reversal_lookbacks only belong to reversal groups")
    if action is RiskAction.CROSS_SECTIONAL_VOL and not individual:
        raise ValueError(f"{source}: cross-sectional group requires vol windows")
    if action is not RiskAction.CROSS_SECTIONAL_VOL and individual:
        raise ValueError(f"{source}: individual vol windows only belong to XS groups")
    aliases = raw.get("legacy_aliases", [])
    if not isinstance(aliases, list) or not all(isinstance(item, str) for item in aliases):
        raise ValueError(f"{source}: legacy_aliases must be an array of strings")
    spec = GroupSpec(
        path=source,
        program=program,
        schema_version=SPEC_SCHEMA_VERSION,
        group_id=group_id,
        name=name,
        wave=wave,
        status=status,
        risk_action=action,
        risk_source=risk_source,
        legacy_aliases=tuple(aliases),
        reversal_lookbacks=reversal,
        individual_vol_windows=individual,
        raw=raw,
    )
    ids = [item.strategy_id for item in spec.strategies()]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{source}: strategy IDs are not unique")
    return spec


def toml_dumps(value: Mapping[str, Any]) -> str:
    """Serialize the JSON-like resolved spec subset as deterministic TOML."""

    lines: list[str] = []
    _emit_toml_table(lines, (), value)
    return "\n".join(lines).rstrip() + "\n"


def _emit_toml_table(
    lines: list[str], prefix: tuple[str, ...], table: Mapping[str, Any]
) -> None:
    scalar_items: list[tuple[str, Any]] = []
    table_items: list[tuple[str, Mapping[str, Any]]] = []
    for key, value in table.items():
        if isinstance(value, Mapping):
            table_items.append((str(key), value))
        else:
            scalar_items.append((str(key), value))
    if prefix:
        if lines and lines[-1] != "":
            lines.append("")
        lines.append("[" + ".".join(_toml_key(part) for part in prefix) + "]")
    for key, value in scalar_items:
        lines.append(f"{_toml_key(key)} = {_toml_value(value)}")
    for key, child in table_items:
        _emit_toml_table(lines, (*prefix, key), child)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise TypeError(f"unsupported resolved TOML value: {type(value).__name__}")


def _toml_key(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", value):
        return value
    return _toml_value(value)


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open("rb") as handle:
        value = tomllib.load(handle)
    if not isinstance(value, dict):  # pragma: no cover - tomllib always returns dict
        raise ValueError(f"{path}: TOML root must be a table")
    return value


def _require_schema(raw: Mapping[str, Any], path: Path) -> None:
    if raw.get("schema_version") != SPEC_SCHEMA_VERSION:
        raise ValueError(
            f"{path}: schema_version must be {SPEC_SCHEMA_VERSION}, "
            f"got {raw.get('schema_version')!r}"
        )


def _table(raw: Mapping[str, Any], key: str, path: Path) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{path}: {key} must be a TOML table")
    return value


def _string_list(raw: Mapping[str, Any], key: str, path: Path) -> list[str]:
    value = raw.get(key)
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"{path}: {key} must be a non-empty string array")
    return list(value)


def _positive_int_list(raw: Mapping[str, Any], key: str, path: Path) -> list[int]:
    value = raw.get(key)
    if not isinstance(value, list) or not value or not all(
        isinstance(item, int) and not isinstance(item, bool) and item > 0
        for item in value
    ):
        raise ValueError(f"{path}: {key} must be a non-empty positive integer array")
    return list(value)


def _optional_positive_int_list(
    raw: Mapping[str, Any], key: str, path: Path
) -> list[int]:
    if key not in raw:
        return []
    values = _positive_int_list(raw, key, path)
    _require_unique(values, key, path)
    return values


def _require_unique(values: tuple[Any, ...] | list[Any], key: str, path: Path) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{path}: {key} must contain unique values")


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value
