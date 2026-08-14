"""Versioned Parquet layout and immutable dataset manifests."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd

from .schema import canonicalize_prices


_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class SnapshotExistsError(FileExistsError):
    """Raised when an immutable snapshot/version would be overwritten."""


def _safe_component(value: str, label: str) -> str:
    if not _SAFE_COMPONENT.fullmatch(value):
        raise ValueError(f"unsafe {label}: {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class DatasetLayout:
    root: Path

    def __init__(self, root: str | Path = "data") -> None:
        object.__setattr__(self, "root", Path(root).resolve())

    def create(self) -> "DatasetLayout":
        for relative in ("raw", "curated", "manifests"):
            (self.root / relative).mkdir(parents=True, exist_ok=True)
        return self

    def raw_snapshot_dir(self, provider: str, snapshot_id: str) -> Path:
        provider = _safe_component(provider, "provider")
        snapshot_id = _safe_component(snapshot_id, "snapshot_id")
        return self.root / "raw" / provider / snapshot_id

    def curated_dir(self, dataset_version: str) -> Path:
        dataset_version = _safe_component(dataset_version, "dataset_version")
        return self.root / "curated" / dataset_version

    def manifest_path(self, dataset_version: str) -> Path:
        dataset_version = _safe_component(dataset_version, "dataset_version")
        return self.root / "manifests" / f"{dataset_version}.json"


class ParquetStore:
    """Small explicit table store; DuckDB can query the resulting files."""

    def __init__(self, layout: DatasetLayout) -> None:
        self.layout = layout.create()

    def write_raw_snapshot(
        self,
        frame: pd.DataFrame,
        *,
        provider: str,
        snapshot_id: str,
        filename: str = "prices.parquet",
    ) -> Path:
        directory = self.layout.raw_snapshot_dir(provider, snapshot_id)
        path = directory / _safe_component(filename, "filename")
        return _write_parquet_immutable(frame, path, index=True)

    def write_curated_prices(
        self, prices: pd.DataFrame, *, dataset_version: str
    ) -> Path:
        frame = canonicalize_prices(prices).reset_index()
        path = self.layout.curated_dir(dataset_version) / "prices_daily.parquet"
        return _write_parquet_immutable(frame, path)

    def read_curated_prices(self, *, dataset_version: str) -> pd.DataFrame:
        path = self.layout.curated_dir(dataset_version) / "prices_daily.parquet"
        return canonicalize_prices(_read_parquet(path))

    def write_curated_table(
        self, frame: pd.DataFrame, *, dataset_version: str, table_name: str
    ) -> Path:
        table_name = _safe_component(table_name, "table_name")
        path = self.layout.curated_dir(dataset_version) / f"{table_name}.parquet"
        return _write_parquet_immutable(frame, path)

    def read_curated_table(
        self, *, dataset_version: str, table_name: str
    ) -> pd.DataFrame:
        table_name = _safe_component(table_name, "table_name")
        return _read_parquet(
            self.layout.curated_dir(dataset_version) / f"{table_name}.parquet"
        )


class ManifestStore:
    """Write one immutable JSON manifest for each curated dataset version."""

    def __init__(self, layout: DatasetLayout) -> None:
        self.layout = layout.create()

    def write(
        self,
        dataset_version: str,
        payload: dict[str, Any],
        *,
        referenced_files: list[str | Path] | tuple[str | Path, ...] = (),
    ) -> Path:
        path = self.layout.manifest_path(dataset_version)
        if path.exists():
            raise SnapshotExistsError(f"manifest already exists: {path}")
        files: list[dict[str, object]] = []
        for value in referenced_files:
            file_path = Path(value).resolve()
            if not file_path.is_file():
                raise FileNotFoundError(file_path)
            try:
                recorded_path = str(file_path.relative_to(self.layout.root))
            except ValueError:
                recorded_path = str(file_path)
            files.append(
                {
                    "path": recorded_path,
                    "size_bytes": file_path.stat().st_size,
                    "sha256": sha256_file(file_path),
                }
            )
        manifest = {
            **payload,
            "dataset_version": dataset_version,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "files": files,
        }
        _write_json_immutable(manifest, path)
        return path

    def read(self, dataset_version: str) -> dict[str, Any]:
        with self.layout.manifest_path(dataset_version).open(
            "r", encoding="utf-8"
        ) as handle:
            return json.load(handle)


def sha256_file(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_parquet_immutable(
    frame: pd.DataFrame, path: Path, *, index: bool = False
) -> Path:
    if path.exists():
        raise SnapshotExistsError(f"table already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        frame.to_parquet(temporary, index=index)
        os.replace(temporary, path)
    except ImportError as exc:
        raise ImportError(
            "Writing Parquet requires the optional 'pyarrow' dependency"
        ) from exc
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def _read_parquet(path: Path) -> pd.DataFrame:
    try:
        return pd.read_parquet(path)
    except ImportError as exc:
        raise ImportError(
            "Reading Parquet requires the optional 'pyarrow' dependency"
        ) from exc


def _write_json_immutable(payload: dict[str, Any], path: Path) -> None:
    if path.exists():
        raise SnapshotExistsError(f"file already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
