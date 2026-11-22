"""Pricing filters: grade text, collector numbers, JP name-search, EUR."""
from pricing_engine import CardIdentity, CardQuery, build_ebay_query_string, format_grade, is_relevant_title, number_in_title
from card_lookup import _extract_market_price
from tcgdex_lookup import pick_name_search_hit


def test_whole_grade_is_not_written_as_10_point_0():
    assert format_grade(10.0) == "10"
    assert format_grade(9.5) == "9.5"
    identity = CardIdentity(name="Charizard", set_name="Base", card_number="4", language="english")
    query = CardQuery(card=identity, is_graded=True, grade_company="PSA", grade=10.0)
    text = build_ebay_query_string(query)
    assert "PSA 10" in text
    assert "10.0" not in text
    assert is_relevant_title("PSA 10 Charizard Base 4/102", query) is True
    assert is_relevant_title("PSA 9 Charizard Base 4/102", query) is False


def test_collector_number_is_not_a_substring():
    assert number_in_title("25", "pikachu 25/102") is True
    assert number_in_title("25", "pikachu 125/165") is False
    assert number_in_title("25", "pikachu 250") is False
    assert number_in_title("063", "card 63/100") is True
    assert number_in_title("63", "card 163") is False


def test_name_search_prefers_a_modern_sv_card():
    items = [
        {"id": "neo1-10"},
        {"id": "base1-58"},
        {"id": "SV2a-173"},
        {"id": "SV8-1"},
    ]
    assert pick_name_search_hit(items)["id"] == "SV8-1"


def test_default_price_skips_first_edition_when_unlimited_has_no_market():
    card = {"tcgplayer": {"prices": {
        "1stEditionHolofoil": {"market": 500},
        "unlimitedHolofoil": {"low": 10},
    }}, "cardmarket": {"prices": {}}}
    price, source = _extract_market_price(card)
    assert price is None
    assert source == "tcgplayer"


def test_cardmarket_euros_are_converted():
    card = {"tcgplayer": {}, "cardmarket": {"prices": {"averageSellPrice": 10}}}
    price, source = _extract_market_price(card)
    assert price == 11.0
    assert source == "cardmarket"
