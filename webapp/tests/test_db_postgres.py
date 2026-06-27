"""Integration tests for db_postgres — requires DATABASE_URL env var."""
import os
import pytest
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../.env"))

if not os.environ.get("DATABASE_URL"):
    pytest.skip("No DATABASE_URL set — skipping PostgreSQL integration tests",
                allow_module_level=True)

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import db_postgres as db

@pytest.fixture
def test_user():
    """Create a test user, yield it, then delete (cascades to cards/tags)."""
    user = db.create_user("_test_user_", "#ff0000")
    yield user
    db.delete_user(user.id)

def test_create_and_get_user(test_user):
    u = db.get_user(test_user.id)
    assert u is not None
    assert u.name == "_test_user_"
    assert u.avatar_color == "#ff0000"

def test_list_users_includes_test_user(test_user):
    users = db.list_users()
    names = [u.name for u in users]
    assert "_test_user_" in names

def test_create_and_get_card(test_user):
    from db_postgres import Card
    card = db.create_card(Card(
        id=None, user_id=test_user.id,
        name="Charizard", set_name="Base Set",
        card_number="4/102", language="english",
    ))
    assert card.id is not None
    assert card.name == "Charizard"
    fetched = db.get_card(card.id)
    assert fetched is not None
    assert fetched.name == "Charizard"

def test_update_card(test_user):
    from db_postgres import Card
    card = db.create_card(Card(
        id=None, user_id=test_user.id, name="Pikachu", language="english"
    ))
    updated = db.update_card(card.id, current_market_price=25.50)
    assert updated.current_market_price == 25.50

def test_delete_card(test_user):
    from db_postgres import Card
    card = db.create_card(Card(
        id=None, user_id=test_user.id, name="Mewtwo", language="english"
    ))
    assert db.delete_card(card.id) is True
    assert db.get_card(card.id) is None

def test_price_history(test_user):
    from db_postgres import Card
    card = db.create_card(Card(
        id=None, user_id=test_user.id, name="Blastoise", language="english"
    ))
    db.log_price(card.id, 100.0, source="test")
    db.log_price(card.id, 110.0, source="test")
    history = db.get_price_history(card.id)
    assert len(history) == 2
    assert history[0]["price"] == 100.0
    assert history[1]["price"] == 110.0

def test_log_price_dedup(test_user):
    from db_postgres import Card
    card = db.create_card(Card(
        id=None, user_id=test_user.id, name="Venusaur", language="english"
    ))
    db.log_price(card.id, 50.0, source="test")
    db.log_price(card.id, 50.004, source="test")  # within $0.005 tolerance
    history = db.get_price_history(card.id)
    assert len(history) == 1  # deduped

def test_tags(test_user):
    tag = db.create_tag(test_user.id, "for trade", "#f97316", is_trade_tag=True)
    assert tag.id is not None
    from db_postgres import Card
    card = db.create_card(Card(
        id=None, user_id=test_user.id, name="Raichu", language="english"
    ))
    db.add_tag_to_card(card.id, tag.id)
    fetched = db.get_card(card.id)
    assert any(t.id == tag.id for t in fetched.tags)

def test_portfolio_summary(test_user):
    from db_postgres import Card
    db.create_card(Card(
        id=None, user_id=test_user.id, name="Snorlax",
        language="english", purchase_price=10.0, current_market_price=15.0
    ))
    summary = db.portfolio_summary(test_user.id)
    assert summary.card_count == 1
    assert summary.total_market_value == 15.0
