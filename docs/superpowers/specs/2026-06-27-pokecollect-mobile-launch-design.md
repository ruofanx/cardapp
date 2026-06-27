# PokeCollect — Mobile Launch Design

**Date:** 2026-06-27  
**Status:** Approved  
**Goal:** Transform the current 3-person LAN app into a publicly distributed iOS/Android app + hosted PWA with freemium monetization, targeting card collectors at trade shows.

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                  Users / Devices                     │
│  iPhone (App Store)  Android (Play Store)  Browser  │
│         └──────────────────┘                │        │
│              Capacitor Shell                 PWA     │
│         (WebView wrapping same React app)    URL     │
└──────────────────────┬──────────────────────────────┘
                       │ HTTPS
┌──────────────────────▼──────────────────────────────┐
│              Railway (Cloud Host)                    │
│         FastAPI backend  +  static React build       │
│              │                                       │
│         PostgreSQL (Supabase managed)                │
│         File storage: Supabase Storage (card photos) │
└─────────────────────────────────────────────────────┘
                       │
        ┌──────────────┴──────────────┐
        │                             │
   Supabase Auth              RevenueCat
  (accounts + JWT)        (IAP + subscriptions)
```

**What changes from today:**
- SQLite file → Supabase PostgreSQL (cloud, multi-user)
- Card photos in `uploads/` → Supabase Storage
- No auth → Supabase Auth (email/password + Google/Apple sign-in)
- Babel-in-browser → Vite build
- Capacitor wraps the Vite-built React app for iOS + Android

**What stays the same:**
- All FastAPI routes (minimal changes)
- All React screens (Browse, Scan, Detail, Trade, etc.)
- Pricing engines (eBay, PriceCharting, TCGdex)
- Trade proposer logic

---

## 2. Data Model & Auth

### Account Hierarchy
```
Account (Supabase Auth user — 1 email, 1 login)
  └── Profile (1 free, unlimited on paid plan)
        └── Collection (cards owned by this profile)
        └── Trade history
        └── Want list
```

A family signs up once with one email. The parent is the billing owner. Child profiles are added under the same account — no separate logins needed for kids; the parent switches profiles on the device.

### Auth Flow
- Sign up / log in with email+password, Google, or Apple (one tap)
- JWT token sent with every API request; FastAPI validates via Supabase middleware
- On first launch, user picks or creates a profile (like the current user-switcher)

### Database Schema Changes
- All existing tables (cards, price_history, trades) get `account_id` + `profile_id` columns
- New tables: `profiles`, `want_list`, `price_alerts`, `scan_usage`
- eBay/PriceCharting/OCR caches remain as server-side SQLite (cache data, not user data)

### Trade Show Mode (per profile)
- `trade_mode: boolean` toggle in Settings per profile
- When on, cards marked `for_trade: true` become visible to other attendees
- Discovery via shareable QR code / link — no GPS required to start

---

## 3. Monetization

### Tiers

| Feature | Free | Family/Pro ($4.99–$7.99/mo) |
|---|---|---|
| Profiles | 1 | Unlimited |
| Card scans (OCR) | 20/month | Unlimited |
| Price view | Current price only | Full history + charts |
| Price alerts | — | ✓ |
| Want list (broadcast at shows) | — | ✓ |
| Portfolio analytics | — | ✓ |
| Collection export (CSV/PDF) | — | ✓ |
| Ads | Shown | Hidden |

### Payment Infrastructure
- **RevenueCat** handles iOS IAP, Google Play billing, and Stripe (web) in one SDK
- Free users see a paywall sheet when tapping "Add Profile", price alerts, or export
- Subscription managed in-app; cancellation handled by the platform store

### Future Monetization (not in v1)
- Banner ads for free users (Google AdMob via Capacitor plugin)
- AI grading / condition assessment (high API cost — premium add-on)
- Social/follow features

---

## 4. Frontend Changes

### Vite Migration
Replace Babel-in-browser with Vite build. Same JSX files, compiled at build time. Output: `dist/` folder served by FastAPI and packaged by Capacitor. Estimated effort: ~1 day.

### New Screens

| Screen | Notes |
|---|---|
| Onboarding / Sign Up | Email+password, Google, Apple. Replaces current `SettingsAndOnboarding.jsx` |
| Profile Switcher | Wire existing UI to real account/profile model |
| Add Profile (Paywall) | Free users see paywall; paid users enter kid's name + avatar |
| Subscription / Upgrade | RevenueCat paywall UI (pre-built sheet or custom) |
| Trade Show Mode toggle | In Settings — enables public visibility + want list broadcast |
| Want List | New screen: cards you're looking for; visible to traders at shows |
| Price Alerts | Set target price per card; push notification on trigger |
| Export Collection | One-tap CSV/PDF generation |

### Existing Screens — Minimal Changes
- Browse, Detail, Scan, Trade, Home: wire to real `profile_id` from auth
- Scan: add counter badge for free users ("14 / 20 scans used this month")

### Capacitor Plugins Added
- Camera (replaces browser `getUserMedia` — more reliable on iOS)
- Push notifications (price alerts)
- In-app purchases (wired to RevenueCat)

---

## 5. Backend Changes

### FastAPI Updates
- **Auth middleware**: `get_current_profile()` dependency on all routes — injects `account_id` + `profile_id`, scopes all queries automatically
- **Scan rate limiting**: Monthly counter per free profile; returns `429` with upgrade prompt when limit hit
- **Trade show endpoints**:
  - `GET /show/traders` — list public profiles at an event (filter by shareable event ID)
  - `GET /show/wantlist` — search want lists of public traders
- **Price alert job**: Extend existing APScheduler daily refresh to check alerts and trigger Supabase push notifications
- **Export endpoint**: `GET /profile/export` — streams CSV of user's collection

### Migration Plan
1. Write one-time migration script: current SQLite data → PostgreSQL with `account_id` + `profile_id` seeded for the existing family
2. eBay/PriceCharting/OCR caches stay as local SQLite on the Railway instance

---

## 6. Deployment & Cost

| Service | Purpose | Cost |
|---|---|---|
| Railway | FastAPI host + static React `dist/` | ~$5/mo |
| Supabase | PostgreSQL + Auth + Storage | Free tier to start |
| RevenueCat | IAP + subscriptions | Free up to $2.5k MRR |
| Apple Developer Program | iOS App Store | $99/yr |
| Google Play | Android App Store | $25 one-time |

**Total upfront cost to launch: ~$124. Monthly burn: ~$5.**

---

## 7. Launch Sequence

1. Vite migration + Supabase auth wiring
2. PostgreSQL schema + data migration
3. New screens (onboarding, paywall, profiles)
4. Wire existing screens to real auth/profile IDs
5. RevenueCat integration (IAP + subscription)
6. Trade show mode + want list
7. Capacitor build → TestFlight (iOS) + internal track (Android)
8. PWA goes live on Railway (public URL, immediate)
9. App Store / Play Store submission
10. Price alerts + push notifications
11. Export feature
