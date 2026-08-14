"""Canonical registration and completeness checks for experiment groups."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Iterator, Mapping

from .spec import (
    GroupSpec,
    ProgramSpec,
    RiskAction,
    RiskSource,
    StrategySpec,
    load_group_spec,
    load_program_spec,
)


GROUP_COORDINATES: Mapping[str, tuple[RiskAction, RiskSource]] = MappingProxyType(
    {
        "G00": (RiskAction.NONE, RiskSource.NONE),
        "G11": (RiskAction.CONTINUOUS_SCALE, RiskSource.SPY_HIST),
        "G12": (RiskAction.CONTINUOUS_SCALE, RiskSource.BOOK_HIST),
        "G13": (RiskAction.CONTINUOUS_SCALE, RiskSource.BOOK_FORECAST),
        "G21": (RiskAction.HIGH_VOL_REVERSAL, RiskSource.SPY_HIST),
        "G22": (RiskAction.HIGH_VOL_REVERSAL, RiskSource.BOOK_HIST),
        "G23": (RiskAction.HIGH_VOL_REVERSAL, RiskSource.BOOK_FORECAST),
        "G31": (RiskAction.HIGH_VOL_DERISK, RiskSource.SPY_HIST),
        "G32": (RiskAction.HIGH_VOL_DERISK, RiskSource.BOOK_HIST),
        "G33": (RiskAction.HIGH_VOL_DERISK, RiskSource.BOOK_FORECAST),
        "XS01": (
            RiskAction.CROSS_SECTIONAL_VOL,
            RiskSource.INDIVIDUAL_HIST_VOL,
        ),
    }
)

MAIN_GROUP_IDS = tuple(group_id for group_id in GROUP_COORDINATES if group_id != "XS01")


@dataclass(frozen=True, slots=True)
class ExperimentCatalog:
    program: ProgramSpec
    groups: tuple[GroupSpec, ...]

    @classmethod
    def load(cls, config_dir: str | Path) -> "ExperimentCatalog":
        directory = Path(config_dir)
        program = load_program_spec(directory / "program.toml")
        groups = tuple(
            load_group_spec(directory / f"{group_id}.toml", program=program)
            for group_id in GROUP_COORDINATES
        )
        catalog = cls(program=program, groups=groups)
        catalog.validate()
        return catalog

    def validate(self) -> None:
        by_id = {group.group_id: group for group in self.groups}
        if len(by_id) != len(self.groups):
            raise ValueError("catalog contains duplicate group IDs")
        missing = set(GROUP_COORDINATES).difference(by_id)
        extra = set(by_id).difference(GROUP_COORDINATES)
        if missing or extra:
            raise ValueError(
                f"catalog group mismatch: missing={sorted(missing)}, extra={sorted(extra)}"
            )
        for group_id, expected in GROUP_COORDINATES.items():
            actual = (by_id[group_id].risk_action, by_id[group_id].risk_source)
            if actual != expected:
                raise ValueError(
                    f"{group_id} coordinate mismatch: expected={expected}, actual={actual}"
                )
        strategy_ids = [item.strategy_id for item in self.strategies()]
        if len(strategy_ids) != len(set(strategy_ids)):
            raise ValueError("catalog contains duplicate strategy IDs")
        if self.main_strategy_count != 468:
            raise ValueError(
                f"main G00/G11-G33 grid must contain 468 paths, got "
                f"{self.main_strategy_count}"
            )

    def group(self, group_id: str) -> GroupSpec:
        normalized = group_id.upper()
        for group in self.groups:
            if group.group_id == normalized:
                return group
        raise KeyError(group_id)

    def strategies(self, group_id: str | None = None) -> tuple[StrategySpec, ...]:
        groups = self.groups if group_id is None else (self.group(group_id),)
        return tuple(item for group in groups for item in group.strategies())

    def __iter__(self) -> Iterator[GroupSpec]:
        return iter(self.groups)

    @property
    def main_strategy_count(self) -> int:
        return sum(self.group(group_id).strategy_count for group_id in MAIN_GROUP_IDS)

    @property
    def supplemental_strategy_count(self) -> int:
        return sum(
            group.strategy_count
            for group in self.groups
            if group.group_id not in MAIN_GROUP_IDS
        )
