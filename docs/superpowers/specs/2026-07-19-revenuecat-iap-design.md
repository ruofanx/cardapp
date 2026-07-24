# RevenueCat IAP — Design

**Date:** 2026-07-19 (revised 2026-07-24)
**Status:** Approved
**Parent:** [2026-06-27-pokecollect-mobile-launch-design.md](2026-06-27-pokecollect-mobile-launch-design.md) — this fills in step 5 of the Launch Sequence ("RevenueCat integration (IAP + subscription)").
**Goal:** Make the existing "Upgrade to Family/Pro" paywall actually work. Today it's UI-only — tapping Upgrade just navigates to Settings, and nothing ever sets `accounts.plan = 'pro'`. This spec wires a real Apple in-app purchase through RevenueCat, ending at `accounts.plan` flipping automatically.

---

## 1. Current state (confirmed against code, 2026-07-24)

- `accounts` table already has `plan TEXT DEFAULT 'free'` and `trial_ends_at TIMESTAMPTZ` (`db_postgres.py`).
- **Correction from the 2026-07-19 pass:** `trial_ends_at` is *not* dead. `create_account()` (`db_postgres.py:597-613`, added in `ccb97a2`) already sets it to `now + 14 days` unconditionally on every signup — every new account gets a 14-day app-granted trial with no purchase involved. **Decision: keep this alongside the new StoreKit trial** (see §3.1) rather than removing it — new users get a no-payment-method grace period, and Apple's own intro-offer trial covers the actual subscription later. This means a user can legitimately get up to 28 free days total (14 at signup + 14 more if they subscribe before or shortly after the signup trial lapses and are still StoreKit-eligible) — accepted as intentional generosity, not a bug.
- `is_pro` (`plan == 'pro' or trial_ends_at > now`) is computed inline in three places in `app.py` (`/api/account`, the profile-limit gate, `/api/identify`) — **but inconsistently**: the profile-limit gate (`app.py:275`) checks `account.get("plan") == "pro"` directly and ignores `trial_ends_at`, so a brand-new account in its signup trial currently gets unlimited scans but is *still capped at 1 profile*. Unifying onto one helper (§3) fixes this as a side effect.
- Paywall gates that exist and work: `POST /api/profiles` → `402` past `FREE_PROFILE_LIMIT = 1`; `POST /api/identify` → `429 scan_limit_reached` past the monthly scan limit.
- Paywall UI: `PaywallSheet` is defined inline in `webapp/frontend/src/screens/AddProfile.jsx`, shown only when the 402 is caught. Its "Upgrade · $3.99/mo · 14-day free trial" button calls `onUpgrade` → `navigate('settings')`. No purchase happens.
- Settings (`SettingsAndOnboarding.jsx`) already renders a Plan card ("Free" / "Pro" / "Pro Trial" with days-left) reading `account.is_pro` / `account.trial_ends_at` — but its "Upgrade" pill has no `onClick`.
- The 429 scan-limit response has **no** frontend handling at all today (confirmed via grep — `Scan.jsx` doesn't reference `scan_limit_reached`).
- No billing code of any kind exists yet (Stripe, RevenueCat, StoreKit) — clean slate.
- iOS bundle ID: `com.pokecollect.app`. Capacitor scaffold uses Swift Package Manager (no `Podfile`).
- Not yet done: Apple Developer Program enrollment, App Store Connect app/product, RevenueCat project. These block real end-to-end testing but not writing the code.

---

## 2. Architecture

```
App (Capacitor)                RevenueCat                 FastAPI backend
─────────────────              ──────────                 ────────────────
Purchases.configure(
  appUserID = account.id)  ──▶  ties subscriber
                                 identity to our
                                 account UUID
                                 (no mapping table
                                 needed)

tap Upgrade
 → purchasePackage()      ──▶  StoreKit purchase
                                 sheet (14-day free
                                 trial intro offer)
                                       │
                          CustomerInfo returned to app
                          (optimistic "Pro" in UI)      ──webhook──▶  POST /api/webhooks/revenuecat
                                                                       verifies shared secret,
                                                                       parses event, updates
                                                                       accounts.plan / trial_ends_at
```

Identity: RevenueCat's `app_user_id` is set to our own Supabase account UUID at configure-time, so the webhook payload's `app_user_id` maps directly to `accounts.id` — no separate RevenueCat-subscriber-id column or lookup table.

---

## 3. Backend changes

### 3.1 Trial model (both trials coexist)
Two independent things can make `account_is_pro()` true, and both write the same `trial_ends_at` column:
1. **Signup trial** — `create_account()` already sets `trial_ends_at = now + 14 days` at account creation. Unchanged by this spec.
2. **StoreKit subscription trial** — the webhook (§3.3) sets `trial_ends_at` to the subscription's own trial expiration when `period_type == 'TRIAL'`, which **overwrites** whatever was there before (including a still-active or already-expired signup trial). That's correct: the column always means "when does the current pro grace period end, whichever kind it is."

### 3.2 `webapp/app.py`
- `account_is_pro(account: dict) -> bool` — extracts the `plan=='pro' or trial_ends_at>now` check currently duplicated in three places into one helper; all three call sites switch to use it, **including the profile-limit gate at `app.py:275`**, which fixes the trial-blindness bug noted in §1.
- `POST /api/webhooks/revenuecat` (no `get_current_account` dependency — this is server-to-server, authenticated by shared secret instead):
  - Reject with `401` if the `Authorization` header doesn't match `REVENUECAT_WEBHOOK_SECRET`.
  - Reject with `400` if the payload doesn't parse into the expected shape.
  - Look up `accounts` row by `event.app_user_id` (== `accounts.id`); if it doesn't exist, log and return `200` (nothing to do — shouldn't happen since we always configure with our own id, but not worth a retry storm if it ever does).
  - Apply the state transition below, `UPDATE accounts SET plan=..., trial_ends_at=...`. On DB failure, return `500` so RevenueCat retries — we want eventual consistency, not a dropped update.
  - Otherwise return `200`.

