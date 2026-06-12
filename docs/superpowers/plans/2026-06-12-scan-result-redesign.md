# Scan Result Panel Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign `ScanResultSheet` in `webapp/static/screens/Scan.jsx` into a "Matching Cards" grid whose primary action saves the selected card straight to the collection (`addToCollection`) and resets the screen so the next card can be scanned immediately.

**Architecture:** Single-file change to `webapp/static/screens/Scan.jsx`, split into four sequential edits: (1) `ScanScreen`-level handler/state rewiring (toast generalization + new `handleAddToCollection`, drop queue-based `handleAdd`), (2) `ScanResultSheet` header redesign, (3) results list → 2-column grid, (4) bottom action row redesign. There is no JS test runner for this Babel-in-browser frontend, so each task is verified live against the already-running dev server at `:8000` using the chrome-devtools MCP tools (console-error check + screenshot), per `feedback-no-extra-uvicorn` — do NOT start a second uvicorn.

**Tech Stack:** React 18 + Babel-standalone JSX (in-browser transpile, no build step). Existing shared components: `Icon`, `CardArt`, `Price` from `webapp/static/components.jsx`.

---

## Task 1: `ScanScreen` — direct `addToCollection`, generalized toast, drop queue path

**Files:**
- Modify: `webapp/static/screens/Scan.jsx`

- [ ] **Step 1: Drop the unused `setScanQueue` prop**

In the `ScanScreen` function signature, remove `setScanQueue` (no longer written to — `scanQueue` itself stays, it's still read by the cart chip / bottom Cart button):

Old:
```js
function ScanScreen({ tweaks, navigate, scanQueue, setScanQueue, identifyCard, addToCollection, backend }) {
```

New:
```js
function ScanScreen({ tweaks, navigate, scanQueue, identifyCard, addToCollection, backend }) {
```

- [ ] **Step 2: Remove the queue-based `handleAdd`**

Delete this entire function (it pushed the card into `scanQueue` for later bulk review — superseded by direct `addToCollection`):

```js
  const handleAdd = (card) => {
    // Attach the user's captured photo so it survives the queue → backend
    // → userPhotos pipeline. Bulk flush / Add path can persist it after the
    // backend card row is created with a real id.
    const cardWithPhoto = capturedPhotoFile
      ? { ...card, _capturedPhotoFile: capturedPhotoFile }
      : card;
    setScanQueue(q => [...q, cardWithPhoto]);
    setPhase('idle');
    setPipelineLog([]);
    setCandidates([]);
  };

```

- [ ] **Step 3: Generalize the wishlist toast and add `handleAddToCollection`**

Replace the `wishlistToast` state + `handleAddWishlist` block with a generic `toast` state (shape `{ icon, text }`) used by both the wishlist path and the new collection-add path, and add `handleAddToCollection` right after it.

Old:
```js
  const [wishlistToast, setWishlistToast] = useStateScan(null);
  const handleAddWishlist = async (card) => {
    if (!addToCollection) return;
    const existingTags = Array.isArray(card.tags) ? card.tags : [];
    const tags = existingTags.includes('wishlist')
      ? existingTags
      : [...existingTags, 'wishlist'];
    try {
      await addToCollection({
        ...card,
        purchase_price: null,    // wishlist items aren't owned yet
        tags,
      });
      setWishlistToast(`Added "${card.name}" to wishlist`);
      setTimeout(() => setWishlistToast(null), 2200);
    } catch (e) {
      setWishlistToast(`Couldn't add to wishlist: ${String(e.message || e).slice(0, 60)}`);
      setTimeout(() => setWishlistToast(null), 3000);
    }
    setPhase('idle');
    setPipelineLog([]);
    setCandidates([]);
  };
