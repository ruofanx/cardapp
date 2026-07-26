# RevenueCat IAP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire a real Apple in-app purchase through RevenueCat into PokeCollect's existing (UI-only) paywall, so `accounts.plan` actually flips to `'pro'` on purchase.

**Architecture:** RevenueCat's Capacitor SDK drives the native StoreKit purchase sheet (`appUserID` = our Supabase account UUID, no mapping table needed); a new `POST /api/webhooks/revenuecat` endpoint reads the webhook event fields directly (no extra RevenueCat REST call) and updates `accounts.plan`/`trial_ends_at`. The client also treats a successful purchase as optimistically "pro" immediately, since the webhook lands asynchronously.

**Tech Stack:** FastAPI + psycopg2 (`db_postgres.py`), pytest + `fastapi.testclient.TestClient`, React 18 + Vite, Capacitor 8, `@revenuecat/purchases-capacitor@^13.2.4`.

## Global Constraints

- Bundle ID is `com.pokecollect.app` — already set in the Xcode project, do not change it.
- Backend loads env vars via `load_dotenv(dotenv_path="../.env")` relative to `webapp/` — new env vars go in the repo-root `.env` (gitignored) and a placeholder line in `.env.example` (committed).
- Frontend has flat `src/` structure (no subdirectories besides `screens/`) — new shared modules go directly in `src/`, not `src/components/`.
- No frontend test runner exists in this project — do not introduce one. Frontend verification is `npm run build` (catches syntax/import errors) plus the manual Simulator pass in Task 12.
- Backend tests follow two existing patterns depending on what they touch: pure-logic/route tests use `fastapi.testclient.TestClient` + `monkeypatch` on `app_module.db.*` (see `tests/test_sealed.py:396-433`); DB-layer tests hit the real `DATABASE_URL` with create-then-delete fixtures (see `tests/test_db_postgres.py`).
- Every webhook state transition must be idempotent — re-delivering the same event must produce the same DB state.
- Single subscription tier: $3.99/mo, one RevenueCat entitlement id `"pro"`, one product. No annual tier, no Android — out of scope (see spec §8).
- Two trials coexist by design (see spec §3.1): the signup-time 14-day grace trial (`create_account`, unchanged) and the StoreKit subscription trial (this plan). Both write the same `trial_ends_at` column; the more recent write wins.
- RevenueCat's webhook `Authorization` header carries the *exact* string configured in the RevenueCat dashboard, not a `Bearer `-prefixed token — compare it verbatim against `REVENUECAT_WEBHOOK_SECRET`.

---

### Task 1: `db_postgres.py` — account plan update + delete helpers

**Files:**
- Modify: `webapp/db_postgres.py` (add functions after `create_account`, which ends at line 613)
- Test: `webapp/tests/test_db_postgres.py` (append)

**Interfaces:**
- Produces: `update_account_plan(uid: str, *, plan: str, trial_ends_at: datetime | None) -> dict | None` — returns `{"id", "email", "plan", "trial_ends_at"}` or `None` if no account with that id exists.
- Produces: `delete_account(uid: str) -> bool`

- [ ] **Step 1: Write the failing tests**

Append to `webapp/tests/test_db_postgres.py` (after the existing `test_tags` test, before `test_portfolio_summary`):

```python
@pytest.fixture
def test_account():
    account = db.create_account("_test_account_uid_", "test-account@example.com")
    yield account
    db.delete_account(account["id"])


def test_update_account_plan_to_pro(test_account):
    updated = db.update_account_plan(test_account["id"], plan="pro", trial_ends_at=None)
    assert updated is not None
    assert updated["plan"] == "pro"
    assert updated["trial_ends_at"] is None


def test_update_account_plan_sets_trial(test_account):
    from datetime import datetime, timezone, timedelta
    trial_end = datetime.now(timezone.utc) + timedelta(days=14)
    updated = db.update_account_plan(test_account["id"], plan="pro", trial_ends_at=trial_end)
    assert updated["plan"] == "pro"
    assert updated["trial_ends_at"] is not None


def test_update_account_plan_unknown_account_returns_none():
    result = db.update_account_plan("_nonexistent_uid_", plan="pro", trial_ends_at=None)
    assert result is None


def test_delete_account_removes_row(test_account):
    uid = test_account["id"]
    assert db.delete_account(uid) is True
    assert db.get_account(uid) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd webapp && source .venv/bin/activate && pytest tests/test_db_postgres.py -k "update_account_plan or delete_account_removes" -v`
Expected: FAIL with `AttributeError: module 'db_postgres' has no attribute 'update_account_plan'` (and same for `delete_account`)

- [ ] **Step 3: Implement the functions**

In `webapp/db_postgres.py`, insert immediately after `create_account` (after line 613, before the blank line that follows):

```python

def update_account_plan(uid: str, *, plan: str, trial_ends_at) -> dict | None:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE accounts SET plan = %s, trial_ends_at = %s WHERE id = %s "
            "RETURNING id, email, plan, trial_ends_at",
            (plan, trial_ends_at, uid),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {"id": str(row["id"]), "email": row["email"], "plan": row["plan"], "trial_ends_at": row["trial_ends_at"]}


def delete_account(uid: str) -> bool:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM accounts WHERE id = %s", (uid,))
        return cur.rowcount > 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_db_postgres.py -k "update_account_plan or delete_account_removes" -v`
