from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient

import app as app_module


def test_account_is_pro_true_for_paid_plan():
    assert app_module.account_is_pro({"plan": "pro", "trial_ends_at": None}) is True


def test_account_is_pro_true_during_trial():
    future = datetime.now(timezone.utc) + timedelta(days=1)
    assert app_module.account_is_pro({"plan": "free", "trial_ends_at": future}) is True


def test_account_is_pro_false_after_trial_expires():
    past = datetime.now(timezone.utc) - timedelta(days=1)
    assert app_module.account_is_pro({"plan": "free", "trial_ends_at": past}) is False


def test_account_is_pro_false_for_free_no_trial():
    assert app_module.account_is_pro({"plan": "free", "trial_ends_at": None}) is False


WEBHOOK_SECRET = "test-secret-value"


@pytest.fixture(autouse=True)
def _set_secret(monkeypatch):
    monkeypatch.setattr(app_module, "REVENUECAT_WEBHOOK_SECRET", WEBHOOK_SECRET)


@pytest.fixture
def client():
    return TestClient(app_module.app)


def _event(**overrides):
    base = {
        "type": "INITIAL_PURCHASE",
        "app_user_id": "acct-123",
        "entitlement_ids": ["pro"],
        "period_type": "NORMAL",
        "expiration_at_ms": 4102444800000,
    }
    base.update(overrides)
    return {"event": base}


def test_webhook_wrong_secret_returns_401(client):
    res = client.post("/api/webhooks/revenuecat", json=_event(), headers={"Authorization": "wrong"})
    assert res.status_code == 401


def test_webhook_missing_secret_header_returns_401(client):
    res = client.post("/api/webhooks/revenuecat", json=_event())
    assert res.status_code == 401


def test_webhook_malformed_payload_returns_400(client):
    res = client.post("/api/webhooks/revenuecat", json={"not_event": {}}, headers={"Authorization": WEBHOOK_SECRET})
    assert res.status_code == 400


def test_webhook_initial_purchase_sets_plan_pro(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(app_module.db, "get_account", lambda uid: {"id": uid, "plan": "free", "trial_ends_at": None})
    monkeypatch.setattr(app_module.db, "update_account_plan",
                         lambda uid, *, plan, trial_ends_at: captured.update(uid=uid, plan=plan, trial_ends_at=trial_ends_at))
    res = client.post("/api/webhooks/revenuecat", json=_event(), headers={"Authorization": WEBHOOK_SECRET})
    assert res.status_code == 200
    assert captured == {"uid": "acct-123", "plan": "pro", "trial_ends_at": None}


def test_webhook_trial_period_sets_trial_ends_at(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(app_module.db, "get_account", lambda uid: {"id": uid, "plan": "free", "trial_ends_at": None})
    monkeypatch.setattr(app_module.db, "update_account_plan",
                         lambda uid, *, plan, trial_ends_at: captured.update(uid=uid, plan=plan, trial_ends_at=trial_ends_at))
    res = client.post("/api/webhooks/revenuecat", json=_event(period_type="TRIAL"), headers={"Authorization": WEBHOOK_SECRET})
    assert res.status_code == 200
    assert captured["plan"] == "pro"
    assert captured["trial_ends_at"] is not None


def test_webhook_expiration_sets_plan_free(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(app_module.db, "get_account", lambda uid: {"id": uid, "plan": "pro", "trial_ends_at": None})
    monkeypatch.setattr(app_module.db, "update_account_plan",
                         lambda uid, *, plan, trial_ends_at: captured.update(uid=uid, plan=plan, trial_ends_at=trial_ends_at))
    res = client.post("/api/webhooks/revenuecat", json=_event(type="EXPIRATION", entitlement_ids=[]),
                       headers={"Authorization": WEBHOOK_SECRET})
    assert res.status_code == 200
    assert captured == {"uid": "acct-123", "plan": "free", "trial_ends_at": None}


def test_webhook_cancellation_is_noop(client, monkeypatch):
    monkeypatch.setattr(app_module.db, "get_account", lambda uid: {"id": uid, "plan": "pro", "trial_ends_at": None})

    def boom(*a, **k):
        raise AssertionError("update_account_plan should not be called for CANCELLATION")
    monkeypatch.setattr(app_module.db, "update_account_plan", boom)
    res = client.post("/api/webhooks/revenuecat", json=_event(type="CANCELLATION"), headers={"Authorization": WEBHOOK_SECRET})
    assert res.status_code == 200


def test_webhook_unknown_account_returns_200_without_update(client, monkeypatch):
    monkeypatch.setattr(app_module.db, "get_account", lambda uid: None)

    def boom(*a, **k):
        raise AssertionError("update_account_plan should not be called for an unknown account")
    monkeypatch.setattr(app_module.db, "update_account_plan", boom)
    res = client.post("/api/webhooks/revenuecat", json=_event(), headers={"Authorization": WEBHOOK_SECRET})
    assert res.status_code == 200


def test_webhook_db_failure_returns_500(client, monkeypatch):
    monkeypatch.setattr(app_module.db, "get_account", lambda uid: {"id": uid, "plan": "free", "trial_ends_at": None})

    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(app_module.db, "update_account_plan", boom)
    res = client.post("/api/webhooks/revenuecat", json=_event(), headers={"Authorization": WEBHOOK_SECRET})
    assert res.status_code == 500


def test_webhook_transfer_event_without_app_user_id_returns_200(client, monkeypatch):
    """RevenueCat TRANSFER events carry transferred_from/transferred_to but no
    app_user_id. They must no-op with 200, not 400 (a 400 makes RevenueCat retry
    the delivery indefinitely)."""
    def boom(*a, **k):
        raise AssertionError("update_account_plan should not be called for TRANSFER")
    monkeypatch.setattr(app_module.db, "update_account_plan", boom)
    payload = {"event": {"type": "TRANSFER", "transferred_from": ["x"], "transferred_to": ["y"]}}
    res = client.post("/api/webhooks/revenuecat", json=payload, headers={"Authorization": WEBHOOK_SECRET})
    assert res.status_code == 200


def test_webhook_actionable_event_missing_app_user_id_returns_200(client, monkeypatch):
    """An otherwise-actionable event with no app_user_id is ignored with 200
    rather than raising."""
    def boom(*a, **k):
        raise AssertionError("update_account_plan should not be called without an app_user_id")
    monkeypatch.setattr(app_module.db, "update_account_plan", boom)
    payload = {"event": {"type": "INITIAL_PURCHASE", "entitlement_ids": ["pro"], "period_type": "NORMAL"}}
    res = client.post("/api/webhooks/revenuecat", json=payload, headers={"Authorization": WEBHOOK_SECRET})
    assert res.status_code == 200