```

New:
```js
  // Generic bottom toast — { icon, text }. Used for both the wishlist path
  // and the direct "Add to Collection" path below.
  const [toast, setToast] = useStateScan(null);
  const handleAddWishlist = async (card) => {
    if (!addToCollection) return;
    const existingTags = Array.isArray(card.tags) ? card.tags : [];
    const tags = existingTags.includes('wishlist')
      ? existingTags
      : [...existingTags, 'wishlist'];
    try {
      await addToCollection({
        ...card,
        purchase_price: null,    // wishlist items aren't owned yet
        tags,
      });
      setToast({ icon: 'star', text: `Added "${card.name}" to wishlist` });
      setTimeout(() => setToast(null), 2200);
    } catch (e) {
      setToast({ icon: 'info', text: `Couldn't add to wishlist: ${String(e.message || e).slice(0, 60)}` });
      setTimeout(() => setToast(null), 3000);
    }
    setPhase('idle');
    setPipelineLog([]);
    setCandidates([]);
  };

  // Direct-to-collection path — replaces the old scanQueue-based "Add to
  // cart". On success, resets the sheet so the camera is immediately ready
  // for the next card. On failure, leaves the sheet open (with the user's
  // edits intact) so they can retry.
  const handleAddToCollection = async (card) => {
    if (!addToCollection) return;
    const cardWithPhoto = capturedPhotoFile
      ? { ...card, _capturedPhotoFile: capturedPhotoFile }
      : card;
    try {
      await addToCollection(cardWithPhoto);
      setToast({ icon: 'check', text: `Added "${card.name}" to collection` });
      setTimeout(() => setToast(null), 2200);
      setPhase('idle');
      setPipelineLog([]);
      setCandidates([]);
    } catch (e) {
      setToast({ icon: 'info', text: `Couldn't add "${card.name}": ${String(e.message || e).slice(0, 60)}` });
      setTimeout(() => setToast(null), 3000);
    }
  };
```

- [ ] **Step 4: Rewire the `ScanResultSheet` invocation**

Old:
```jsx
        <ScanResultSheet
          candidates={candidates}
          tweaks={tweaks}
          capturedPhotoUrl={capturedPhotoUrl}
          capturedPhotoFile={capturedPhotoFile}
          onAdd={handleAdd}
          onAddWishlist={handleAddWishlist}
          onSkip={handleSkip}
          onDetail={(card) => navigate('detail', { card })}
        />
```

New:
```jsx
        <ScanResultSheet
          candidates={candidates}
          tweaks={tweaks}
          capturedPhotoUrl={capturedPhotoUrl}
          capturedPhotoFile={capturedPhotoFile}
          onAddToCollection={handleAddToCollection}
          onAddWishlist={handleAddWishlist}
          onSkip={handleSkip}
          onDetail={(card) => navigate('detail', { card })}
        />
```

- [ ] **Step 5: Generalize the toast render block**

Old:
```jsx
      {/* Wishlist confirmation toast — auto-dismisses */}
      {wishlistToast && (
        <div style={{
          position: 'absolute', left: 16, right: 16, bottom: 96,
          padding: '12px 14px', borderRadius: 12,
          background: 'oklch(0.20 0.04 200 / 0.95)',
          backdropFilter: 'blur(12px) saturate(140%)',
          color: 'var(--ink)', fontSize: 13, fontWeight: 500,
          boxShadow: '0 12px 32px -12px oklch(0 0 0 / 0.6)',
          zIndex: 50, animation: 'riseIn 0.24s ease-out',
          display: 'flex', alignItems: 'center', gap: 10,
        }}>
          <Icon name="star" size={16} style={{ color: 'var(--accent)' }}/>
          <span style={{ flex: 1 }}>{wishlistToast}</span>
        </div>
      )}
