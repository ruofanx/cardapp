"""Tests for tcgdex_lookup._resolve_set_id — JP set-name -> TCGdex set-ID
alias table.

Bug: "Super Electric Breaker", "Paradise Dragona", and "Battle Partners"
were mapped to the WRONG TCGdex set IDs. SV7a is actually "Paradise
Dragona" (楽園ドラゴーナ) and SV8 is actually "Super Electric Breaker"
(超電ブレイカー) — the two were swapped in SET_NAME_ALIASES. Similarly
"Battle Partners" (バトルパートナーズ) is SV9, not SV9a (SV9a is "Arena of
Heat" / 熱風のアリーナ). Because _resolve_set_id feeds directly into
lookup_jp_card's direct-by-ID lookup, these swaps caused scans of cards
from these (very recent, popular) JP sets to attach a DIFFERENT card's
image — either a different printing of the same Pokemon (Paradise
Dragona <-> Super Electric Breaker) or an entirely unrelated card (Arena
of Heat instead of Battle Partners).
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from tcgdex_lookup import _resolve_set_id


def test_super_electric_breaker_resolves_to_sv8():
    assert _resolve_set_id("Super Electric Breaker") == "SV8"


def test_paradise_dragona_resolves_to_sv7a():
    assert _resolve_set_id("Paradise Dragona") == "SV7a"


def test_battle_partners_resolves_to_sv9():
    assert _resolve_set_id("Battle Partners") == "SV9"
    assert _resolve_set_id("Battle Partners ex") == "SV9"
    assert _resolve_set_id("SV: Battle Partners") == "SV9"