Expected: PASS (4 tests) — requires `DATABASE_URL` to be set in `.env`; it already is (confirmed in repo).

- [ ] **Step 5: Commit**

```bash
git add webapp/db_postgres.py webapp/tests/test_db_postgres.py
git commit -m "feat: add update_account_plan and delete_account db helpers"
```

---

### Task 2: `app.py` — unify `is_pro` logic into `account_is_pro()`

**Files:**
- Modify: `webapp/app.py:246-284` (add helper, refactor 3 call sites), `webapp/app.py:1597-1612` (third call site)
- Test: `webapp/tests/test_billing_webhook.py` (create — this file will also hold Task 3's webhook tests)

**Interfaces:**
- Produces: `account_is_pro(account: dict) -> bool`
- Consumes: nothing new (pure function of the `account` dict shape already returned by `db.get_account`/`db.create_account`: `{"id", "email", "plan", "trial_ends_at"}`)

- [ ] **Step 1: Write the failing tests**

Create `webapp/tests/test_billing_webhook.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp && source .venv/bin/activate && pytest tests/test_billing_webhook.py -v`
Expected: FAIL with `AttributeError: module 'app' has no attribute 'account_is_pro'`

- [ ] **Step 3: Add the helper and refactor the three call sites**

In `webapp/app.py`, insert immediately after `FREE_SCAN_LIMIT = 20` (line 246):

```python

def account_is_pro(account: dict) -> bool:
    """True if the account is on a paid plan or currently within a trial
    window — either the signup grace trial (set once at account creation)
    or an active StoreKit subscription trial (set by the RevenueCat
    webhook). Both share the same trial_ends_at column."""
    if account["plan"] == "pro":
        return True
    trial_ends_at = account.get("trial_ends_at")
    return trial_ends_at is not None and trial_ends_at > datetime.now(timezone.utc)
```

Replace the `/api/account` body (lines 254-257):

```python
    is_pro = account["plan"] == "pro" or (
        account["trial_ends_at"] is not None
        and account["trial_ends_at"] > datetime.now(timezone.utc)
    )
```

with:

```python
    is_pro = account_is_pro(account)
```

Replace the profile-limit gate (line 275):

```python
    is_paid = account.get("plan") == "pro"
```

with:

```python
    is_paid = account_is_pro(account)
```

Replace the `/api/identify` scan-limit check (lines 1601-1604):

```python
        is_pro = account["plan"] == "pro" or (
            account["trial_ends_at"] is not None
            and account["trial_ends_at"] > datetime.now(timezone.utc)
        )
```

with:

```python
        is_pro = account_is_pro(account)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_billing_webhook.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full existing suite to confirm no regression**

Run: `pytest tests/ -v`
Expected: PASS (all pre-existing tests still pass — this refactor changes behavior only for the profile-limit gate, which now also respects trials as intended)

- [ ] **Step 6: Commit**

```bash
git add webapp/app.py webapp/tests/test_billing_webhook.py
git commit -m "fix: unify is_pro checks into account_is_pro(), fixes profile-gate trial-blindness"
```

---

### Task 3: `app.py` — RevenueCat webhook endpoint

**Files:**
- Modify: `webapp/app.py` (imports line 31, add webhook route + state logic near line 284, after the `/api/profiles` routes)
- Test: `webapp/tests/test_billing_webhook.py` (append)

**Interfaces:**
- Consumes: `db.get_account(uid) -> dict | None`, `db.update_account_plan(uid, *, plan, trial_ends_at) -> dict | None` (Task 1)
- Produces: `POST /api/webhooks/revenuecat` route; `_revenuecat_plan_update(event: dict) -> tuple[str, object] | None` (pure function, importable as `app_module._revenuecat_plan_update` for tests if needed — not required by other tasks)

- [ ] **Step 1: Write the failing tests**

Append to `webapp/tests/test_billing_webhook.py`:

```python
import pytest
from fastapi.testclient import TestClient

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_billing_webhook.py -v`
Expected: FAIL — `404` on the new route tests (route doesn't exist yet), plus a `NameError`/`AttributeError` around `REVENUECAT_WEBHOOK_SECRET` not existing on `app_module`.

- [ ] **Step 3: Implement the route**

In `webapp/app.py`, add `Header` to the existing fastapi import (line 31):

```python
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, UploadFile, File, Form
```

Insert after the `/api/profiles` POST route (after line 283, i.e. right after `return db.create_profile(account["id"], name, color)`):

```python

REVENUECAT_WEBHOOK_SECRET = os.environ.get("REVENUECAT_WEBHOOK_SECRET", "")

_RC_ACTIVE_EVENT_TYPES = {"INITIAL_PURCHASE", "RENEWAL", "PRODUCT_CHANGE", "UNCANCELLATION"}


def _revenuecat_plan_update(event: dict) -> tuple[str, object] | None:
    """Return (plan, trial_ends_at) to write for this webhook event, or None
    to no-op. CANCELLATION/BILLING_ISSUE/unrecognized types are no-ops by
    design (see spec §3.3) — access is only revoked on EXPIRATION."""
    event_type = event.get("type")
    entitlement_ids = event.get("entitlement_ids") or []

    if event_type == "EXPIRATION":
        return ("free", None)

    if event_type in _RC_ACTIVE_EVENT_TYPES and "pro" in entitlement_ids:
        expiration_ms = event.get("expiration_at_ms")
        if event.get("period_type") == "TRIAL" and expiration_ms:
            trial_ends_at = datetime.fromtimestamp(expiration_ms / 1000, tz=timezone.utc)
        else:
            trial_ends_at = None
        return ("pro", trial_ends_at)

    return None


@app.post("/api/webhooks/revenuecat")
async def revenuecat_webhook(payload: dict, authorization: str | None = Header(default=None)):
    if not REVENUECAT_WEBHOOK_SECRET or authorization != REVENUECAT_WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    event = payload.get("event")
    if not isinstance(event, dict) or "type" not in event or "app_user_id" not in event:
        raise HTTPException(status_code=400, detail="Malformed webhook payload")

    update = _revenuecat_plan_update(event)
    if update is None:
        return {"ok": True}

    plan, trial_ends_at = update
    account_id = event["app_user_id"]
    if not db.get_account(account_id):
        log.warning("revenuecat webhook: unknown account_id %r, ignoring", account_id)
        return {"ok": True}

    try:
        db.update_account_plan(account_id, plan=plan, trial_ends_at=trial_ends_at)
    except Exception:
        log.exception("revenuecat webhook: failed to update account %r", account_id)
        raise HTTPException(status_code=500, detail="Failed to update account")

    return {"ok": True}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_billing_webhook.py -v`
Expected: PASS (all tests from Task 2 and Task 3)

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -v`
Expected: PASS

- [ ] **Step 6: Add the env var placeholder**

In `.env.example` (repo root), add a line in the Supabase section:

```
SUPABASE_JWT_SECRET=your-jwt-secret-here
REVENUECAT_WEBHOOK_SECRET=your-revenuecat-webhook-auth-header-value
```

(The second line is new — insert it directly after the existing `SUPABASE_JWT_SECRET` line.)

- [ ] **Step 7: Commit**

```bash
git add webapp/app.py webapp/tests/test_billing_webhook.py .env.example
git commit -m "feat: add POST /api/webhooks/revenuecat, drives accounts.plan from subscription events"
```

---

### Task 4: One-time — grandfather existing family accounts

**Files:** none (operational step, no permanent script per spec §3.4)

**Interfaces:** none

- [ ] **Step 1: Find the family's account ids**

Run (from `webapp/`, with `.venv` active):

```bash
python3 -c "
import db_postgres as db
with db.connect() as conn:
    cur = conn.cursor()
    cur.execute(\"SELECT id, email, plan FROM accounts\")
    for row in cur.fetchall():
        print(row['id'], row['email'], row['plan'])
"
```

Identify which rows correspond to Ro/Reid/Ryan's real sign-in emails (the implementer knows these; not written here since they're personal data, not something this plan should hardcode).

