"""Resolve local runtime storage without hard-coding developer paths.

The repository can stay in a synced folder while large, frequently changing
artifacts live on a local disk.  Explicit CLI arguments always remain the
highest-precedence interface; this module only supplies safe defaults.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tomllib
from typing import Mapping


RUNTIME_CONFIG_ENV = "CROSSSECTION_RUNTIME_CONFIG"
RUNTIME_ROOT_ENV = "CROSSSECTION_RUNTIME_ROOT"
DATA_ROOT_ENV = "CROSSSECTION_DATA_ROOT"
RESULTS_ROOT_ENV = "CROSSSECTION_RESULTS_ROOT"
CACHE_ROOT_ENV = "CROSSSECTION_CACHE_ROOT"
LOG_ROOT_ENV = "CROSSSECTION_LOG_ROOT"

_ALLOWED_CONFIG_KEYS = {
    "schema_version",
    "runtime_root",
    "data_root",
    "results_root",
    "cache_root",
    "log_root",
}


class RuntimeConfigError(ValueError):
    """Raised when the local runtime configuration is malformed."""


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    """Resolved storage roots used as CLI defaults."""

    runtime_root: Path | None
    data_root: Path
    results_root: Path
    cache_root: Path
    log_root: Path
    config_path: Path | None
    source: str

    def create(self) -> None:
        """Create runtime directories without moving or deleting any files."""

        for path in (
            self.data_root,
            self.results_root,
            self.cache_root,
            self.log_root,
        ):
            path.mkdir(parents=True, exist_ok=True)


def resolve_runtime_paths(
    *,
    cwd: str | Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> RuntimePaths:
    """Resolve runtime roots from env, an untracked TOML file, or repo defaults.

    Precedence is specific environment variables, ``CROSSSECTION_RUNTIME_ROOT``,
    values in ``config/runtime.local.toml``, then ``data``/``results`` relative
    to the current working directory.  ``CROSSSECTION_RUNTIME_CONFIG`` can point
    to a different TOML file and must exist when set.
    """

    working_dir = Path(cwd or Path.cwd()).expanduser().resolve()
    env = os.environ if environment is None else environment
    config_path, explicit_config = _select_config_path(working_dir, env)
    config = _read_config(config_path) if config_path is not None else {}

    env_runtime_root = _nonempty(env.get(RUNTIME_ROOT_ENV))
    configured_runtime_root = _nonempty(config.get("runtime_root"))
    runtime_value = env_runtime_root or configured_runtime_root
    runtime_root = (
        _resolve_path(
            runtime_value,
            base=(config_path.parent if config_path is not None else working_dir),
        )
        if runtime_value is not None
        else None
    )

    default_base = runtime_root or working_dir
    data_root = _resolve_named_root(
        env.get(DATA_ROOT_ENV), config.get("data_root"), default_base / "data", default_base
    )
    results_root = _resolve_named_root(
        env.get(RESULTS_ROOT_ENV),
        config.get("results_root"),
        default_base / "results",
        default_base,
    )
    cache_root = _resolve_named_root(
        env.get(CACHE_ROOT_ENV),
        config.get("cache_root"),
        default_base / "cache",
        default_base,
    )
    log_root = _resolve_named_root(
        env.get(LOG_ROOT_ENV),
        config.get("log_root"),
        default_base / "logs",
        default_base,
    )

    if any(
        _nonempty(env.get(name)) is not None
        for name in (
            RUNTIME_ROOT_ENV,
            DATA_ROOT_ENV,
            RESULTS_ROOT_ENV,
            CACHE_ROOT_ENV,
            LOG_ROOT_ENV,
        )
    ):
        source = "environment"
    elif config_path is not None:
        source = "explicit_config" if explicit_config else "local_config"
    else:
        source = "repository_default"

    return RuntimePaths(
        runtime_root=runtime_root,
        data_root=data_root,
        results_root=results_root,
        cache_root=cache_root,
        log_root=log_root,
        config_path=config_path,
        source=source,
    )


def _select_config_path(
    working_dir: Path, environment: Mapping[str, str]
) -> tuple[Path | None, bool]:
    explicit = _nonempty(environment.get(RUNTIME_CONFIG_ENV))
    if explicit is not None:
        path = _resolve_path(explicit, base=working_dir)
        if not path.is_file():
            raise RuntimeConfigError(f"runtime config does not exist: {path}")
        return path, True
    candidate = working_dir / "config" / "runtime.local.toml"
    return (candidate, False) if candidate.is_file() else (None, False)


def _read_config(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise RuntimeConfigError(f"cannot read runtime config {path}: {error}") from None
    unknown = set(raw) - _ALLOWED_CONFIG_KEYS
    if unknown:
        raise RuntimeConfigError(
            f"unknown runtime config keys: {', '.join(sorted(unknown))}"
        )
    if raw.get("schema_version") != 1:
        raise RuntimeConfigError("runtime config schema_version must equal 1")
    for key in _ALLOWED_CONFIG_KEYS - {"schema_version"}:
        value = raw.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise RuntimeConfigError(f"runtime config {key} must be a non-empty string")
    return raw


def _resolve_named_root(
    environment_value: object,
    config_value: object,
    fallback: Path,
    base: Path,
) -> Path:
    selected = _nonempty(environment_value) or _nonempty(config_value)
    return _resolve_path(selected, base=base) if selected is not None else fallback.resolve()


def _resolve_path(value: object, *, base: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeConfigError("runtime paths must be non-empty strings")
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _nonempty(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    selected = value.strip()
    return selected or None