```

New:
```jsx
      {/* Confirmation/error toast — auto-dismisses */}
      {toast && (
        <div style={{
          position: 'absolute', left: 16, right: 16, bottom: 96,
          padding: '12px 14px', borderRadius: 12,
          background: 'oklch(0.20 0.04 200 / 0.95)',
          backdropFilter: 'blur(12px) saturate(140%)',
          color: 'var(--ink)', fontSize: 13, fontWeight: 500,
          boxShadow: '0 12px 32px -12px oklch(0 0 0 / 0.6)',
          zIndex: 50, animation: 'riseIn 0.24s ease-out',
          display: 'flex', alignItems: 'center', gap: 10,
        }}>
          <Icon name={toast.icon} size={16} style={{ color: toast.icon === 'info' ? 'var(--neg)' : 'var(--accent)' }}/>
          <span style={{ flex: 1 }}>{toast.text}</span>
        </div>
      )}
```

- [ ] **Step 6: Update the `ScanResultSheet` signature**

Old:
```js
function ScanResultSheet({ candidates, tweaks, capturedPhotoUrl, capturedPhotoFile, onAdd, onAddWishlist, onSkip, onDetail }) {
```

New:
```js
function ScanResultSheet({ candidates, tweaks, capturedPhotoUrl, capturedPhotoFile, onAddToCollection, onAddWishlist, onSkip, onDetail }) {
```

- [ ] **Step 7: Point the existing "Add to cart" button at `onAddToCollection`**

This is a minimal rewire only — Task 4 redesigns this button's label/styling/loading state. For now just swap the call so the app is fully wired and testable after this task.

Old:
```jsx
            <button className="tap" onClick={() => {
              const paidNum = Number(purchasePrice);
              const tags = tagsInput
                .split(',')
                .map(t => t.trim())
                .filter(Boolean);
              onAdd({
                ...card,
                condition,
                lang,
                grader: isGraded ? grader : null,
                grade:  isGraded ? grade  : null,
                is_graded: isGraded,
                usd: adjUSD,
                purchase_price: (purchasePrice !== '' && Number.isFinite(paidNum) && paidNum >= 0) ? paidNum : null,
                tags,
              });
            }} style={{
              flex: 1.4, height: 46, borderRadius: 14,
              background: 'var(--accent)', color: 'var(--accent-ink)',
              fontWeight: 600, fontSize: 14,
            }}>Add to cart</button>
```

New:
```jsx
            <button className="tap" onClick={() => {
              const paidNum = Number(purchasePrice);
              const tags = tagsInput
                .split(',')
                .map(t => t.trim())
                .filter(Boolean);
              onAddToCollection({
                ...card,
                condition,
                lang,
                grader: isGraded ? grader : null,
                grade:  isGraded ? grade  : null,
                is_graded: isGraded,
                usd: adjUSD,
                purchase_price: (purchasePrice !== '' && Number.isFinite(paidNum) && paidNum >= 0) ? paidNum : null,
                tags,
              });
            }} style={{
              flex: 1.4, height: 46, borderRadius: 14,
              background: 'var(--accent)', color: 'var(--accent-ink)',
              fontWeight: 600, fontSize: 14,
            }}>Add to cart</button>
```

- [ ] **Step 8: Verify in browser**

The dev server is already running on `:8000` (do NOT start another uvicorn). Using the chrome-devtools MCP tools:
1. `navigate_page` to `http://localhost:8000/`
2. Navigate into the Scan screen, search for a known card name (e.g. "Pikachu") via the search bar
3. `list_console_messages` — confirm no new errors (Babel transpile errors surface here)
4. Pick a candidate, tap "Add to cart" (still labeled that for now)
5. Confirm a toast reading `Added "<name>" to collection` appears, the sheet dismisses, and the camera view returns
6. Navigate to Browse/Home and confirm the card now appears in the collection

- [ ] **Step 9: Commit**

```bash
cd /Users/ruofanxu/claude/CardApp
git add webapp/static/screens/Scan.jsx
git commit -m "feat(scan): add cards directly to collection instead of the scan queue"
```

---

## Task 2: `ScanResultSheet` — "Matching Cards" header + captured-photo thumbnail

**Files:**
- Modify: `webapp/static/screens/Scan.jsx`

- [ ] **Step 1: Replace the match-count header**

Old:
```jsx
        <div className="row gap-2" style={{ marginBottom: 10, color: 'var(--pos)' }}>
          <Icon name="check" size={16} stroke={2.4}/>
          <span style={{ fontSize: 12, fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase' }}>
            {filtered.length} of {candidates.length} match{candidates.length === 1 ? '' : 'es'}
          </span>
        </div>
```

New:
```jsx
        <div className="row" style={{ marginBottom: 10, alignItems: 'flex-start', justifyContent: 'space-between', gap: 10 }}>
          <div>
            <div style={{ fontSize: 16, fontWeight: 700 }}>Matching Cards</div>
            <div style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: 2 }}>
              {filtered.length} Result{filtered.length === 1 ? '' : 's'}
            </div>
          </div>
          {capturedPhotoUrl && (
            <img src={capturedPhotoUrl} alt="" style={{
              width: 40, height: 56, objectFit: 'cover', borderRadius: 8,
              border: '1px solid var(--hairline-soft)', flexShrink: 0,
            }}/>
          )}
        </div>
```

- [ ] **Step 2: Verify in browser**

1. `navigate_page` to `http://localhost:8000/` (or reload if already there)
2. Run a scan/search that returns at least one candidate
3. `take_screenshot` — confirm the sheet now shows "Matching Cards" / "N Results" with the captured-photo thumbnail top-right (thumbnail only appears for photo-based scans, not text search)
4. `list_console_messages` — confirm no new errors

- [ ] **Step 3: Commit**

```bash
cd /Users/ruofanxu/claude/CardApp
git add webapp/static/screens/Scan.jsx
git commit -m "feat(scan): replace match-count header with Matching Cards header"
```

---

## Task 3: `ScanResultSheet` — 2-column "Matching Cards" results grid

**Files:**
- Modify: `webapp/static/screens/Scan.jsx`

- [ ] **Step 1: Replace the candidate list with a 2-column grid**

Old:
```jsx
        {/* Scrollable results list */}
        <div style={{ overflowY: 'auto', flex: 1, marginRight: -16, paddingRight: 16 }}>
          {filtered.length === 0 && (
            <div style={{ padding: '24px 0', color: 'var(--ink-3)', fontSize: 13, textAlign: 'center' }}>
              No matches with these filters.
            </div>
          )}
          <div className="col" style={{ gap: 8 }}>
            {filtered.map((cand, i) => {
              const active = i === safePicked;
              // If we have no catalogued art for this printing, fall back to
              // the user's own scan photo so they see THEIR actual card, not
              // a misleading English/Japanese reprint.
              const candForDisplay = cand.image_url ? cand : { ...cand, image_url: capturedPhotoUrl || null };
              return (
                <button key={cand.id || i} className="tap" onClick={() => setPicked(i)} style={{
                  display: 'grid', gridTemplateColumns: '52px 1fr auto', gap: 12, alignItems: 'center',
                  padding: 10, borderRadius: 14, textAlign: 'left',
                  background: active ? 'var(--bg-2)' : 'var(--bg-1)',
                  border: `1px solid ${active ? 'var(--accent)' : 'var(--hairline-soft)'}`,
                }}>
                  <CardArt card={candForDisplay} renderMode={tweaks.cardRender} size="sm" flat/>
                  <div style={{ minWidth: 0 }}>
                    <div className="row gap-2" style={{ alignItems: 'center' }}>
                      <span style={{
                        fontSize: 9, fontWeight: 700, padding: '1px 5px', borderRadius: 4, flexShrink: 0,
                        background: cand.lang === 'JP' ? 'oklch(0.45 0.16 25)'
                                   : cand.lang === 'CH' ? 'oklch(0.45 0.14 80)'
                                   : 'oklch(0.40 0.06 250)',
                        color: '#fff',
                        letterSpacing: '0.05em',
                      }}>{cand.lang || 'EN'}</span>
                      <span style={{ fontSize: 14, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1, minWidth: 0 }}>{cand.name}</span>
                    </div>
                    {!window.api?.isSealedProduct?.(cand) && (
                    <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {cand.code}{cand.set ? ` · ${cand.set}` : ''}
                    </div>
                    )}
                    {(cand.variant || cand._rarity) && (
                      <div style={{ fontSize: 10, color: 'var(--ink-3)', marginTop: 2 }}>{cand.variant || cand._rarity}</div>
                    )}
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <Price usd={cand.usd} currency={cur === 'BOTH' ? 'USD' : cur} size="sm"/>
                  </div>
                </button>
              );
            })}
          </div>
        </div>
```

New:
```jsx
        {/* Scrollable results grid */}
        <div style={{ overflowY: 'auto', flex: 1, marginRight: -16, paddingRight: 16 }}>
          {filtered.length === 0 && (
            <div style={{ padding: '24px 0', color: 'var(--ink-3)', fontSize: 13, textAlign: 'center' }}>
              No matches with these filters.
            </div>
          )}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 10 }}>
            {filtered.map((cand, i) => {
              const active = i === safePicked;
              // If we have no catalogued art for this printing, fall back to
              // the user's own scan photo so they see THEIR actual card, not
              // a misleading English/Japanese reprint.
              const candForDisplay = cand.image_url ? cand : { ...cand, image_url: capturedPhotoUrl || null };
              return (
                <button key={cand.id || i} className="tap" onClick={() => setPicked(i)} style={{
                  display: 'flex', flexDirection: 'column', gap: 6,
                  padding: 10, borderRadius: 14, textAlign: 'left',
                  background: active ? 'var(--bg-2)' : 'var(--bg-1)',
                  border: `1px solid ${active ? 'var(--accent)' : 'var(--hairline-soft)'}`,
                }}>
                  <div style={{ position: 'relative', alignSelf: 'center' }}>
                    <CardArt card={candForDisplay} renderMode={tweaks.cardRender} size="md" flat/>
                    <div style={{
                      position: 'absolute', top: -6, right: -6,
                      width: 24, height: 24, borderRadius: 12,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      background: active ? 'var(--accent)' : 'var(--bg-3)',
                      color: active ? 'var(--accent-ink)' : 'var(--ink-2)',
                      border: '1px solid var(--hairline-soft)',
                    }}>
                      <Icon name="plus" size={14} stroke={2.4}/>
                    </div>
                  </div>
                  <div className="row gap-2" style={{ alignItems: 'center' }}>
                    <span style={{
                      fontSize: 9, fontWeight: 700, padding: '1px 5px', borderRadius: 4, flexShrink: 0,
                      background: cand.lang === 'JP' ? 'oklch(0.45 0.16 25)'
                                 : cand.lang === 'CH' ? 'oklch(0.45 0.14 80)'
                                 : 'oklch(0.40 0.06 250)',
                      color: '#fff',
                      letterSpacing: '0.05em',
                    }}>{cand.lang || 'EN'}</span>
                    <span style={{ fontSize: 13, fontWeight: 600, lineHeight: 1.25 }}>{cand.name}</span>
                  </div>
                  {!window.api?.isSealedProduct?.(cand) && (
                    <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>
                      {cand.code}{cand.set ? ` · ${cand.set}` : ''}
                    </div>
                  )}
                  {(cand.variant || cand._rarity) && (
                    <div style={{ fontSize: 10, color: 'var(--ink-3)' }}>{cand.variant || cand._rarity}</div>
                  )}
                  <div style={{ marginTop: 2 }}>
                    <Price usd={cand.usd} currency={cur === 'BOTH' ? 'USD' : cur} size="sm"/>
                  </div>
                </button>
              );
            })}
          </div>
        </div>
```

- [ ] **Step 2: Verify in browser**

1. Reload `http://localhost:8000/`, run a search/scan that returns 2+ candidates (e.g. search "Sliggoo" — known to return both an EN and a JP printing per `project_cardapp` memory)
2. `take_screenshot` — confirm a 2-column grid renders: image, LANG badge + name, code/set, rarity, price, with a "+" badge on each card's image
3. Tap the second candidate — confirm its cell gets the accent border highlight and the edit-details panel below updates to that card
4. `list_console_messages` — confirm no new errors

- [ ] **Step 3: Commit**

```bash
cd /Users/ruofanxu/claude/CardApp
git add webapp/static/screens/Scan.jsx
git commit -m "feat(scan): show scan results as a 2-column Matching Cards grid"
```

---

## Task 4: `ScanResultSheet` — action row: Done / Add to Collection + in-flight guard

**Files:**
- Modify: `webapp/static/screens/Scan.jsx`

- [ ] **Step 1: Add `isAdding` state**

Add this alongside the other edit-details state (right after the `tagsInput` declaration):

Old:
```js
  // Comma-separated user tags (e.g. "favorite, trade-bait, sleeve").
  const [tagsInput, setTagsInput] = useStateScan('');
```

New:
```js
  // Comma-separated user tags (e.g. "favorite, trade-bait, sleeve").
  const [tagsInput, setTagsInput] = useStateScan('');
  // In-flight guard for "Add to Collection" — disables the button and
  // prevents double-submits while the request is pending.
  const [isAdding, setIsAdding] = useStateScan(false);
```

- [ ] **Step 2: Replace the action row**

Old:
```jsx
        {card && (
          <div className="row gap-2" style={{ marginTop: 10 }}>
            <button className="tap" onClick={onSkip} style={{
              flex: 1, height: 46, borderRadius: 14,
              background: 'var(--bg-2)', color: 'var(--ink)',
              fontWeight: 500, fontSize: 14,
            }}>Skip</button>
            <button className="tap" onClick={() => onDetail({ ...card, condition, lang, grader: isGraded ? grader : null, grade: isGraded ? grade : null, is_graded: isGraded, usd: adjUSD })} style={{
              flex: 1, height: 46, borderRadius: 14,
              background: 'var(--bg-2)', color: 'var(--ink)',
              fontWeight: 500, fontSize: 14,
            }}>Details</button>
            {onAddWishlist && (
              <button className="tap" title="Add to wishlist" aria-label="Add to wishlist"
                onClick={() => {
                  const tags = tagsInput.split(',').map(t => t.trim()).filter(Boolean);
                  onAddWishlist({
                    ...card,
                    condition,
                    lang,
                    grader:    isGraded ? grader : null,
                    grade:     isGraded ? grade  : null,
                    is_graded: isGraded,
                    usd:       adjUSD,
                    tags,
                  });
                }}
                style={{
                  width: 46, height: 46, borderRadius: 14, flexShrink: 0,
                  background: 'var(--accent-soft)', color: 'var(--accent)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                }}>
                <Icon name="star" size={20}/>
              </button>
            )}
            <button className="tap" onClick={() => {
              const paidNum = Number(purchasePrice);
              const tags = tagsInput
                .split(',')
                .map(t => t.trim())
                .filter(Boolean);
              onAddToCollection({
                ...card,
                condition,
                lang,
                grader: isGraded ? grader : null,
                grade:  isGraded ? grade  : null,
                is_graded: isGraded,
                usd: adjUSD,
                purchase_price: (purchasePrice !== '' && Number.isFinite(paidNum) && paidNum >= 0) ? paidNum : null,
                tags,
              });
            }} style={{
              flex: 1.4, height: 46, borderRadius: 14,
              background: 'var(--accent)', color: 'var(--accent-ink)',
              fontWeight: 600, fontSize: 14,
            }}>Add to cart</button>
          </div>
        )}
```

New:
```jsx
        {card && (
          <div className="row gap-2" style={{ marginTop: 10 }}>
            <button className="tap" onClick={onSkip} style={{
              flex: 1, height: 46, borderRadius: 14,
              background: 'var(--bg-2)', color: 'var(--ink)',
              fontWeight: 500, fontSize: 14,
            }}>Done</button>
            <button className="tap" onClick={() => onDetail({ ...card, condition, lang, grader: isGraded ? grader : null, grade: isGraded ? grade : null, is_graded: isGraded, usd: adjUSD })} style={{
              flex: 1, height: 46, borderRadius: 14,
              background: 'var(--bg-2)', color: 'var(--ink)',
              fontWeight: 500, fontSize: 14,
            }}>Details</button>
            {onAddWishlist && (
              <button className="tap" title="Add to wishlist" aria-label="Add to wishlist"
                onClick={() => {
                  const tags = tagsInput.split(',').map(t => t.trim()).filter(Boolean);
                  onAddWishlist({
                    ...card,
                    condition,
                    lang,
                    grader:    isGraded ? grader : null,
                    grade:     isGraded ? grade  : null,
                    is_graded: isGraded,
                    usd:       adjUSD,
                    tags,
                  });
                }}
                style={{
                  width: 46, height: 46, borderRadius: 14, flexShrink: 0,
                  background: 'var(--accent-soft)', color: 'var(--accent)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                }}>
                <Icon name="star" size={20}/>
              </button>
            )}
            <button className="tap" disabled={isAdding} onClick={async () => {
              const paidNum = Number(purchasePrice);
              const tags = tagsInput
                .split(',')
                .map(t => t.trim())
                .filter(Boolean);
              setIsAdding(true);
              try {
                await onAddToCollection({
                  ...card,
                  condition,
                  lang,
                  grader: isGraded ? grader : null,
                  grade:  isGraded ? grade  : null,
                  is_graded: isGraded,
                  usd: adjUSD,
                  purchase_price: (purchasePrice !== '' && Number.isFinite(paidNum) && paidNum >= 0) ? paidNum : null,
                  tags,
                });
              } finally {
                setIsAdding(false);
              }
            }} style={{
              flex: 1.4, height: 46, borderRadius: 14,
              background: 'var(--accent)', color: 'var(--accent-ink)',
              fontWeight: 600, fontSize: 14,
              opacity: isAdding ? 0.6 : 1,
            }}>{isAdding ? 'Adding…' : 'Add to Collection'}</button>
          </div>
        )}
```

- [ ] **Step 3: End-to-end verification in browser**

Using the chrome-devtools MCP tools against the existing `:8000` server:
1. `navigate_page` to `http://localhost:8000/`, go to Scan
2. Run a search/scan returning 2+ candidates
3. `take_screenshot` — confirm action row reads `Done | Details | ⭐ | Add to Collection`
4. Pick a candidate in the grid, fill in TAGS (e.g. "binder") and PAID $ (e.g. "5.00")
5. Tap "Add to Collection" — confirm button shows "Adding…" briefly, then a toast `Added "<name>" to collection` appears and the sheet dismisses back to the camera view (ready for the next scan)
6. `list_console_messages` — confirm no new errors
7. Navigate to Browse, find the newly-added card, confirm its tags include "binder" and purchase price is $5.00
8. Repeat steps 2-5 once more without reloading, to confirm the screen is correctly reset for back-to-back scans
9. Trigger a failure case if feasible (e.g. temporarily disconnect network in devtools, tap Add to Collection) and confirm an error toast appears while the sheet stays open with the entered tags/price intact — then restore network

- [ ] **Step 4: Commit**

```bash
cd /Users/ruofanxu/claude/CardApp
git add webapp/static/screens/Scan.jsx
git commit -m "feat(scan): rename action row to Done/Add to Collection with in-flight guard"
```
