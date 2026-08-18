"""Helpers for publishing a reviewable subset of immutable runtime bundles."""
from __future__ import annotations

from pathlib import Path
import shutil

from momentum_reversal.data.round2_market import sha256_file


def copy_compact(source: Path, destination: Path, relative_paths: tuple[str, ...]) -> None:
    """Copy only registered compact artifacts and fail on content drift."""
    destination.mkdir(parents=True, exist_ok=True)
    for relative in relative_paths:
        src = source / relative
        dst = destination / relative
        if not src.is_file():
            raise FileNotFoundError(f"missing compact publish input: {src}")
        if dst.exists():
            if not dst.is_file() or sha256_file(src) != sha256_file(dst):
                raise FileExistsError(f"published compact artifact differs: {dst}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