### 3.3 State transition (from the webhook event's `type`, `entitlement_ids`, `period_type`, `expiration_at_ms` — no extra RevenueCat API call needed, since we only ever have one entitlement/product)

| Event type | Effect |
|---|---|
| `INITIAL_PURCHASE`, `RENEWAL`, `PRODUCT_CHANGE`, `UNCANCELLATION` (with `"pro"` in `entitlement_ids`) | `plan='pro'`; `trial_ends_at` = event's expiration if `period_type=='TRIAL'`, else `NULL` |
| `EXPIRATION` | `plan='free'`, `trial_ends_at=NULL` — this is the only event that revokes access |
| `CANCELLATION` | No-op. Auto-renew is off, but the user keeps Pro until the period actually lapses (handled by the later `EXPIRATION`) |
| `BILLING_ISSUE` | No-op for v1 — Apple's billing-retry grace period generally keeps access alive until a real `EXPIRATION` fires |
| anything else | No-op, `200` |

Every branch is idempotent — safe for RevenueCat's retry-on-non-2xx behavior.

### 3.4 One-time (not a persisted script)
- Grandfather Ro/Reid/Ryan's existing accounts: `UPDATE accounts SET plan='pro' WHERE id IN (...)` run once by hand during implementation, so shipping the paywall doesn't interrupt the family's own usage.

### 3.5 Env vars (`.env`, and Railway)
- `REVENUECAT_WEBHOOK_SECRET` — shared secret RevenueCat sends back in the webhook's `Authorization` header.

---

## 4. Frontend changes