- [ ] **Step 2: Grandfather those accounts**

Run the same pattern with an `UPDATE`, substituting the real email addresses found in Step 1:

```bash
python3 -c "
import db_postgres as db
emails = ['REPLACE_WITH_RO_EMAIL', 'REPLACE_WITH_REID_EMAIL', 'REPLACE_WITH_RYAN_EMAIL']
with db.connect() as conn:
    cur = conn.cursor()
    cur.execute('UPDATE accounts SET plan = %s WHERE email = ANY(%s) RETURNING id, email, plan', ('pro', emails))
    for row in cur.fetchall():
        print('grandfathered:', row['id'], row['email'], row['plan'])
"
```

- [ ] **Step 3: Verify**

Re-run the Step 1 query and confirm those three rows now show `plan = pro`.

No commit for this task — it's a one-time data change, not a code change.

---

### Task 5: Frontend — RevenueCat SDK wrapper

**Files:**
- Modify: `webapp/frontend/package.json` (add dependency)
- Create: `webapp/frontend/src/purchases.js`

**Interfaces:**
- Produces: `configurePurchases(accountId: string): Promise<void>`, `logOutPurchases(): Promise<void>`, `getProPackage(): Promise<PurchasesPackage | null>`, `isEligibleForTrial(pkg): Promise<boolean>`, `purchasePackage(pkg): Promise<CustomerInfo>`, `purchaseWasCancelled(err): boolean`, `purchaseIsPending(err): boolean`, `restorePurchases(): Promise<CustomerInfo>`, `customerInfoIsPro(customerInfo): boolean`

- [ ] **Step 1: Add the dependency**

```bash
cd webapp/frontend && npm install @revenuecat/purchases-capacitor@^13.2.4
```

- [ ] **Step 2: Write the wrapper module**

Create `webapp/frontend/src/purchases.js`:

