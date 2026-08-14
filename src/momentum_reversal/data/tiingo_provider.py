"""Tiingo EOD adapter producing the project's canonical price schema.

The adapter intentionally uses only the Python standard library for HTTP and
``.env`` handling.  A transport can be injected for deterministic offline
tests.  Authentication is sent exclusively in the ``Authorization`` header;
the API token is never placed in a URL or an exception message.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import json
import math
import os
from pathlib import Path
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

from .provider import AssetRef, PriceRequest
from .schema import (
    CANONICAL_PRICE_COLUMNS,
    DataSchemaError,
    canonicalize_prices,
    normalize_session_date,
    validate_canonical_prices,
)


DEFAULT_TIINGO_BASE_URL = "https://api.tiingo.com"
DEFAULT_TIINGO_TOKEN_ENV = "TIINGO_API_TOKEN"


class TiingoCredentialError(RuntimeError):
    """Raised when no usable Tiingo API token can be resolved."""


class TiingoDownloadError(RuntimeError):
    """Raised when Tiingo cannot return a usable EOD response."""


TiingoTransport = Callable[..., object]


_REQUIRED_FIELDS = frozenset(
    {
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "adjOpen",
        "adjHigh",
        "adjLow",
        "adjClose",
        "divCash",
        "splitFactor",
    }
)
_DOTENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class TiingoProvider:
    """Fetch Tiingo EOD prices using native raw and adjusted OHLC fields.

    Credential lookup order is: an explicit ``api_token``, the process
    environment, then ``TIINGO_API_TOKEN`` in an explicitly selected dotenv
    file or the project-root ``.env``.  ``environment`` and ``transport`` are
    injectable to keep all credential and network behavior testable offline.
    """

    def __init__(
        self,
        *,
        api_token: str | None = None,
        dotenv_path: str | Path | None = None,
        project_root: str | Path | None = None,
        environment: Mapping[str, str] | None = None,
        token_env_var: str = DEFAULT_TIINGO_TOKEN_ENV,
        transport: TiingoTransport | None = None,
        base_url: str = DEFAULT_TIINGO_BASE_URL,
        timeout: float = 30.0,
    ) -> None:
        if not token_env_var or not _DOTENV_KEY.fullmatch(token_env_var):
            raise ValueError("token_env_var must be a valid environment variable name")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a positive finite number")
        if not base_url.strip():
            raise ValueError("base_url must be non-empty")

        self._api_token = resolve_tiingo_api_token(
            api_token=api_token,
            dotenv_path=dotenv_path,
            project_root=project_root,
            environment=environment,
            token_env_var=token_env_var,
        )
        self._transport = transport or _urllib_json_transport
        self._base_url = base_url.rstrip("/")
        self._timeout = float(timeout)

    def fetch_prices(self, request: PriceRequest) -> pd.DataFrame:
        """Fetch every asset over the request's inclusive date interval."""

        frames: list[pd.DataFrame] = []
        for asset in request.assets:
            payload = self._fetch_asset(asset, request)
            frame = normalize_tiingo_response(payload, asset)
            dates = frame.index.get_level_values("date")
            frame = frame.loc[(dates >= request.start) & (dates <= request.end)]
            if frame.empty:
                raise TiingoDownloadError(
                    f"Tiingo returned no rows inside the requested interval for "
                    f"{asset.symbol}"
                )
            frames.append(frame)

        combined = canonicalize_prices(
            pd.concat(frames, axis=0), required_columns=("tr_open", "tr_close")
        )
        validate_canonical_prices(combined)
        return combined

    def _fetch_asset(self, asset: AssetRef, request: PriceRequest) -> object:
        encoded_symbol = quote(asset.symbol, safe="")
        url = f"{self._base_url}/tiingo/daily/{encoded_symbol}/prices"
        params = {
            # Tiingo's EOD endpoint accepts inclusive start/end dates.  Unlike
            # Yahoo, the end date must not be advanced by one day.
            "startDate": request.start.strftime("%Y-%m-%d"),
            "endDate": request.end.strftime("%Y-%m-%d"),
        }
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Token {self._api_token}",
        }
        try:
            payload = self._transport(
                url,
                params=params,
                headers=headers,
                timeout=self._timeout,
            )
        except TiingoDownloadError:
            raise
        except Exception:
            # Do not chain arbitrary transport exceptions: third-party
            # transports sometimes include request headers in their message.
            raise TiingoDownloadError(
                f"Tiingo request failed for {asset.symbol}"
            ) from None

        if payload is None or (
            isinstance(payload, Sequence)
            and not isinstance(payload, (str, bytes, bytearray))
            and len(payload) == 0
        ):
            raise TiingoDownloadError(
                f"Tiingo returned no price rows for {asset.symbol}"
            )
        return payload