- Add `@revenuecat/purchases-capacitor` to `webapp/frontend/package.json`; `npx cap sync ios` (flag as an implementation-time risk: if the plugin's iOS side only ships a Podfile/podspec rather than SPM, this SPM-only project may need a one-time CocoaPods bootstrap — verify during implementation, not assumed here).
- New `webapp/frontend/src/purchases.js` — thin wrapper: `configurePurchases(accountId)`, `getOfferings()`, `purchasePackage(pkg)`, `restorePurchases()`.
- Call `configurePurchases(account.id)` once in `app.jsx`, right after the account is loaded from the backend (same place `is_pro` etc. become available).
- Extract `PaywallSheet` out of `AddProfile.jsx` into a shared `webapp/frontend/src/components/PaywallSheet.jsx` (same copy/pricing/feature-list, unchanged) — it now needs to be reachable from three places:
  1. Existing 402 on add-profile (unchanged trigger, now shared component).
  2. Settings "Upgrade" pill — currently has no `onClick`; wire it to open the sheet.
  3. Scan's `429 scan_limit_reached` — currently has zero UI reaction; add a minimal catch that opens the same sheet. (Small, closely related fix — the whole point of the paywall is to convert this exact moment.)
- Real purchase button: fetch the RevenueCat offering's package, call `purchasePackage`. On success, treat the returned `CustomerInfo` as authoritative for the client's own immediate UI (show "Pro" right away) — the webhook independently keeps the server's `accounts.plan` in sync for server-side gates (`/api/profiles`, `/api/identify`). On cancel, close silently. On error, inline message + retry. On "pending" (Ask to Buy / Family Sharing approval), show a waiting state and don't grant access until a real entitlement appears.
- **Trial-copy accuracy (new, from the "keep both trials" decision):** Apple decides per-Apple-ID whether a StoreKit intro offer trial applies — we can't just hardcode "14-day free trial" in the paywall copy regardless of the signup trial's state. `PaywallSheet` calls RevenueCat's `checkTrialOrIntroDiscountEligibility()` for the package and renders "14-day free trial · then $3.99/mo" only when eligible; otherwise "$3.99/mo · Cancel anytime" with no trial line.
- Settings: add a "Restore Purchases" action (Apple requires this for any app selling subscriptions) calling `restorePurchases()`.

---

## 5. Error handling summary

- Purchase cancel → no error, close sheet.
- StoreKit/network failure → inline error, retry.
- Ask-to-Buy pending → waiting state, no access granted yet.
- App killed mid-purchase → next launch's `Purchases.configure()` + `getCustomerInfo()` resyncs from RevenueCat's local cache, so no lasting client/server mismatch.
- Webhook: bad secret → `401`; malformed body → `400`; DB write failure → `500` (triggers retry); unknown account → `200` + log.
- No nightly reconciliation job in v1 (YAGNI) — revisit only if webhook drift shows up in practice.

---

## 6. Testing

- Backend: new `webapp/tests/test_billing_webhook.py`, following the existing `test_auth.py` / `test_db_postgres.py` pattern. Table-driven over the event-type transition table in §3, plus: wrong-secret → 401, malformed payload → 400. Also a unit test for the new `account_is_pro()` helper.
- Frontend: no test runner exists in this project (confirmed) — not introducing one now. Purchase-flow verification is manual:
  1. Xcode local StoreKit Configuration file in the Simulator — exercises the full purchase UI/UX today, no Apple Developer account required.
  2. Once App Store Connect + RevenueCat exist (see §7), a sandbox-tester pass on a real/TestFlight build for the true end-to-end webhook round trip.

---

## 7. Manual setup required (blocks real end-to-end testing, not code)

Not yet done as of this spec. To be completed by the project owner when ready:

1. Enroll in the Apple Developer Program ($99/yr).
2. App Store Connect: create the app record (bundle id `com.pokecollect.app`), a Subscription Group, and one auto-renewable subscription product (e.g. `com.pokecollect.app.pro.monthly`, $3.99/mo) with a 14-day free-trial introductory offer.
3. RevenueCat: create a project, add the iOS app with the bundle id + an App Store Connect API key (for server-side receipt validation), create a `"pro"` entitlement attached to the product, and an Offering/Package wrapping it.
4. Grab the RevenueCat iOS **public** SDK key → app config. Grab the webhook auth header value → `REVENUECAT_WEBHOOK_SECRET` (`.env` + Railway).
5. Point the RevenueCat webhook at `https://cardapp-production-569d.up.railway.app/api/webhooks/revenuecat`.

---

## 8. Out of scope for this pass

- Android / Google Play billing (no Android scaffold exists yet at all — separate future work per the parent doc).
- Annual pricing tier ($29.99/yr) — parent doc mentions it, but the current paywall UI only offers monthly; adding the second tier is a follow-up, not blocking this spec.
- Nightly webhook-drift reconciliation job.
- Proactive `BILLING_ISSUE` handling (grace-period banners, etc.).
