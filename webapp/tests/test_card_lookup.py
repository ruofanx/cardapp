"""Tests for card_lookup._score_match — promo number-mismatch scoring.

Bug: scanning a brand-new promo (2026 "First Partner Illustration Collection
S1" Charmander, printed as "Black Star Promos - 038") returned svp-47
(Scarlet & Violet Black Star Promos Charmander #47, $1.57) as the matched
card — wrong artwork, wrong price. The new printing isn't in the Pokemon TCG
API yet, so the number-strict query returns nothing and lookup_card falls
back to a broad name search. svp-47 scored 100 (substring set-name match +20,
"Promo" rarity matches the LLM's "Promo" variant +80) even though its card
number (47) doesn't match the OCR'd number (38) at all — "Promo" rarity and
"*Black Star Promos*" set names are shared by 200+ cards across a decade, so
neither signal actually pinpoints a specific printing without the number.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from card_lookup import _score_match, MIN_LOOKUP_SCORE


def _promo_card(number, set_name="Scarlet & Violet Black Star Promos", rarity="Promo"):
    return {"number": number, "set": {"name": set_name}, "rarity": rarity}


def test_promo_number_mismatch_with_substring_set_is_rejected():
    card = _promo_card("47")
    score = _score_match(card, want_number="38", want_set="Black Star Promos", want_variant="Promo")
    assert score < MIN_LOOKUP_SCORE


def test_promo_number_match_still_scores_high():
    card = _promo_card("38")
    score = _score_match(card, want_number="38", want_set="Black Star Promos", want_variant="Promo")
    assert score >= MIN_LOOKUP_SCORE


def test_exact_set_match_with_number_mismatch_unaffected():
    card = _promo_card("44", set_name="Black Star Promos")
    score = _score_match(card, want_number="38", want_set="Black Star Promos", want_variant="Promo")
    assert score >= MIN_LOOKUP_SCORE