def resolve_tiingo_api_token(
    *,
    api_token: str | None = None,
    dotenv_path: str | Path | None = None,
    project_root: str | Path | None = None,
    environment: Mapping[str, str] | None = None,
    token_env_var: str = DEFAULT_TIINGO_TOKEN_ENV,
) -> str:
    """Resolve a token without printing, logging, or returning its source.

    An explicitly provided dotenv path must exist.  With no path, only the
    ``.env`` at the discovered project root is considered; nested dotenv files
    are not searched or merged.
    """

    if not token_env_var or not _DOTENV_KEY.fullmatch(token_env_var):
        raise ValueError("token_env_var must be a valid environment variable name")

    if api_token is not None:
        token = api_token.strip()
        if not token:
            raise TiingoCredentialError("explicit Tiingo api_token is empty")
        return token

    env = os.environ if environment is None else environment
    env_token = env.get(token_env_var)
    if env_token is not None and env_token.strip():
        return env_token.strip()

    explicit_dotenv = dotenv_path is not None
    if explicit_dotenv:
        selected_path = Path(dotenv_path).expanduser()
    else:
        root = (
            Path(project_root).expanduser()
            if project_root is not None
            else _discover_project_root(Path.cwd())
        )
        selected_path = root / ".env"

    if not selected_path.is_file():
        if explicit_dotenv:
            raise TiingoCredentialError(
                f"Tiingo dotenv file does not exist: {selected_path}"
            )
    else:
        dotenv_token = _read_dotenv_value(selected_path, token_env_var)
        if dotenv_token is not None and dotenv_token.strip():
            return dotenv_token.strip()

    raise TiingoCredentialError(
        f"Tiingo API token is missing; pass api_token or set {token_env_var}"
    )


def normalize_tiingo_response(payload: object, asset: AssetRef) -> pd.DataFrame:
    """Convert one Tiingo EOD JSON array to canonical long-form prices."""

    if not isinstance(payload, list) or not payload:
        raise TiingoDownloadError(
            f"Tiingo response for {asset.symbol} must be a non-empty JSON array"
        )
    if any(not isinstance(row, Mapping) for row in payload):
        raise TiingoDownloadError(
            f"Tiingo response for {asset.symbol} contains a non-object row"
        )

    rows = [dict(row) for row in payload]
    missing = sorted(_REQUIRED_FIELDS.difference(set.intersection(*(set(row) for row in rows))))
    if missing:
        raise DataSchemaError(
            f"Tiingo rows for {asset.symbol} missing required fields: {missing}"
        )

    source = pd.DataFrame(rows)
    dates: list[pd.Timestamp] = []
    for value in source["date"]:
        if value is None or pd.isna(value):
            raise DataSchemaError(f"Tiingo rows for {asset.symbol} contain an invalid date")
        try:
            date = normalize_session_date(value)
        except (TypeError, ValueError, OverflowError):
            raise DataSchemaError(
                f"Tiingo rows for {asset.symbol} contain an invalid date"
            ) from None
        if pd.isna(date):
            raise DataSchemaError(f"Tiingo rows for {asset.symbol} contain an invalid date")
        dates.append(date)

    converted = pd.DataFrame({"date": dates})
    field_mapping = {
        "adjOpen": "tr_open",
        "adjHigh": "tr_high",
        "adjLow": "tr_low",
        "adjClose": "tr_close",
        "open": "raw_open",
        "high": "raw_high",
        "low": "raw_low",
        "close": "raw_close",
    }
    for source_name, canonical_name in field_mapping.items():
        values = pd.to_numeric(source[source_name], errors="coerce")
        invalid = values.isna() | ~np.isfinite(values) | (values <= 0)
        if invalid.any():
            raise DataSchemaError(
                f"Tiingo field {source_name} for {asset.symbol} contains invalid values"
            )
        # Use the native Tiingo adjusted fields directly.  Do not infer a
        # common factor from raw and adjusted close.
        converted[canonical_name] = values.to_numpy(dtype=float)

    volume = pd.to_numeric(source["volume"], errors="coerce")
    if (volume.isna() | ~np.isfinite(volume) | (volume < 0)).any():
        raise DataSchemaError(
            f"Tiingo field volume for {asset.symbol} contains invalid values"
        )
    converted["volume"] = volume.to_numpy(dtype=float)

    dividends = pd.to_numeric(source["divCash"], errors="coerce")
    if (dividends.isna() | ~np.isfinite(dividends)).any():
        raise DataSchemaError(
            f"Tiingo field divCash for {asset.symbol} contains invalid values"
        )
    converted["dividends"] = dividends.to_numpy(dtype=float)

    split_factors = pd.to_numeric(source["splitFactor"], errors="coerce")
    if (split_factors.isna() | ~np.isfinite(split_factors) | (split_factors <= 0)).any():
        raise DataSchemaError(
            f"Tiingo field splitFactor for {asset.symbol} contains invalid values"
        )
    # Tiingo uses 1.0 on non-event days; the canonical/Yahoo convention uses
    # 0.0 when no split occurred and the actual factor on an event day.
    converted["stock_splits"] = np.where(
        split_factors.to_numpy(dtype=float) == 1.0,
        0.0,
        split_factors.to_numpy(dtype=float),
    )
    converted["source_symbol"] = asset.symbol
    converted["sid"] = asset.sid
    converted = converted.set_index(["date", "sid"])

    ordered = [*CANONICAL_PRICE_COLUMNS, "source_symbol"]
    result = canonicalize_prices(converted.loc[:, ordered])
    validate_canonical_prices(result)
    return result


