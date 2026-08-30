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
    for lo, hi in LIFECYCLE_BINS:
        if lo <= months < hi:
            return f"{lo:g}-{hi if hi < 10_000 else '36+'}"
    return "36+"


def _month_to_date(month_str: str) -> date:
    y, m = month_str.split("-")
    return date(int(y), int(m), 1)


def _months_between(a: date, b: date) -> float:
    return (b.year - a.year) * 12 + (b.month - a.month)


def _market_index(history: dict[str, list[tuple[str, float]]]) -> dict[str, float]:
    ratios_by_month: dict[str, list[float]] = defaultdict(list)
    for points in history.values():
        if len(points) < 2:
            continue
        base_price = points[0][1]
        if base_price <= 0:
            continue
        for month, price in points:
            ratios_by_month[month].append(price / base_price)
    return {month: statistics.median(ratios) for month, ratios in ratios_by_month.items()}


def _lifecycle_curve(
    cards_by_key: dict[str, CorpusCardRow],
    history: dict[str, list[tuple[str, float]]],
    market_index: dict[str, float],
) -> dict[str, float]:
    by_bin: dict[str, list[float]] = defaultdict(list)
    for card_key, points in history.items():
        card = cards_by_key.get(card_key)
        if not card or len(points) < 2:
            continue
        base_price = points[0][1]
        if base_price <= 0:
            continue
        release = _month_to_date(card.release_date[:7])
        for month, price in points:
            idx = market_index.get(month)
            if not idx or idx <= 0:
                continue
            detrended = (price / idx) / base_price
            months_since = _months_between(release, _month_to_date(month))
            if months_since < 0:
                continue
            by_bin[_bin_label(months_since)].append(detrended)
    return {b: statistics.median(v) for b, v in by_bin.items() if v}


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
    intercept left unregularized. Returns (beta, residual_std, r_squared)."""
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
        idx = market_index.get(latest_month, 1.0)
        release = _month_to_date(card.release_date[:7])
        months_since = _months_between(release, _month_to_date(latest_month))
        mult = lifecycle_multiplier(curve, months_since) or 1.0
        char_tier = character_tiers.get_character_tier(card.name)
        scarcity = pull_rates.pull_rate_scarcity(card.era, tier, card.set_name, cards_in_tier=1)

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