```js
import { Purchases, LOG_LEVEL, PURCHASES_ERROR_CODE } from '@revenuecat/purchases-capacitor'

// Public (safe to embed client-side) RevenueCat SDK key for the iOS app.
// Placeholder until the RevenueCat project exists — see
// docs/superpowers/specs/2026-07-19-revenuecat-iap-design.md §7.
const REVENUECAT_PUBLIC_SDK_KEY = 'appl_REPLACE_ME'
const ENTITLEMENT_ID = 'pro'
const INTRO_ELIGIBILITY_STATUS_ELIGIBLE = 2

let configured = false

export async function configurePurchases(accountId) {
  if (configured || !accountId) return
  await Purchases.setLogLevel({ level: LOG_LEVEL.INFO })
  await Purchases.configure({ apiKey: REVENUECAT_PUBLIC_SDK_KEY, appUserID: accountId })
  configured = true
}

export async function logOutPurchases() {
  if (!configured) return
  try { await Purchases.logOut() } catch { /* already logged out / never configured */ }
  configured = false
}

export async function getProPackage() {
  const offerings = await Purchases.getOfferings()
  return offerings.current?.availablePackages?.[0] || null
}

export async function isEligibleForTrial(pkg) {
  if (!pkg?.product?.identifier) return false
  const result = await Purchases.checkTrialOrIntroductoryPriceEligibility({
    productIdentifiers: [pkg.product.identifier],
  })
  return result?.[pkg.product.identifier]?.status === INTRO_ELIGIBILITY_STATUS_ELIGIBLE
}

export async function purchasePackage(pkg) {
  const { customerInfo } = await Purchases.purchasePackage({ aPackage: pkg })
  return customerInfo
}

export function purchaseWasCancelled(err) {
  return err?.code === PURCHASES_ERROR_CODE.PURCHASE_CANCELLED_ERROR
}

export function purchaseIsPending(err) {
  return err?.code === PURCHASES_ERROR_CODE.PAYMENT_PENDING_ERROR
}

export async function restorePurchases() {
  const { customerInfo } = await Purchases.restorePurchases()
  return customerInfo
}

export function customerInfoIsPro(customerInfo) {
  return Boolean(customerInfo?.entitlements?.active?.[ENTITLEMENT_ID])
}
```

- [ ] **Step 3: Verify the build**

```bash
cd webapp/frontend && npm run build
```

Expected: build succeeds (no syntax/import errors). It's fine that `Purchases.configure()` will fail at runtime with the placeholder API key until Task 11's setup — this step only checks the module is syntactically valid and importable.

- [ ] **Step 4: Commit**

```bash
git add webapp/frontend/package.json webapp/frontend/package-lock.json webapp/frontend/src/purchases.js
git commit -m "feat: add RevenueCat Capacitor SDK wrapper (purchases.js)"
```

---

### Task 6: Frontend — shared `PaywallSheet` component

**Files:**
- Create: `webapp/frontend/src/PaywallSheet.jsx`

**Interfaces:**
- Consumes (Task 5): `getProPackage`, `isEligibleForTrial`, `purchasePackage`, `purchaseWasCancelled`, `purchaseIsPending`, `customerInfoIsPro`
- Produces: `export default function PaywallSheet({ reason = 'general', onClose, onUpgraded })` — `reason` is `'profiles' | 'scans' | 'general'` and only changes the subcopy line; `onClose()` fires on dismiss without purchase; `onUpgraded(customerInfo)` fires after a purchase that confirms the `pro` entitlement is active.

- [ ] **Step 1: Write the component**

Create `webapp/frontend/src/PaywallSheet.jsx`:

