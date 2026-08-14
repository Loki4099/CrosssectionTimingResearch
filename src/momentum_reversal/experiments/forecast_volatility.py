"""Frozen V4 rolling forecasts of future strategy volatility."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from itertools import product

import numpy as np
import pandas as pd


class VolatilityForecastModel(StrEnum):
    PERSISTENCE = "persistence"
    EWMA_094 = "ewma_094"
    HAR_RV = "har_rv"


@dataclass(frozen=True, order=True, slots=True)
class ForecastScalingSpec:
    model: VolatilityForecastModel
    max_exposure: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "model", VolatilityForecastModel(self.model))
        if self.max_exposure not in {1.0, 1.5}:
            raise ValueError("V4 max exposure must be 1.0 or 1.5")

    @property
    def experiment_suffix(self) -> str:
        cap = round(self.max_exposure * 100)
        return f"{self.model.value}__cap{cap}"


def v4_forecast_specs() -> tuple[ForecastScalingSpec, ...]:
    return tuple(
        ForecastScalingSpec(model, cap)
        for model, cap in product(tuple(VolatilityForecastModel), (1.0, 1.5))
    )


def rolling_volatility_forecasts(
    returns: pd.Series,
    *,
    horizon: int = 21,
    ewma_decay: float = 0.94,
    har_train_window: int = 504,
    numerical_variance_floor: float = 1e-12,
) -> pd.DataFrame:
    """Forecast annualized volatility using causal Persistence/EWMA/HAR.

    HAR predicts log future mean daily squared return. At prediction close
    ``t``, the newest training-label origin is ``t-horizon``; its future window
    ends at ``t`` and is therefore fully observed. Exactly the latest
    ``har_train_window`` matured labels are used.
    """

    if horizon < 2:
        raise ValueError("horizon must be at least two sessions")
    if not 0.0 < ewma_decay < 1.0:
        raise ValueError("ewma_decay must lie in (0, 1)")
    if har_train_window < 20:
        raise ValueError("HAR training window is too short")
    if numerical_variance_floor <= 0:
        raise ValueError("numerical variance floor must be positive")
    values = _validated_returns(returns)
    squared = values.pow(2)

    trailing_5 = squared.rolling(5, min_periods=5).mean()
    trailing_21 = squared.rolling(21, min_periods=21).mean()
    future_variance = squared.rolling(horizon, min_periods=horizon).mean().shift(
        -horizon
    )
    persistence_variance = squared.rolling(
        horizon, min_periods=horizon
    ).mean()
    ewma_variance = squared.ewm(
        alpha=1.0 - ewma_decay, adjust=False
    ).mean()

    features = np.column_stack(
        [
            np.ones(len(values), dtype=float),
            np.log(squared.clip(lower=numerical_variance_floor).to_numpy()),
            np.log(trailing_5.clip(lower=numerical_variance_floor).to_numpy()),
            np.log(trailing_21.clip(lower=numerical_variance_floor).to_numpy()),
        ]
    )
    target = np.log(
        future_variance.clip(lower=numerical_variance_floor).to_numpy()
    )
    har_variance = np.full(len(values), np.nan, dtype=float)
    first_feature_position = 20
    first_prediction_position = (
        first_feature_position + har_train_window - 1 + horizon
    )
    for prediction_position in range(first_prediction_position, len(values)):
        train_end = prediction_position - horizon
        train_start = train_end - har_train_window + 1
        x_train = features[train_start : train_end + 1]
        y_train = target[train_start : train_end + 1]
        if (
            len(y_train) != har_train_window
            or not np.isfinite(x_train).all()
            or not np.isfinite(y_train).all()
            or not np.isfinite(features[prediction_position]).all()
        ):
            continue
        coefficients, *_ = np.linalg.lstsq(x_train, y_train, rcond=None)
        predicted_log_variance = float(
            features[prediction_position] @ coefficients
        )
        predicted_variance = float(np.exp(predicted_log_variance))
        if np.isfinite(predicted_variance) and predicted_variance > 0.0:
            har_variance[prediction_position] = predicted_variance

    annualization = 252.0
    output = pd.DataFrame(
        {
            "actual_future_volatility": np.sqrt(
                annualization * future_variance
            ),
            VolatilityForecastModel.PERSISTENCE.value: np.sqrt(
                annualization * persistence_variance
            ),
            VolatilityForecastModel.EWMA_094.value: np.sqrt(
                annualization * ewma_variance
            ),
            VolatilityForecastModel.HAR_RV.value: np.sqrt(
                annualization * har_variance
            ),
        },
        index=values.index,
    )
    output.index.name = values.index.name or "date"
    return output


def common_forecast_activation_date(
    forecasts: pd.DataFrame, signal_dates: pd.Index
) -> pd.Timestamp:
    required = [model.value for model in VolatilityForecastModel]
    missing_columns = set(required).difference(forecasts.columns)
    if missing_columns:
        raise ValueError(f"forecasts are missing columns: {sorted(missing_columns)}")
    dates = pd.DatetimeIndex(pd.to_datetime(signal_dates)).normalize()
    available = forecasts.reindex(dates)[required].apply(
        lambda column: np.isfinite(pd.to_numeric(column, errors="coerce"))
        & pd.to_numeric(column, errors="coerce").gt(0.0)
    )
    valid = available.all(axis=1)
    if not valid.any():
        raise ValueError("no signal date has all three volatility forecasts")
    return pd.Timestamp(dates[valid.to_numpy()][0])


def forecast_target_allocation(
    forecast: pd.Series,
    signal_dates: pd.Index,
    *,
    activation_date: object,
    target_volatility: float = 0.15,
    max_exposure: float = 1.0,
) -> pd.Series:
    """Stay at 1x before common activation, then apply target/forecast."""

    if target_volatility <= 0 or max_exposure <= 0:
        raise ValueError("target volatility and max exposure must be positive")
    dates = pd.DatetimeIndex(pd.to_datetime(signal_dates)).normalize()
    if dates.has_duplicates or dates.tz is not None:
        raise ValueError("signal dates must be unique and timezone-naive")
    activation = pd.Timestamp(activation_date).normalize()
    values = pd.to_numeric(forecast, errors="coerce").reindex(dates)
    active = dates >= activation
    invalid = active & (values.isna() | ~np.isfinite(values) | values.le(0.0))
    if invalid.any():
        raise ValueError(
            f"forecast is unavailable after activation on {dates[invalid][:5].tolist()}"
        )
    allocation = pd.Series(1.0, index=dates, dtype=float)
    allocation.loc[active] = (
        target_volatility / values.loc[active]
    ).clip(lower=0.0, upper=max_exposure)
    allocation.name = "target_risk_allocation"
    return allocation


def volatility_forecast_metrics(forecasts: pd.DataFrame) -> pd.DataFrame:
    """Evaluate all models on their shared HAR-available, matured-label sample."""

    actual_column = "actual_future_volatility"
    model_columns = [model.value for model in VolatilityForecastModel]
    required = [actual_column, *model_columns]
    missing = set(required).difference(forecasts.columns)
    if missing:
        raise ValueError(f"forecasts are missing columns: {sorted(missing)}")
    numeric = forecasts[required].apply(pd.to_numeric, errors="coerce")
    common = np.isfinite(numeric).all(axis=1) & numeric.gt(0.0).all(axis=1)
    sample = numeric.loc[common]
    if sample.empty:
        raise ValueError("no common forecast evaluation sample")
    actual_volatility = sample[actual_column].to_numpy(dtype=float)
    actual_variance = np.square(actual_volatility)
    denominator = float(
        np.square(actual_variance - actual_variance.mean()).sum()
    )
    rows: list[dict[str, float | str | pd.Timestamp]] = []
    for model in model_columns:
        forecast_volatility = sample[model].to_numpy(dtype=float)
        forecast_variance = np.square(forecast_volatility)
        ratio = actual_variance / forecast_variance
        qlike = float(np.mean(ratio - np.log(ratio) - 1.0))
        squared_error = float(
            np.square(actual_variance - forecast_variance).sum()
        )
        rows.append(
            {
                "model": model,
                "observations": float(len(sample)),
                "evaluation_start": pd.Timestamp(sample.index[0]),
                "evaluation_end": pd.Timestamp(sample.index[-1]),
                "qlike": qlike,
                "mae_volatility": float(
                    np.mean(np.abs(actual_volatility - forecast_volatility))
                ),
                "variance_r_squared": (
                    float(1.0 - squared_error / denominator)
                    if denominator > 0
                    else np.nan
                ),
                "volatility_correlation": float(
                    np.corrcoef(actual_volatility, forecast_volatility)[0, 1]
                ),
                "mean_forecast_volatility": float(forecast_volatility.mean()),
                "mean_actual_volatility": float(actual_volatility.mean()),
            }
        )
    return pd.DataFrame(rows)


def _validated_returns(returns: pd.Series) -> pd.Series:
    if not isinstance(returns, pd.Series):
        raise TypeError("returns must be a pandas Series")
    if not isinstance(returns.index, pd.DatetimeIndex):
        raise ValueError("returns must use a DatetimeIndex")
    if (
        returns.index.tz is not None
        or returns.index.has_duplicates
        or not returns.index.is_monotonic_increasing
    ):
        raise ValueError("returns index must be unique, increasing, and timezone-naive")
    values = pd.to_numeric(returns, errors="coerce").astype(float)
    if values.isna().any() or not np.isfinite(values).all() or values.le(-1.0).any():
        raise ValueError("returns must be finite and greater than -100%")
    return values
