"""Exchange-session scheduling without hard-coded Friday/Monday assumptions."""

from __future__ import annotations

from typing import Literal

import pandas as pd


RebalanceFrequency = Literal["weekly", "monthly"]


def rebalance_schedule(
    sessions: pd.DatetimeIndex,
    frequency: RebalanceFrequency,
) -> pd.DataFrame:
    """Map each period's last session close to the next session open.

    The final signal is dropped when the supplied calendar has no following
    session.  Holiday-shortened weeks therefore work automatically.
    """

    sessions = pd.DatetimeIndex(pd.to_datetime(sessions)).normalize().sort_values().unique()
    if sessions.tz is not None:
        raise ValueError("sessions must be timezone-naive")
    if frequency == "weekly":
        periods = sessions.to_period("W-FRI")
    elif frequency == "monthly":
        periods = sessions.to_period("M")
    else:
        raise ValueError("frequency must be 'weekly' or 'monthly'")

    positions = pd.Series(range(len(sessions)), index=sessions)
    signal_dates = pd.Series(sessions, index=periods).groupby(level=0, sort=True).last().array
    rows: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for signal_date in signal_dates:
        pos = int(positions.loc[signal_date])
        if pos + 1 < len(sessions):
            rows.append((signal_date, sessions[pos + 1]))
    result = pd.DataFrame(rows, columns=["signal_date", "execution_date"])
    if not result.empty:
        result = result.set_index("signal_date", drop=False)
    return result