```jsx
import { useEffect, useState } from 'react'
import { Icon, NavBar, NavBackButton } from './components.jsx'
import { getProPackage, isEligibleForTrial, purchasePackage, purchaseWasCancelled, purchaseIsPending, customerInfoIsPro } from './purchases.js'

const REASON_COPY = {
  profiles: 'The free plan includes 1 profile. Upgrade to add unlimited profiles for everyone in your family.',
  scans: "You've hit this month's free scan limit. Upgrade for unlimited card scans.",
  general: 'Upgrade for unlimited profiles, unlimited scans, and full price history.',
}

const FEATURES = [
  ['Unlimited profiles', 'Add all your kids with their own collections'],
  ['Unlimited scans', 'No monthly limit on card identification'],
  ['Full price history', 'Charts and trend data for every card'],
  ['Price alerts', 'Get notified when a card hits your target'],
]

export default function PaywallSheet({ reason = 'general', onClose, onUpgraded }) {
  const [pkg, setPkg] = useState(null)
  const [eligibleForTrial, setEligibleForTrial] = useState(false)
  const [status, setStatus] = useState('loading') // loading | ready | purchasing | pending | error
  const [errorMsg, setErrorMsg] = useState('')

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const p = await getProPackage()
        if (cancelled) return
        setPkg(p)
        if (p) setEligibleForTrial(await isEligibleForTrial(p))
        setStatus('ready')
      } catch (e) {
        if (cancelled) return
        setErrorMsg(String(e.message || e))
        setStatus('error')
      }
    })()
    return () => { cancelled = true }
  }, [])

  async function handleUpgrade() {
    if (!pkg || status === 'purchasing') return
    setStatus('purchasing')
    setErrorMsg('')
    try {
      const customerInfo = await purchasePackage(pkg)
      if (customerInfoIsPro(customerInfo)) {
        onUpgraded?.(customerInfo)
      } else {
        setStatus('ready')
      }
    } catch (e) {
      if (purchaseWasCancelled(e)) {
        setStatus('ready')
        return
      }
      if (purchaseIsPending(e)) {
        setStatus('pending')
        return
      }
      setErrorMsg(String(e.message || e))
      setStatus('error')
    }
  }

  const priceLine = pkg
    ? (eligibleForTrial ? `${pkg.product.priceString}/mo · 14-day free trial` : `${pkg.product.priceString}/mo`)
    : '$3.99/mo'

  return (
    <div style={{ position: 'absolute', inset: 0, background: 'var(--bg)', display: 'flex', flexDirection: 'column' }}>
      <NavBar title="" left={<NavBackButton onClick={onClose} label="Back" />} />
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '24px 28px', gap: 0 }}>
        <div className="foil" style={{ width: 72, height: 72, borderRadius: 20, animation: 'foilRot 18s linear infinite', marginBottom: 24 }} />
        <div style={{ fontSize: 26, fontWeight: 700, letterSpacing: '-0.02em', marginBottom: 8, textAlign: 'center' }}>
          Upgrade to Family/Pro
        </div>
        <div style={{ fontSize: 15, color: 'var(--ink-3)', textAlign: 'center', lineHeight: 1.55, marginBottom: 32 }}>
          {REASON_COPY[reason] || REASON_COPY.general}
        </div>

        <div style={{ width: '100%', display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 24 }}>
          {FEATURES.map(([title, desc]) => (
            <div key={title} className="row gap-3" style={{ padding: '12px 14px', background: 'var(--bg-1)', borderRadius: 12 }}>
              <Icon name="check" size={18} style={{ color: 'var(--accent)', flexShrink: 0, marginTop: 1 }} />
              <div>
                <div style={{ fontSize: 14, fontWeight: 600 }}>{title}</div>
                <div style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: 1 }}>{desc}</div>
              </div>
            </div>
          ))}
        </div>

        {status === 'error' && (
          <div style={{ width: '100%', color: 'var(--neg)', fontSize: 13, padding: '8px 12px', background: 'oklch(0.35 0.10 30 / 0.15)', borderRadius: 8, marginBottom: 16 }}>
            {errorMsg || 'Something went wrong. Please try again.'}
          </div>
        )}
        {status === 'pending' && (
          <div style={{ width: '100%', color: 'var(--ink-2)', fontSize: 13, padding: '8px 12px', background: 'var(--bg-1)', borderRadius: 8, marginBottom: 16, textAlign: 'center' }}>
            Waiting for approval (Ask to Buy) — this screen will update once it's approved.
          </div>
        )}

        <button
          onClick={handleUpgrade}
          disabled={status === 'loading' || status === 'purchasing' || status === 'pending' || !pkg}
          style={{
            width: '100%', padding: '15px', borderRadius: 14,
            background: 'var(--accent)', color: 'var(--accent-ink)',
            fontWeight: 700, fontSize: 17, border: 'none',
            cursor: (status === 'ready' && pkg) ? 'pointer' : 'not-allowed',
            opacity: (status === 'loading' || status === 'purchasing') ? 0.7 : 1,
          }}>
          {status === 'purchasing' ? 'Processing…' : `Upgrade · ${priceLine}`}
        </button>
        <div style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: 10, textAlign: 'center' }}>
          Cancel anytime
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Verify the build**

```bash
cd webapp/frontend && npm run build
```

Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add webapp/frontend/src/PaywallSheet.jsx
git commit -m "feat: shared PaywallSheet component with real RevenueCat purchase flow"
```

---

### Task 7: Frontend — wire `AddProfile.jsx` to the shared paywall

**Files:**
- Modify: `webapp/frontend/src/screens/AddProfile.jsx`

**Interfaces:**
- Consumes (Task 6): `PaywallSheet` default export, props `{ reason, onClose, onUpgraded }`

- [ ] **Step 1: Replace the inline `PaywallSheet` with the shared one**

In `webapp/frontend/src/screens/AddProfile.jsx`:

Replace the import line:

```jsx
import { Icon, NavBar, NavBackButton } from '../components.jsx'
```

with:

```jsx
import { Icon, NavBar, NavBackButton } from '../components.jsx'
import PaywallSheet from '../PaywallSheet.jsx'
```

Replace the paywall-trigger line:

```jsx
  if (showPaywall) {
    return <PaywallSheet onClose={() => setShowPaywall(false)} onUpgrade={() => navigate('settings')} />
  }
```

with:

```jsx
  if (showPaywall) {
    return <PaywallSheet reason="profiles" onClose={() => setShowPaywall(false)} onUpgraded={() => setShowPaywall(false)} />
  }
```

Delete the inline `function PaywallSheet({ onClose, onUpgrade }) { ... }` definition entirely (the whole block starting at `function PaywallSheet({ onClose, onUpgrade }) {` through its closing `}` — this was the last top-level function in the file).

- [ ] **Step 2: Verify the build**

```bash
cd webapp/frontend && npm run build
```

Expected: build succeeds. Grep-check there's no leftover duplicate definition:

```bash
grep -n "function PaywallSheet" webapp/frontend/src/screens/AddProfile.jsx
```

Expected: no output (the only definition now lives in `src/PaywallSheet.jsx`).

- [ ] **Step 3: Commit**

```bash
git add webapp/frontend/src/screens/AddProfile.jsx
git commit -m "feat: wire AddProfile's 402 paywall to the real purchase flow"
```

