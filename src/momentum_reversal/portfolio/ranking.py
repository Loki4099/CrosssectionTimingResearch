"""Deterministic cross-sectional ranking and equal-weight selection."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd


def rank_and_select(
    scores: pd.Series,
    members: Iterable[str],
    top_n: int,
) -> pd.DataFrame:
    """Audit every PIT member and select the highest finite scores.

    Ties are resolved by ascending ``sid`` so repeated runs are identical and
    Top10 is always a subset of Top20/Top50 for one score vector.
    """

    if top_n <= 0:
        raise ValueError("top_n must be positive")
    member_index = pd.Index(sorted(set(members)), name="sid", dtype="object")
    if member_index.empty:
        raise ValueError("PIT membership is empty")
    member_scores = scores.reindex(member_index).astype(float)
    finite = np.isfinite(member_scores.to_numpy())

    ranked = pd.DataFrame({"score": member_scores, "eligible": finite}, index=member_index)
    ranked["exclusion_reason"] = np.where(ranked["eligible"], pd.NA, "missing_factor")
    eligible = (
        ranked.loc[ranked["eligible"]]
        .reset_index()
        .sort_values(["score", "sid"], ascending=[False, True], kind="mergesort")
    )
    if len(eligible) < top_n:
        raise ValueError(f"only {len(eligible)} eligible securities for Top{top_n}")

    rank_map = pd.Series(np.arange(1, len(eligible) + 1), index=eligible["sid"])
    ranked["rank"] = rank_map.reindex(ranked.index).astype("Int64")
    ranked["selected"] = ranked["rank"].le(top_n).fillna(False).astype(bool)
    return ranked.sort_values(["eligible", "rank"], ascending=[False, True], na_position="last")


def winner_loser_weights(
    scores: pd.Series,
    members: Iterable[str],
    n_each: int,
    *,
    gross_exposure: float = 1.0,
) -> pd.Series:
    """Build deterministic equal-weight winner-minus-loser targets.

    ``gross_exposure=1`` produces a fully collateralized, dollar-neutral book
    with ``+0.5`` allocated to the winners and ``-0.5`` to the losers.  Passing
    ``gross_exposure=2`` produces the conventional academic ``+1/-1`` WML
    return.  Score ties use ascending ``sid`` on both legs; the two legs are
    always disjoint.

    The returned Series contains only non-zero targets and is named
    ``target_weight``.  It is suitable for ``BaselineBacktester.run``'s
    ``target_weight_generator`` callback.
    """

    if n_each <= 0:
        raise ValueError("n_each must be positive")
    if not np.isfinite(gross_exposure) or gross_exposure <= 0:
        raise ValueError("gross_exposure must be finite and positive")
    if not isinstance(scores, pd.Series):
        raise TypeError("scores must be a pandas Series")

    member_index = pd.Index(sorted(set(map(str, members))), name="sid", dtype="object")
    if member_index.empty:
        raise ValueError("PIT membership is empty")
    member_scores = pd.to_numeric(scores.reindex(member_index), errors="coerce")
    eligible = pd.DataFrame(
        {"sid": member_index, "score": member_scores.to_numpy(dtype=float)}
    )
    eligible = eligible.loc[np.isfinite(eligible["score"])].copy()
    if len(eligible) < 2 * n_each:
        raise ValueError(
            f"only {len(eligible)} eligible securities for {n_each} winners "
            f"and {n_each} losers"
        )

    winners = eligible.sort_values(
        ["score", "sid"], ascending=[False, True], kind="mergesort"
    ).head(n_each)
    remaining = eligible.loc[~eligible["sid"].isin(winners["sid"])]
    losers = remaining.sort_values(
        ["score", "sid"], ascending=[True, True], kind="mergesort"
    ).head(n_each)
    if set(winners["sid"]).intersection(losers["sid"]):
        raise RuntimeError("winner and loser legs unexpectedly overlap")

    leg_weight = float(gross_exposure) / (2.0 * n_each)
    weights = pd.concat(
        [
            pd.Series(leg_weight, index=pd.Index(winners["sid"], name="sid")),
            pd.Series(-leg_weight, index=pd.Index(losers["sid"], name="sid")),
        ]
    ).sort_index()
    weights.name = "target_weight"
    return weights.astype(float)
