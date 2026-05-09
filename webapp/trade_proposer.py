"""
Subset-sum trade proposer.

Given a target dollar value and a user's collection, find combinations of
their cards whose total market value falls within tolerance of the target.

Algorithm:
- Filter out cards without a current_market_price (we can't trade what we
  haven't valued).
- Brute-force enumerate combinations up to MAX_K cards (default 5). For
  ~50 cards this is C(50,5) ≈ 2.1M — fast enough in Python (sub-second).
- Return the top N combinations sorted by:
    1. closeness to target (smaller |sum - target| first)
    2. fewer cards (cleaner trades preferred)

For very large collections (>200 cards) we'd want a meet-in-the-middle DP,
but that's not needed for personal collections.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import List

from db import Card


@dataclass
class TradeOption:
    cards: List[Card]
    total_value: float
    delta_to_target: float       # signed: + means over target, - means under
    n_cards: int

    def to_dict(self):
        return {
            "cards": [
                {
                    "id": c.id,
                    "name": c.name,
                    "set_name": c.set_name,
                    "card_number": c.card_number,
                    "variant": c.variant,
                    "language": c.language,
                    "condition": c.condition,
                    "current_market_price": c.current_market_price,
                    "image_url": c.image_url,
                }
                for c in self.cards
            ],
            "total_value": round(self.total_value, 2),
            "delta_to_target": round(self.delta_to_target, 2),
            "n_cards": self.n_cards,
        }


def propose_trades(
    collection: List[Card],
    target_value: float,
    tolerance: float = 5.0,
    max_combo_size: int = 5,
    max_results: int = 10,
    exclude_card_ids: set[int] | None = None,
) -> List[TradeOption]:
    """
    Find combinations from `collection` summing to within `tolerance` of `target_value`.

    Args:
        collection: list of Card objects (only those with current_market_price are used)
        target_value: dollar amount to match
        tolerance: |sum - target| must be ≤ this to be included (USD)
        max_combo_size: max number of cards per combination (default 5)
        max_results: cap on number of combinations returned
        exclude_card_ids: card IDs to exclude (e.g. the cards being traded *in*)
    """
    exclude_card_ids = exclude_card_ids or set()
    priced = [c for c in collection
              if c.current_market_price is not None
              and c.id not in exclude_card_ids]

    if not priced:
        return []

    # Pruning: pre-sort by price descending so we can early-exit if even the
    # max-value combo of size k is below (target - tolerance).
    priced.sort(key=lambda c: c.current_market_price or 0, reverse=True)
    max_price = priced[0].current_market_price or 0
    min_price = priced[-1].current_market_price or 0

    options: List[TradeOption] = []
    for k in range(1, min(max_combo_size, len(priced)) + 1):
        # Quick reachability check: can k cards even sum to within tolerance?
        max_sum_at_k = max_price * k
        min_sum_at_k = min_price * k
        if max_sum_at_k < target_value - tolerance:
            continue
        if min_sum_at_k > target_value + tolerance:
            continue

        for combo in combinations(priced, k):
            total = sum(c.current_market_price or 0 for c in combo)
            delta = total - target_value
            if abs(delta) <= tolerance:
                options.append(TradeOption(
                    cards=list(combo),
                    total_value=total,
                    delta_to_target=delta,
                    n_cards=k,
                ))

    # Sort: closest match first, then fewest cards, then highest single-card
    # value (tiebreaker — bigger cards usually preferred in trades).
    options.sort(key=lambda o: (
        abs(o.delta_to_target),
        o.n_cards,
        -max((c.current_market_price or 0) for c in o.cards),
    ))
    return options[:max_results]