---

### Task 8: Frontend — wire Settings "Upgrade" pill + "Restore Purchases"

**Files:**
- Modify: `webapp/frontend/src/screens/SettingsAndOnboarding.jsx`

**Interfaces:**
- Consumes (Task 6): `PaywallSheet`
- Consumes (Task 5): `restorePurchases`, `customerInfoIsPro`

- [ ] **Step 1: Add imports and state**

Replace the import block at the top of `SettingsAndOnboarding.jsx`:

```jsx
import React, { useState, useEffect } from 'react'
import api from '../api.js'
import { NavBar, Icon, CardArt } from '../components.jsx'
import { CARDS } from '../data.js'
```

with:

```jsx
import React, { useState, useEffect } from 'react'
import api from '../api.js'
import { NavBar, Icon, CardArt } from '../components.jsx'
import { CARDS } from '../data.js'
import PaywallSheet from '../PaywallSheet.jsx'
import { restorePurchases, customerInfoIsPro } from '../purchases.js'
```

Add state right after the existing `const [account, setAccount] = useState(null);` line:

```jsx
  const [account, setAccount] = useState(null);
  const [showPaywall, setShowPaywall] = useState(false);
  const [restoreStatus, setRestoreStatus] = useState(null); // null | 'checking' | 'restored' | 'nothing' | 'error'
```

- [ ] **Step 2: Wire the Upgrade pill and add an early return for the paywall**

Locate the Upgrade pill:

```jsx
                    {!isPro && (
                      <div style={{
                        padding: '5px 12px', borderRadius: 999,
                        background: 'var(--accent)', color: 'var(--accent-ink)',
                        fontSize: 11, fontWeight: 700,
                      }}>Upgrade</div>
                    )}
```

Replace with (turns the `div` into a `button` and wires the click):

```jsx
                    {!isPro && (
                      <button className="tap" onClick={() => setShowPaywall(true)} style={{
                        padding: '5px 12px', borderRadius: 999, border: 'none',
                        background: 'var(--accent)', color: 'var(--accent-ink)',
                        fontSize: 11, fontWeight: 700, cursor: 'pointer',
                      }}>Upgrade</button>
                    )}
```

Add an early return for the paywall right before the component's main `return (` statement (i.e. as the first line after all the `useState`/`useEffect`/handler declarations, before `return (`):

```jsx
  if (showPaywall) {
    return (
      <PaywallSheet
        reason="general"
        onClose={() => setShowPaywall(false)}
        onUpgraded={() => { setShowPaywall(false); api.getAccount().then(a => setAccount(a)).catch(() => {}); }}
      />
    );
  }
```

- [ ] **Step 3: Add a "Restore Purchases" row**

Find the sign-out button (`onClick={onSignOut}`, referenced at line ~244 in the original file) and add a Restore Purchases row directly above it, following the existing row style used elsewhere in the file (a `button className="tap row gap-1"` pattern):

```jsx
        <div style={{ padding: '0 16px 16px' }}>
          <button className="tap row gap-1" onClick={async () => {
            setRestoreStatus('checking');
            try {
              const info = await restorePurchases();
              setRestoreStatus(customerInfoIsPro(info) ? 'restored' : 'nothing');
              if (customerInfoIsPro(info)) {
                api.getAccount().then(a => setAccount(a)).catch(() => {});
              }
            } catch {
              setRestoreStatus('error');
            }
          }} style={{ fontSize: 13, color: 'var(--ink-3)', padding: '10px 0' }}>
            <Icon name="refresh-cw" size={14} />
            {restoreStatus === 'checking' ? 'Checking…'
              : restoreStatus === 'restored' ? 'Purchases restored'
              : restoreStatus === 'nothing' ? 'No purchases found'
              : restoreStatus === 'error' ? 'Restore failed — try again'
              : 'Restore Purchases'}
          </button>
        </div>
```

