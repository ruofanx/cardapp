# Scan Result Panel Redesign — Design Spec

**Date:** 2026-06-12
**Status:** Approved

## Overview

Redesign `ScanResultSheet` (in `webapp/static/screens/Scan.jsx`) so scan results are
presented as a "Matching Cards" grid, and the primary action saves the selected
card straight to the user's collection (instead of queuing it for later bulk
review). After a successful add, the sheet resets and the camera is immediately
ready for the next card — enabling rapid back-to-back scanning of a stack of
cards.

## Scope

In scope: `ScanResultSheet` component only — its results display, the add
action, and removal of the "Add to cart" button.
Out of scope: `Bulk.jsx`, the `scanQueue` state/plumbing in `app.jsx`, and the
"Cart · N" bottom-nav button in `ScanScreen` — these are left as dead code for
now (Scan no longer feeds `scanQueue`, so it stays empty / the Cart button
always shows 0).

---

## Section 1: Results display — "Matching Cards" grid

Replace the current single-column candidate list (lines ~731-774) with a
2-column grid:

- **Header row**: "Matching Cards" title + `{filtered.length} Result(s)`
  (replacing the current "{filtered.length} of {candidates.length} matches"
  line). Small thumbnail of `capturedPhotoUrl` shown top-right of the header
  when available.
- **Filter chips** (TYPE / LANG / SET / RARITY): unchanged, kept above the
  grid — still useful since the result set is uncapped.
- **Grid cells** (`gridTemplateColumns: 'repeat(2, 1fr)'`, gap ~10px), one per
  `filtered` candidate:
  - `CardArt` at `size="md"` (110px wide, 5:7) as the cell's image, using the
    same `candForDisplay` fallback-to-captured-photo logic as today.
  - A small circular "+" badge overlaid on the top-right corner of the image
    (visual affordance only — see below for what it does).
  - Below the image: LANG badge + name (matches current row styling, wrapped
    instead of truncated to one line since cells are narrower), code + set,
    and rarity/variant.
  - Price + % change (`<Price>` component), right-aligned under the rarity
    line.
  - Selected cell (`i === safePicked`) gets the existing accent border +
    `--bg-2` background highlight; unselected cells use `--bg-1` /
    `--hairline-soft` border, same as today's rows.
- **Tapping a cell OR its "+" badge**: both call `setPicked(i)` — selecting
  that candidate for the add-details panel below. (The "+" is not a separate
  quick-add; it's part of the same select interaction, just visually
  signposting "this is the one that gets added".)
- The grid lives inside the existing scrollable container
  (`overflowY: 'auto', flex: 1`) — no slicing or capping of `filtered`/
  `candidates` is introduced anywhere in the pipeline.

No changes to `runIdentify()` / candidate-gathering logic — "don't limit how
many to scan" is satisfied by *not adding* any cap, not by changing the
search fan-out.

---

## Section 2: Add-details panel (unchanged)

The existing panel (lines ~777-940: LANG, COND, GRADER, GRADE, manual
PRICE + eBay link, **PAID $**, **TAGS**, adjusted-price summary) is kept
as-is below the grid, operating on the currently-selected `card`. This
already covers the "tags and dollar paid" requirement — no new fields
needed.

---

## Section 3: "Add to Collection" action

Bottom action row changes from `Skip / Details / Wishlist⭐ / Add to cart` to
`Done / Details / Wishlist⭐ / Add to Collection`:

- **Done** — same as today's `onSkip`: dismiss the sheet, return to `idle`,
  clear `pipelineLog`/`candidates`. (Renamed only; behavior unchanged.)
- **Details** — unchanged, navigates to the Detail screen with the edited
  card.
- **Wishlist ⭐** — unchanged.
- **Add to Collection** (primary, replaces "Add to cart"):
  - Builds the same payload `handleAdd` builds today (`condition`, `lang`,
    `grader`/`grade`/`is_graded`, `usd: adjUSD`, `purchase_price`, `tags`,
    plus `_capturedPhotoFile` if present).
  - Calls `await addToCollection(payload)` directly (this prop is already
    passed into `ScanScreen` and into `ScanResultSheet` for the wishlist
    path — reuse it).
  - **On success**: reset exactly like `handleAddWishlist` does today —
    `setPhase('idle')`, clear `pipelineLog`/`candidates` — plus show a toast
    "Added \"{card.name}\" to collection" (reuse the existing
    `wishlistToast` state/UI, renamed to a generic `toast` or a second toast
    variable — implementer's choice, just keep the existing 2.2s
    auto-dismiss pattern). This returns the user straight to the camera view,
    ready to scan the next card.
  - **On failure**: show an error toast ("Couldn't add to collection: ...",
    same pattern as the existing wishlist error toast) and **do not** change
    `phase`/clear `candidates` — the sheet stays open with the user's edits
    intact so they can retry.
  - Add a simple in-flight guard (e.g. disable the button / `isAdding` state)
    so a slow request can't be double-submitted by a second tap.

`handleAdd` (the `setScanQueue` version) and the "Add to cart" button are
removed from `ScanResultSheet` and `ScanScreen` entirely.

---

## Section 4: Edge cases

- **Empty `filtered`** (all results filtered out by chips): existing "No
  matches with these filters." message stays, grid renders nothing.
- **No `card` selected** (shouldn't happen since `safePicked` always clamps
  into `filtered`/`candidates`, but guarded today via `{card && (...)}`):
  add-details panel and action row stay hidden as today.
- **Captured-photo fallback image**: unchanged — cells without
  `image_url` show `capturedPhotoUrl`.
- **Sealed products**: unchanged — `ProductTypeBadge`, hidden condition/grade
  fields, etc. all carry over into the grid cell / add-details panel as they
  exist today.