def _discover_project_root(start: Path) -> Path:
    current = start.expanduser().resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return current


def _read_dotenv_value(path: Path, key: str) -> str | None:
    selected: str | None = None
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        raise TiingoCredentialError(f"cannot read Tiingo dotenv file: {path}") from None

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        candidate_key, raw_value = line.split("=", 1)
        candidate_key = candidate_key.strip()
        if not _DOTENV_KEY.fullmatch(candidate_key):
            raise TiingoCredentialError(
                f"invalid dotenv key at {path}:{line_number}"
            )
        if candidate_key != key:
            continue
        selected = _parse_dotenv_value(raw_value, path=path, line_number=line_number)
    return selected


def _parse_dotenv_value(raw_value: str, *, path: Path, line_number: int) -> str:
    value = raw_value.strip()
    if not value:
        return ""
    if value[0] in {"'", '"'}:
        quote_character = value[0]
        closing_quote = value.find(quote_character, 1)
        if closing_quote < 0:
            raise TiingoCredentialError(
                f"unterminated dotenv value at {path}:{line_number}"
            )
        trailing = value[closing_quote + 1 :].strip()
        if trailing and not trailing.startswith("#"):
            raise TiingoCredentialError(
                f"invalid text after dotenv value at {path}:{line_number}"
            )
        return value[1:closing_quote]
    # Preserve '#' inside a token, but allow the common ``VALUE  # comment``
    # form.  Variable interpolation is intentionally unsupported.
    return re.split(r"\s+#", value, maxsplit=1)[0].rstrip()


def _urllib_json_transport(
    url: str,
    *,
    params: Mapping[str, str],
    headers: Mapping[str, str],
    timeout: float,
) -> object:
    query = urlencode(dict(params))
    request = Request(f"{url}?{query}", headers=dict(headers), method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            status = int(getattr(response, "status", 200))
            body = response.read()
    except HTTPError as exc:
        raise TiingoDownloadError(
            f"Tiingo HTTP request failed with status {exc.code}"
        ) from None
    except (URLError, TimeoutError, OSError):
        raise TiingoDownloadError("Tiingo HTTP request failed") from None

    if not 200 <= status < 300:
        raise TiingoDownloadError(f"Tiingo HTTP request failed with status {status}")
    if not body:
        return []
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise TiingoDownloadError("Tiingo returned invalid JSON") from None