(If `Icon` doesn't have a `refresh-cw` icon registered, check `components.jsx`'s `Icon` name list — check with `grep -n "'refresh" webapp/frontend/src/components.jsx` before this step and substitute an existing icon name, e.g. one already used by `handleRefreshAll` elsewhere in this same file, if `refresh-cw` isn't present.)

- [ ] **Step 4: Verify the build**

```bash
cd webapp/frontend && npm run build
```

Expected: build succeeds.

- [ ] **Step 5: Commit**

```bash
git add webapp/frontend/src/screens/SettingsAndOnboarding.jsx
git commit -m "feat: wire Settings Upgrade pill and Restore Purchases to real IAP flow"
```

---

### Task 9: Frontend — Scan's 429 scan-limit → paywall

**Files:**
- Modify: `webapp/frontend/src/screens/Scan.jsx`

**Interfaces:**
- Consumes (Task 6): `PaywallSheet`

- [ ] **Step 1: Add import and state**

Replace:

```jsx
import React, { useState, useRef, useEffect } from 'react'
import api from '../api.js'
import { CardArt, Icon, Price } from '../components.jsx'
```

with:

```jsx
import React, { useState, useRef, useEffect } from 'react'
import api from '../api.js'
import { CardArt, Icon, Price } from '../components.jsx'
import PaywallSheet from '../PaywallSheet.jsx'
```

Add state next to the other `useState` declarations in `ScanScreen` (right after `const [scanUsage, setScanUsage] = useState(null);`):

```jsx
  const [scanUsage, setScanUsage] = useState(null); // { used, limit } or null
  const [showPaywall, setShowPaywall] = useState(false);
```

- [ ] **Step 2: Catch the 429 in `runIdentify`'s error handler**

Replace:

```jsx
    } catch (e) {
      log('Identify failed', String(e.message || e).slice(0, 80), 'miss');
      setError(String(e.message || e));
      setPhase('idle');
    }
  };
```

with:

```jsx
    } catch (e) {
      if (e.status === 429) {
        setPhase('idle');
        setShowPaywall(true);
        return;
      }
      log('Identify failed', String(e.message || e).slice(0, 80), 'miss');
      setError(String(e.message || e));
      setPhase('idle');
    }
  };
```

- [ ] **Step 3: Render the paywall**

Add an early return inside `ScanScreen`, right before its main `return (` statement:

```jsx
  if (showPaywall) {
    return (
      <PaywallSheet
        reason="scans"
        onClose={() => setShowPaywall(false)}
        onUpgraded={() => {
          setShowPaywall(false);
          api.getAccount().then(acct => {
            if (acct?.scan_limit != null) setScanUsage({ used: acct.scan_used ?? 0, limit: acct.scan_limit });
            else setScanUsage(null);
          }).catch(() => {});
        }}
      />
    );
  }
```

- [ ] **Step 4: Verify the build**

```bash
cd webapp/frontend && npm run build
```

Expected: build succeeds.

- [ ] **Step 5: Commit**

```bash
git add webapp/frontend/src/screens/Scan.jsx
git commit -m "feat: show upgrade paywall when the monthly scan limit is hit"
```

---

### Task 10: Frontend — configure RevenueCat on auth, log out on sign-out

**Files:**
- Modify: `webapp/frontend/src/app.jsx:220-243`

**Interfaces:**
- Consumes (Task 5): `configurePurchases(accountId)`, `logOutPurchases()`

- [ ] **Step 1: Import the wrapper**

Replace:

```jsx
import { useState, useEffect, useCallback } from 'react'
import api, { setAuthToken, setCurrentProfileId } from './api.js'
import { supabase } from './supabase.js'
```

with:

```jsx
import { useState, useEffect, useCallback } from 'react'
import api, { setAuthToken, setCurrentProfileId } from './api.js'
import { supabase } from './supabase.js'
import { configurePurchases, logOutPurchases } from './purchases.js'
```

- [ ] **Step 2: Call `configurePurchases` once the Supabase session resolves**

Replace:

```jsx
  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      if (data.session) {
        setAuthToken(data.session.access_token)
        setAuthed(true)
      }
    })
    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, session) => {
      setAuthToken(session?.access_token || null)
      setAuthed(!!session)
    })
    return () => subscription.unsubscribe()
  }, [])
```

with:

```jsx
  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      if (data.session) {
        setAuthToken(data.session.access_token)
        setAuthed(true)
        configurePurchases(data.session.user.id)
      }
    })
    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, session) => {
      setAuthToken(session?.access_token || null)
      setAuthed(!!session)
      if (session) configurePurchases(session.user.id)
    })
    return () => subscription.unsubscribe()
  }, [])
```

- [ ] **Step 3: Log out of RevenueCat on sign-out**

Replace:

```jsx
  async function handleSignOut() {
    await supabase.auth.signOut()
    setAuthToken(null)
    setAuthed(false)
  }
```

with:

```jsx
  async function handleSignOut() {
    await logOutPurchases()
    await supabase.auth.signOut()
    setAuthToken(null)
    setAuthed(false)
  }
```

- [ ] **Step 4: Verify the build**

```bash
cd webapp/frontend && npm run build
```

Expected: build succeeds.

- [ ] **Step 5: Commit**

```bash
git add webapp/frontend/src/app.jsx
git commit -m "feat: configure RevenueCat identity on auth, log out on sign-out"
```

---

### Task 11: iOS — sync Capacitor plugin, StoreKit Configuration file for local testing

**Files:**
- Modify: `webapp/frontend/ios/App/App.xcodeproj/project.pbxproj` (via `cap sync`, not hand-edited)
- Create: `webapp/frontend/ios/App/App/Configuration.storekit`

**Interfaces:** none (native/tooling task, no JS/Python interface)

- [ ] **Step 1: Sync the plugin into the Xcode project**

```bash
cd webapp/frontend && npx cap sync ios
```

Expected: completes without error and reports the RevenueCat plugin found. This project has no `Podfile` (Swift Package Manager only) — the plugin ships both a podspec and a `Package.swift` (confirmed by inspecting the published package), so `cap sync` should add it as an SPM package dependency rather than requiring a CocoaPods install. If `cap sync` reports it needs CocoaPods, install it first with `sudo gem install cocoapods` (requires the machine owner's password — do this interactively, don't script it) and re-run.

- [ ] **Step 2: Enable the In-App Purchase capability**

Open the project: `open webapp/frontend/ios/App/App.xcworkspace` (or `.xcodeproj` if no workspace was generated). In Xcode: select the `App` target → **Signing & Capabilities** → **+ Capability** → add **In-App Purchase**.

- [ ] **Step 3: Create a local StoreKit Configuration file**

Create `webapp/frontend/ios/App/App/Configuration.storekit`:

```json
{
  "identifier": "68F1A2C0-0000-0000-0000-000000000001",
  "nonRenewingSubscriptions": [],
  "products": [],
  "settings": {
    "_askToBuyEnabled": false,
    "_billingIssuesEnabled": false,
    "_disableDialogs": false,
    "_failTransactionsEnabled": false,
    "_locale": "en_US",
    "_storefront": "USA",
    "_storeKitErrors": []
  },
  "subscriptionGroups": [
    {
      "id": "21000000000001",
      "localizations": [],
      "name": "PokeCollect Pro",
      "subscriptions": [
        {
          "adHocOffers": [],
          "codeOffers": [],
          "displayPrice": "3.99",
          "familyShareable": true,
          "groupNumber": 1,
          "internalID": "21000000000002",
          "introductoryOffer": {
            "internalID": "21000000000003",
            "paymentMode": "free",
            "subscriptionPeriod": "P2W"
          },
          "localizations": [
            {
              "description": "Unlimited profiles, unlimited scans, full price history.",
              "displayName": "PokeCollect Pro Monthly",
              "locale": "en_US"
            }
          ],
          "productID": "com.pokecollect.app.pro.monthly",
          "recurringSubscriptionPeriod": "P1M",
          "referenceName": "PokeCollect Pro Monthly",
          "subscriptionGroupID": "21000000000001",
          "type": "RecurringSubscription"
        }
      ]
    }
  ],
  "version": {
    "major": 3,
    "minor": 0
  }
}
```

Open this file in Xcode (double-click it in the Project Navigator) — Xcode will render it in its StoreKit Configuration editor, where any schema mismatch for the current Xcode version will show as an inline validation error and can be fixed visually without hand-editing JSON.

- [ ] **Step 4: Wire the configuration into the run scheme**

In Xcode: **Product** → **Scheme** → **Edit Scheme** → **Run** → **Options** tab → **StoreKit Configuration** → select `Configuration.storekit`.

- [ ] **Step 5: Update `purchases.js`'s placeholder key with a note**

No code change needed yet — `REVENUECAT_PUBLIC_SDK_KEY` in `webapp/frontend/src/purchases.js` stays `'appl_REPLACE_ME'` until the RevenueCat project exists (spec §7). Local StoreKit Configuration testing exercises the purchase *UI* even with `Purchases.configure()` failing, since `getProPackage()`/`purchasePackage()` will reject — Task 12 covers this explicitly.

- [ ] **Step 6: Commit**

```bash
cd /Users/ruofanxu/claude/CardApp
git add webapp/frontend/ios webapp/frontend/package.json webapp/frontend/package-lock.json
git commit -m "chore: sync RevenueCat Capacitor plugin, add local StoreKit Configuration for testing"
```

---

### Task 12: Manual verification pass

**Files:** none

**Interfaces:** none

This task has no automated steps — it's the manual Simulator walkthrough called for in spec §6, plus a checklist for once the manual setup in spec §7 is done.

- [ ] **Step 1: Simulator walkthrough (available now, no Apple Developer account needed)**

Build and run the app in the iOS Simulator with the `Configuration.storekit` file selected (Task 11, Step 4). Walk through:
- Trigger the AddProfile paywall (add a 2nd profile on a free account) → paywall shows → tap Upgrade → local StoreKit purchase sheet appears → complete the purchase → paywall closes.
- Trigger the Settings "Upgrade" pill directly → same flow.
- Trigger the Scan 429 path (either lower `FREE_SCAN_LIMIT` temporarily in `app.py` for this test, or make 20 real identify calls) → paywall shows with the "scans" copy.
- Tap "Restore Purchases" in Settings after a local purchase → confirms restored.
- Cancel out of the StoreKit sheet mid-purchase → paywall returns to its normal (non-error) state, no crash.

Note: since `REVENUECAT_PUBLIC_SDK_KEY` is still the placeholder, `Purchases.configure()` will not have valid credentials yet — this step is blocked until Task 12 Step 2's manual setup is done, *or* can be partially exercised by temporarily configuring against a RevenueCat sandbox project if one is stood up early. Document which you did.

- [ ] **Step 2: Once Apple Developer Program + App Store Connect + RevenueCat exist (spec §7)**

- Replace `REVENUECAT_PUBLIC_SDK_KEY` in `purchases.js` with the real public SDK key.
- Set `REVENUECAT_WEBHOOK_SECRET` in `.env` (local) and in Railway's environment variables, matching the value configured in the RevenueCat webhook dashboard.
- Point the RevenueCat webhook URL at `https://cardapp-production-569d.up.railway.app/api/webhooks/revenuecat`.
- Using a sandbox Apple ID tester account, run the full flow on a real device or TestFlight build and confirm: purchase completes → RevenueCat dashboard shows the subscriber active → webhook fires → `accounts.plan` flips to `'pro'` in the database (check via the Task 4 query pattern) → Settings shows "Pro Trial" with the correct days-left.
- Cancel the sandbox subscription and confirm the later `EXPIRATION` event flips `plan` back to `'free'`.

No commit for this task.
