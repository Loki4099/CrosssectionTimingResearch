"""Explicit conversion of Kenneth French daily RF ZIP files."""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

import pandas as pd

from .qa import DataQualityError


def convert_ken_french_daily_rf_zip(
    source_zip: str | Path,
    destination_csv: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Convert French Library RF percent observations to decimal daily returns.

    The source's daily RF field is expressed in percent. This conversion is
    intentionally explicit: ``rf_return = RF / 100``. No annualization or
    compounding transformation is applied.
    """

    source = Path(source_zip)
    destination = Path(destination_csv)
    if not source.is_file():
        raise FileNotFoundError(source)
    if destination.exists() and not overwrite:
        raise FileExistsError(destination)
    with zipfile.ZipFile(source) as archive:
        csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(csv_names) != 1:
            raise DataQualityError(
                f"expected exactly one CSV in Kenneth French ZIP, found {csv_names}"
            )
        text = archive.read(csv_names[0]).decode("utf-8-sig", errors="strict")

    rows: list[dict[str, object]] = []
    header_seen = False
    for raw_row in csv.reader(io.StringIO(text)):
        cells = [cell.strip() for cell in raw_row]
        if not cells:
            continue
        if cells[0].casefold() in {"date", ""} and any(
            cell.casefold() == "rf" for cell in cells
        ):
            header_seen = True
            rf_position = next(
                index for index, cell in enumerate(cells) if cell.casefold() == "rf"
            )
            continue
        if not header_seen or not cells[0].isdigit() or len(cells[0]) != 8:
            if header_seen and rows:
                break
            continue
        if rf_position >= len(cells):
            raise DataQualityError("Kenneth French RF row has too few columns")
        try:
            date = pd.to_datetime(cells[0], format="%Y%m%d", errors="raise")
            percent = float(cells[rf_position])
        except (TypeError, ValueError) as error:
            raise DataQualityError(f"invalid Kenneth French daily RF row: {cells}") from error
        rows.append({"date": date.strftime("%Y-%m-%d"), "rf_return": percent / 100.0})
    if not rows:
        raise DataQualityError("no daily RF observations found in Kenneth French ZIP")
    frame = pd.DataFrame(rows)
    if frame["date"].duplicated().any():
        raise DataQualityError("Kenneth French daily RF contains duplicate dates")
    destination.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(destination, index=False)
    return destination

