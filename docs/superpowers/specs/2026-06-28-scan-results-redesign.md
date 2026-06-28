# Scan Results Sheet Redesign

**Date:** 2026-06-28
**Status:** Approved

## Problem

The current `ScanResultSheet` in `Scan.jsx` combines card browsing and editing into one cramped bottom sheet: a 2-column thumbnail grid plus a full LANG/COND/GRADE/PRICE/PAID/TAGS edit panel. On a phone this leaves almost no room to actually see the card you found.

## Goal

Give the user a clear, large view of the matched card first, let them browse alternates via a filmstrip, then tap the card to go to the Detail screen for full editing.

## Design

### Layout (top → bottom inside the sheet)

1. **Drag handle + header** — "Matching Cards · N Results" left, scanned photo thumb right. Unchanged.
2. **Hero zone** — centered card image (~110px wide, natural 5:7 aspect ratio). Below: lang badge, card name (bold), `set·code` (mono), auto NM price. Tapping the card image (or the info below) calls `onDetail(card)` with NM/EN/Raw defaults.
3. **Filter chips** — LANG / SET / RARITY rows (horizontal scroll, same logic as today). Moved below the hero.
4. **Candidate strip** — single horizontal-scroll row of small thumbnails (~52px wide, 72px tall). Selected card has accent border. Tapping a thumb updates the hero.
5. **Action bar** — `Done` | ★ (wishlist icon) | `Add to Collection` (quick-add, NM/EN/Raw defaults, existing logic).

### What is removed

- The entire edit panel (LANG/COND/GRADE/PRICE/PAID/TAGS rows, adjusted price display).
- The `Details` button (its function moves to tapping the hero card).

### onDetail call

When the hero is tapped, pass card with sensible defaults so Detail has everything it needs:
```js
onDetail({ ...card, condition: 'NM', lang: card.lang || 'EN', grader: null, grade: null, is_graded: false, usd: card.usd })
```

### Quick-add ("Add to Collection")

Adds with `condition: 'NM'`, `lang: card.lang || 'EN'`, `grader: null`, `grade: null` — same as today's "Add to Collection" button but without the user-edited overrides (those are now set in Detail).

### Detail screen

No changes needed. It already receives a card object from `onDetail` and supports the full edit flow.

## Files changed

- `webapp/static/screens/Scan.jsx` — `ScanResultSheet` function only. No other files.

## Non-goals

- No changes to Detail.jsx, Browse.jsx, or any backend.
- No new state or API calls.
- No wishlist flow changes (★ button works identically).
