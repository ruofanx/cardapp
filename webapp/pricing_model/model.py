# webapp/pricing_model/model.py
"""Model fitting: market index, binned lifecycle curve, cross-sectional
log-linear ridge regression. See spec "Model core" for the full design.

Two-stage estimation, deliberately not a single black-box fit:
  1. Market index — per-month median (price / that card's first-observed
     price) across the whole corpus. Captures "everything moved together."
  2. Lifecycle curve — per-card detrended relative price (index-adjusted),
     binned by months-since-release, median per bin. Captures the
     hype -> supply-slide -> trough -> recovery shape.
  3. Cross-sectional regression — each card's LATEST observation, with the
     market-index and lifecycle effects divided out, regressed on
     {era, language, log(pull_scarcity), character tier} (+ log(1/gem_rate)
     for the PSA10 head). Isolates fundamentals coefficients cleanly so
     every prediction decomposes into named multipliers.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import date

import numpy as np

from pricing_model import character_tiers, gem_rate, pull_rates, rarity_map
from pricing_model.db import CorpusCardRow, ModelRun

RIDGE_ALPHA = 1.0
MEDIAN_POLISH_ITERATIONS = 20

# cards_in_tier is fixed at 1 everywhere in this model -- both here
# (training) and wherever CardFeatures is built for prediction (see Task
# 12's build_card_features call, which must also pass 1). Per-set
# slot-competition effects are folded into pull_rates.py's per-tier config
# instead (via SET_OVERRIDES) rather than computed dynamically at both
# training and serving time, so the two can never drift apart on this
# parameter. Do not change one side without the other.
FIXED_CARDS_IN_TIER = 1

LIFECYCLE_BINS: list[tuple[float, float]] = [
    (0, 3), (3, 6), (6, 9), (9, 12), (12, 18), (18, 24), (24, 36), (36, 10_000),
]

FEATURE_ORDER_RAW: list[str] = [
    "intercept",
    "era:ecard_ex", "era:dp_pt", "era:bw_xy", "era:sm", "era:swsh", "era:sv",
    "lang:japanese", "lang:chinese",
    "log_pull_scarcity",
    "char:S", "char:A", "char:B", "char:D",
]
FEATURE_ORDER_PSA10: list[str] = FEATURE_ORDER_RAW + ["log_inv_gem_rate"]


def _bin_label(months: float) -> str:
    if months < 0:
        months = 0.0  # future/clock-skew release date: treat as newly released
    for lo, hi in LIFECYCLE_BINS:
        if lo <= months < hi:
            return f"{lo:g}-{hi:g}" if hi < 10_000 else f"{lo:g}+"
    return f"{LIFECYCLE_BINS[-1][0]:g}+"


def _month_to_date(month_str: str) -> date:
    y, m = month_str.split("-")
    return date(int(y), int(m), 1)


def _months_between(a: date, b: date) -> float:
    return (b.year - a.year) * 12 + (b.month - a.month)


def _market_index(history: dict[str, list[tuple[str, float]]]) -> dict[str, float]:
    """Chain-linked index: for each pair of adjacent months present in the
    corpus, the link ratio is the median (this-month price / prior-month
    price) across cards observed in BOTH months, and the index cumulates
    these links from 1.0 at the earliest month.

    NOT a per-card ratio against each card's own first observation: cards
    enter this corpus at different ages within the same calendar window
    (PriceCharting's ~33-month chart_data is calendar-anchored, ending
    "now", not anchored to each card's release date), so anchoring each
    card's ratio at its own first point would conflate "when a card
    happened to join the panel" with "the market moving" -- verified during
    review to bias the index by double-digit percentages under realistic
    staggered-entry corpora.
    """
    by_month: dict[str, dict[str, float]] = defaultdict(dict)
    for card_key, points in history.items():
        for month, price in points:
            if price > 0:
                by_month[month][card_key] = price

    months = sorted(by_month.keys())
    index: dict[str, float] = {}
    if not months:
        return index
    index[months[0]] = 1.0
    for prev_month, month in zip(months, months[1:]):
        prev_prices = by_month[prev_month]
        curr_prices = by_month[month]
        common_keys = set(prev_prices) & set(curr_prices)
        ratios = [curr_prices[k] / prev_prices[k] for k in common_keys if prev_prices[k] > 0]
        link = statistics.median(ratios) if ratios else 1.0
        index[month] = index[prev_month] * link
    return index


def _lifecycle_curve(
    cards_by_key: dict[str, CorpusCardRow],
    history: dict[str, list[tuple[str, float]]],
    market_index: dict[str, float],
    iterations: int = MEDIAN_POLISH_ITERATIONS,
) -> dict[str, float]:
    """Median-polish estimate of the age-dependent lifecycle multiplier from
    the market-detrended (card x age-bin) table.

    Anchoring each card's detrended series at its own first observation (an
    earlier draft's approach) has the same staggered-entry problem as a
    naive market index (see `_market_index`): the anchor point's age differs
    per card, so each card gets normalized to 1.0 at a DIFFERENT point on
    the true curve, and averaging those per-bin flattens or inverts the
    recovered shape -- confirmed during review with a constructed
    counter-example (known hype->trough->recovery truth recovered as a
    flat, partly-inverted curve under first-point anchoring).

    Median polish instead treats this as a two-way table (card x age bin)
    and iteratively removes row (card) and column (bin) medians in log
    space. This correctly separates each card's own price level from the
    age-dependent shape even though the table is unbalanced (different
    cards cover different bin ranges within their own ~33-month window) --
    confirmed during review to recover a known non-flat truth almost
    exactly on a corpus with staggered release ages.
    """
    cells: dict[str, dict[str, float]] = defaultdict(dict)
    for card_key, points in history.items():
        card = cards_by_key.get(card_key)
        if not card:
            continue
        release = _month_to_date(card.release_date[:7])
        for month, price in points:
            idx = market_index.get(month)
            if not idx or idx <= 0 or price <= 0:
                continue
            months_since = _months_between(release, _month_to_date(month))
            if months_since < 0:
                continue
            label = _bin_label(months_since)
            log_val = math.log(price / idx)
            prev = cells[card_key].get(label)
            cells[card_key][label] = log_val if prev is None else (prev + log_val) / 2

    if not cells:
        return {}

    # A card whose window covers only one age bin (common for older cards --
    # any card past ~36 months of age has its entire ~33-month window inside
    # the "36+" bin) is exactly 0 after row-centering, injecting a hard 0
    # into that bin's column median. On a production-shaped corpus this can
    # be roughly half the cards and measurably pulls the affected bin's
    # multiplier down (confirmed during review: ~9% low on "36+" vs its
    # true value). Only rows spanning >=2 bins carry information about the
    # bin-to-bin SHAPE, so single-bin rows are dropped before polishing --
    # they contribute nothing but noise to column medians.
    residual = {k: dict(v) for k, v in cells.items() if len(v) >= 2}
    col_effect: dict[str, float] = {}
    all_bins = {b for row in residual.values() for b in row}
    for b in all_bins:
        col_effect[b] = 0.0

    for _ in range(iterations):
        for row in residual.values():
            vals = list(row.values())
            if not vals:
                continue
            m = statistics.median(vals)
            for b in row:
                row[b] -= m
        for b in all_bins:
            vals = [row[b] for row in residual.values() if b in row]
            if not vals:
                continue
            m = statistics.median(vals)
            col_effect[b] += m
            for row in residual.values():
                if b in row:
                    row[b] -= m

    return {b: math.exp(v) for b, v in col_effect.items()}


def lifecycle_multiplier(curve: dict[str, float], months_since_release: float) -> float:
    if not curve:
        return 1.0
    label = _bin_label(months_since_release)
    if label in curve:
        return curve[label]
    values = sorted(curve.values())
    return values[len(values) // 2]


def _feature_row(order: list[str], card: CorpusCardRow, pull_scarcity: float,
                  char_tier: str, log_inv_gem_rate: float | None = None) -> list[float]:
    row = [0.0] * len(order)

    def set_if_present(key: str, value: float = 1.0):
        if key in order:
            row[order.index(key)] = value

    set_if_present("intercept")
    set_if_present(f"era:{card.era}")
    set_if_present(f"lang:{card.language}")
    set_if_present("log_pull_scarcity", math.log(max(pull_scarcity, 1e-6)))
    set_if_present(f"char:{char_tier}")
    if log_inv_gem_rate is not None:
        set_if_present("log_inv_gem_rate", log_inv_gem_rate)
    return row


def _ridge_fit(X: list[list[float]], y: list[float]) -> tuple[np.ndarray, float, float]:
    """Closed-form ridge regression: beta = (X^T X + alpha*I)^-1 X^T y,
    intercept left unregularized. Returns (beta, residual_std, r_squared).
    Assumes column 0 of X is the intercept -- true for both
    FEATURE_ORDER_RAW and FEATURE_ORDER_PSA10 (asserted in fit_model)."""
    Xm = np.array(X)
    ym = np.array(y)
    n, k = Xm.shape
    reg = np.eye(k) * RIDGE_ALPHA
    reg[0, 0] = 0.0  # never regularize the intercept
    beta = np.linalg.solve(Xm.T @ Xm + reg, Xm.T @ ym)
    residuals = ym - Xm @ beta
    dof = max(n - k, 1)
    residual_std = float(math.sqrt(float(np.sum(residuals ** 2)) / dof))
    ss_res = float(np.sum(residuals ** 2))
    ss_tot = float(np.sum((ym - float(np.mean(ym))) ** 2)) or 1.0
    r_squared = 1 - ss_res / ss_tot
    return beta, residual_std, r_squared


def fit_model(cards: list[CorpusCardRow], history: dict[str, list[tuple[str, float]]]) -> ModelRun:
    assert FEATURE_ORDER_RAW[0] == "intercept" and FEATURE_ORDER_PSA10[0] == "intercept"

    cards_by_key = {c.card_key: c for c in cards}
    market_index = _market_index(history)
    curve = _lifecycle_curve(cards_by_key, history, market_index)

    X_raw, y_raw = [], []
    X_psa, y_psa = [], []
    g9_ratios: list[float] = []

    for card in cards:
        points = history.get(card.card_key) or []
        if len(points) < 2:
            continue
        tier = rarity_map.normalize_rarity(card.rarity_raw)
        if tier is None:
            continue
        latest_month, latest_price = points[-1]
        if latest_price <= 0:
            continue
        idx = market_index.get(latest_month) or 1.0
        release = _month_to_date(card.release_date[:7])
        months_since = _months_between(release, _month_to_date(latest_month))
        mult = lifecycle_multiplier(curve, months_since) or 1.0
        char_tier = character_tiers.get_character_tier(card.name)
        scarcity = pull_rates.pull_rate_scarcity(card.era, tier, card.set_name, cards_in_tier=FIXED_CARDS_IN_TIER)

        target = math.log(latest_price / (idx * mult))
        X_raw.append(_feature_row(FEATURE_ORDER_RAW, card, scarcity, char_tier))
        y_raw.append(target)

        if card.psa10_price_usd and card.psa10_price_usd > 0:
            gem = gem_rate.estimate_gem_rate(card.era, tier, "standard")
            X_psa.append(_feature_row(
                FEATURE_ORDER_PSA10, card, scarcity, char_tier,
                log_inv_gem_rate=math.log(1.0 / gem),
            ))
            y_psa.append(math.log(card.psa10_price_usd / (idx * mult)))
            if card.grade9_price_usd and card.grade9_price_usd > 0:
                g9_ratios.append(card.grade9_price_usd / card.psa10_price_usd)

    if not X_raw:
        raise ValueError("fit_model: no valid training rows in corpus (check rarity mapping / history length)")

    beta_raw, residual_std_raw, r_squared_raw = _ridge_fit(X_raw, y_raw)
    coefficients_raw = {name: float(b) for name, b in zip(FEATURE_ORDER_RAW, beta_raw)}

    if X_psa:
        beta_psa, residual_std_psa10, r_squared_psa10 = _ridge_fit(X_psa, y_psa)
        coefficients_psa10 = {name: float(b) for name, b in zip(FEATURE_ORDER_PSA10, beta_psa)}
    else:
        residual_std_psa10, r_squared_psa10, coefficients_psa10 = 0.0, 0.0, {}

    psa9_fraction = statistics.median(g9_ratios) if g9_ratios else 0.4

    return ModelRun(
        id=None, fitted_at="",
        coefficients_raw=coefficients_raw, coefficients_psa10=coefficients_psa10,
        lifecycle_curve=curve, market_index=market_index,
        psa9_fraction=psa9_fraction,
        residual_std_raw=residual_std_raw, residual_std_psa10=residual_std_psa10,
        r_squared_raw=r_squared_raw, r_squared_psa10=r_squared_psa10,
        n_cards=len(cards),
    )
