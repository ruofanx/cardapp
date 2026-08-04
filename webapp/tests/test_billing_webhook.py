from datetime import datetime, timezone, timedelta

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
