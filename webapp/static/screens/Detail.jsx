/* Card detail — price history, sold listings, EN/JP, conditions */

const { useState: useStateDetail, useRef: useRefDetail } = React;

// Grade scales per service. PSA = whole grades only, top is 10. CGC, BGS,
// SGC all have a special "above 10" top grade that's a separate SKU and
// trades at a major premium. We represent that with the sentinel 10.5:
//   CGC 10.5 = CGC 10 Pristine
//   BGS 10.5 = BGS 10 Black Label   (all sub-grades = 10)
//   SGC 10.5 = SGC 10 Pristine
// The backend GRADED_MULTIPLIERS table understands this convention.
const GRADE_OPTIONS = {
  PSA: [10, 9, 8, 7, 6, 5],
  CGC: [10.5, 10, 9.5, 9, 8.5, 8, 7],
  BGS: [10.5, 10, 9.5, 9, 8.5, 8],
  SGC: [10.5, 10, 9.5, 9, 8.5, 8],
};

// Compact label shown inside the grade button. The 10.5 sentinel renders
// distinctly so it doesn't look like an ordinary half-grade.
function gradeButtonLabel(grader, grade) {
  if (grade === 10.5) {
    if (grader === 'BGS') return '10 BL';   // Black Label
    return '10 P';                          // Pristine for CGC / SGC
  }
  return String(grade);
}

// Full label used in chips / detail strings (e.g. "BGS 10 Black Label").
function fullGradeLabel(grader, grade) {
  if (grade === 10.5) {
    if (grader === 'BGS') return 'BGS 10 Black Label';
    return `${grader} 10 Pristine`;
  }
  return `${grader} ${grade}`;
}

// Build deep-link URLs to each marketplace for the user to verify pricing.
// We don't ingest live sold listings ourselves — these jump to the source so
// the user sees the real numbers (filtered by language + grader/grade).
function buildSourceLinks({ card, lang, grading, grader, grade }) {
  if (!card?.name) return [];
  const name = card.name;
  const code = card.code || '';
  const set  = card.set  || '';
  const isGraded = grading === 'graded';
  const base = [name, code, set].filter(Boolean).join(' ');
  const enc = (s) => encodeURIComponent(s);

  // eBay — reuse the api helper so the search uses the same token logic
  // (Japanese qualifier, grading hint) the rest of the app uses.
  let ebayUrl;
  try {
    ebayUrl = window.api?.buildEbayUrl
      ? window.api.buildEbayUrl({ ...card, lang }, isGraded ? { grader, grade } : {})
      : null;
  } catch (_) { ebayUrl = null; }
  if (!ebayUrl) {
    const ebayQ = `${base}${lang === 'JP' ? ' Japanese' : ''}${isGraded ? ` ${grader} ${grade}` : ''}`;
    ebayUrl = `https://www.ebay.com/sch/i.html?_nkw=${enc(ebayQ)}&LH_Sold=1&LH_Complete=1&_sop=13`;
  }

  const links = [{
    key: 'ebay',
    label: 'eBay (sold)',
    sub:   isGraded ? `${grader} ${grade} · recently sold` : 'Recently sold listings',
    lang,
    url: ebayUrl,
  }];

  if (isGraded) {
    // PriceCharting is the canonical graded source — it lists per-grade
    // medians by grading company.
    links.push({
      key: 'pricecharting',
      label: 'PriceCharting',
      sub:   `${grader} ${grade} · raw · all grades`,
      lang,
      url: `https://www.pricecharting.com/search-products?q=${enc(base)}&type=prices`,
    });
  } else if (lang === 'EN') {
    links.push({
      key: 'tcgplayer',
      label: 'TCGplayer',
      sub:   'Market price + active listings',
      lang: 'EN',
      url: `https://www.tcgplayer.com/search/pokemon/product?q=${enc(base)}&view=grid`,
    });
    links.push({
      key: 'cardmarket',
      label: 'Cardmarket',
      sub:   'European trend price (EUR)',
      lang: 'EN',
      url: `https://www.cardmarket.com/en/Pokemon/Cards?searchString=${enc(name)}`,
    });
  } else if (lang === 'JP') {
    links.push({
      key: 'yahoojp',
      label: 'Yahoo! オークション',
      sub:   'Japanese sold auctions',
      lang: 'JP',
      url: `https://auctions.yahoo.co.jp/search/search?p=${enc(name)}&fixed=1&exflg=1&b=1`,
    });
    links.push({
      key: 'cardmarket',
      label: 'Cardmarket',
      sub:   'Japanese listings (EUR)',
      lang: 'JP',
      url: `https://www.cardmarket.com/en/Pokemon/Cards?searchString=${enc(name + ' japanese')}`,
    });
  }
  return links;
}

// Friendly relative time. ISO timestamp → "2h ago" / "3d ago" / "Mar 14".
function fmtAgo(iso) {
  if (!iso) return null;
  const t = new Date(iso);
  if (isNaN(t.getTime())) return null;
  const sec = (Date.now() - t.getTime()) / 1000;
  if (sec < 60) return 'just now';
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  if (sec < 86400 * 7) return `${Math.floor(sec / 86400)}d ago`;
  return t.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function DetailScreen({ tweaks, navigate, addToCollection, removeCard, refreshPrice, updateCard, collection, params = {} }) {
  const cur = tweaks.currency;
  const [tab, setTab] = useStateDetail('overview');
  const [condition, setCondition] = useStateDetail(params.card?.condition || 'NM');
  const [lang, setLang] = useStateDetail(params.card?.lang || 'EN');
  // Grading state: 'raw' or 'graded'. Initialize from the card so a graded
  // import lands in graded mode without the user toggling.
  const [grading, setGrading] = useStateDetail(params.card?.is_graded ? 'graded' : 'raw');
  const [grader, setGrader] = useStateDetail(params.card?.grader || 'PSA');
  const [grade, setGrade] = useStateDetail(params.card?.grade != null ? params.card.grade : 10);
  const [refreshing, setRefreshing] = useStateDetail(false);
  const [adding, setAdding] = useStateDetail(false);
  const [deleting, setDeleting] = useStateDetail(false);
  const [confirmingDelete, setConfirmingDelete] = useStateDetail(false);
  const [lightboxIdx, setLightboxIdx] = useStateDetail(null);
  const [uploadingPhoto, setUploadingPhoto] = useStateDetail(false);
  // Draft pricing preview. When the user toggles a selector, we fetch a new
  // quote into previewPrice (no backend write). The Save banner commits the
  // selector state + the previewed price together; Discard reverts.
  const [previewPrice, setPreviewPrice] = useStateDetail(null);
  const [quoting,      setQuoting]      = useStateDetail(false);
  const [savingDraft,  setSavingDraft]  = useStateDetail(false);
  // Edit-sheet state (only shown for cards already in the collection).
  const [editing, setEditing] = useStateDetail(false);
  const [editPrice, setEditPrice] = useStateDetail('');
  const [editTags, setEditTags] = useStateDetail([]);
  const [newTag, setNewTag] = useStateDetail('');
  const [savingEdit, setSavingEdit] = useStateDetail(false);
  const photoInputRef = useRefDetail(null);
  const userPhotos = window.useUserPhotos(params.card?.id);
  // Reflect updates from app state when collection refreshes the card.
  const live = (collection || []).find(x => x.id === params.card?.id);
  const c = live || params.card || (window.CARDS && window.CARDS[0]) || {};

  // Real price history fetched from the backend. The /api/cards/{id}/price-history
  // endpoint returns every recorded snapshot for this card, oldest first.
  // Backend seeds one row from last_priced_at for legacy cards (backfill),
  // appends a new row on every PATCH that changes current_market_price, and
  // on card creation. The UI gets {at, price} pairs and slices by range below.
  const [historyPoints, setHistoryPoints] = useStateDetail(null);
  React.useEffect(() => {
    if (!c?.id || !window.api?.getPriceHistory) { setHistoryPoints(null); return; }
    // tmp- ids are optimistic inserts that don't exist on the backend yet.
    if (String(c.id).startsWith('tmp-')) { setHistoryPoints([]); return; }
    let cancelled = false;
    (async () => {
      try {
        const res = await window.api.getPriceHistory(c.id);
        if (cancelled) return;
        setHistoryPoints(Array.isArray(res?.points) ? res.points : []);
      } catch (_) {
        if (!cancelled) setHistoryPoints([]);
      }
    })();
    return () => { cancelled = true; };
    // Re-fetch when the card identity changes, or after a refresh that
    // updates last_priced_at / current_market_price (a fresh snapshot lands).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [c?.id, c?.last_priced_at, c?.current_market_price]);
  // Use the real gain_loss_pct from the backend (set from purchase_price vs
  // current_market_price). Falls back to 0 when unknown.
  const change = Number(c.change) || 0;

  // Persisted values (from the backend card row). Used to compute isDirty
  // and to revert the draft on Discard.
  const persistedLang      = c.lang || 'EN';
  const persistedCondition = c.condition || 'NM';
  const persistedGrading   = c.is_graded ? 'graded' : 'raw';
  const persistedGrader    = c.grader || null;
  const persistedGrade     = c.grade != null ? c.grade : null;

  const isDirty = (
    lang      !== persistedLang ||
    condition !== persistedCondition ||
    grading   !== persistedGrading ||
    (grading === 'graded' && (
      grader !== persistedGrader || grade !== persistedGrade
    ))
  );

  // When the user navigates to a different card (same Detail screen, new
  // c.id), reset the draft selectors and clear any preview. Without this the
  // draft would leak across cards because we don't remount on card change.
  const lastIdRef = React.useRef(c.id);
  React.useEffect(() => {
    if (lastIdRef.current === c.id) return;
    lastIdRef.current = c.id;
    setCondition(c.condition || 'NM');
    setLang(c.lang || 'EN');
    setGrading(c.is_graded ? 'graded' : 'raw');
    setGrader(c.grader || 'PSA');
    setGrade(c.grade != null ? c.grade : 10);
    setPreviewPrice(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [c.id]);

  // Build the metadata payload the backend expects. Used by both the
  // quote-only preview and the persist path (handleRefresh + saveDraft).
  const priceQuery = () => ({
    ...c,
    lang,
    condition,
    is_graded: grading === 'graded',
    grader:    grading === 'graded' ? grader : null,
    grade:     grading === 'graded' ? grade  : null,
  });

  const handleRefresh = async () => {
    if (!refreshPrice || !c.id || refreshing) return;
    setRefreshing(true);
    try { await refreshPrice(priceQuery()); } finally { setRefreshing(false); }
  };

  // Auto-fetch image when landing on a card that doesn't have one yet.
  // One-shot per mount — refreshPrice marks _priceUnavailable on failure so
  // we won't loop, and the Pokemon TCG API fallback still loads the art.
  const autoTriedRef = React.useRef(false);
  React.useEffect(() => {
    if (autoTriedRef.current) return;
    if (!c?.id || c.image_url || c._priceUnavailable || !refreshPrice) return;
    autoTriedRef.current = true;
    handleRefresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [c?.id, c?.image_url]);

  // Quote-only preview: whenever the selectors diverge from the persisted
  // values, fetch a new estimated price without writing it back. The user
  // explicitly Saves to commit. Debounced to coalesce rapid clicks.
  React.useEffect(() => {
    if (!isDirty) { setPreviewPrice(null); return; }
    if (!window.api?.quotePrice || !c?.id) return;
    let cancelled = false;
    const t = setTimeout(async () => {
      setQuoting(true);
      try {
        const res = await window.api.quotePrice(priceQuery());
        if (cancelled) return;
        setPreviewPrice(res?.estimated_price != null ? Number(res.estimated_price) : null);
      } catch (_) {
        if (!cancelled) setPreviewPrice(null);
      } finally {
        if (!cancelled) setQuoting(false);
      }
    }, 350);
    return () => { cancelled = true; clearTimeout(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDirty, lang, condition, grading, grader, grade]);

  // Commit the draft selectors + previewed price to the backend.
  const saveDraft = async () => {
    if (!updateCard || !c.id || savingDraft) return;
    setSavingDraft(true);
    try {
      const fields = {
        condition,
        language:  lang === 'JP' ? 'japanese' : lang === 'CH' ? 'chinese' : 'english',
        is_graded: grading === 'graded',
      };
      if (grading === 'graded') {
        fields.grade_company = grader;
        fields.grade = grade;
      }
      if (previewPrice != null) fields.current_market_price = previewPrice;
      await updateCard(c.id, fields);
      setPreviewPrice(null);
    } catch (_) {
      // Banner surfaces the error; keep the draft so the user can retry.
    } finally {
      setSavingDraft(false);
    }
  };

  // Revert selectors to the last-saved state.
  const discardDraft = () => {
    setCondition(persistedCondition);
    setLang(persistedLang);
    setGrading(persistedGrading);
    setGrader(persistedGrader || 'PSA');
    setGrade(persistedGrade != null ? persistedGrade : 10);
    setPreviewPrice(null);
  };

  const handlePickPhoto = () => {
    if (photoInputRef.current) photoInputRef.current.click();
  };
  const handlePhotoSelected = async (e) => {
    const files = Array.from(e.target.files || []);
    e.target.value = ''; // allow re-uploading the same file
    if (!files.length || !c.id) return;
    setUploadingPhoto(true);
    try {
      for (const f of files) {
        try { await window.userPhotos.add(c.id, f); }
        catch (err) { console.warn('photo add failed:', err.message); }
      }
    } finally {
      setUploadingPhoto(false);
    }
  };
  const handleRemovePhoto = (idx) => {
    if (!c.id) return;
    window.userPhotos.remove(c.id, idx);
    setLightboxIdx(null);
  };

  const handleDelete = async () => {
    if (!removeCard || !c.id || deleting) return;
    setDeleting(true);
    try {
      await removeCard(c.id);
      navigate('__back');
    } catch (_) {
      setDeleting(false);
      setConfirmingDelete(false);
    }
  };

  const isInCollection = Boolean((collection || []).find(x => x.id === c.id));

  // -- Edit sheet ----------------------------------------------------------
  const normalizeTagList = (raw) => (Array.isArray(raw) ? raw : [])
    .map(t => typeof t === 'object' && t ? (t.name || t.label || '') : String(t))
    .map(t => t.trim())
    .filter(Boolean);

  const openEdit = () => {
    setEditPrice(c.purchase_price != null ? String(c.purchase_price) : '');
    setEditTags(normalizeTagList(c.tags));
    setNewTag('');
    setEditing(true);
  };

  const addEditTag = () => {
    const t = newTag.trim().replace(/^#+/, '');
    if (!t) return;
    if (editTags.includes(t)) { setNewTag(''); return; }
    setEditTags([...editTags, t]);
    setNewTag('');
  };

  const removeEditTag = (t) => {
    setEditTags(editTags.filter(x => x !== t));
  };

  const saveEdit = async () => {
    if (!updateCard || !c.id || savingEdit) return;
    const fields = {};
    const trimmed = editPrice.trim();
    const nextPrice = trimmed === '' ? null : Number(trimmed);
    if (nextPrice !== c.purchase_price && !(nextPrice == null && c.purchase_price == null)) {
      fields.purchase_price = nextPrice;
    }
    const origTags = normalizeTagList(c.tags);
    const tagsChanged = origTags.length !== editTags.length
      || origTags.some((t, i) => t !== editTags[i]);
    if (tagsChanged) fields.tags = editTags;

    if (Object.keys(fields).length === 0) { setEditing(false); return; }
    setSavingEdit(true);
    try {
      await updateCard(c.id, fields);
      setEditing(false);
    } catch (_) {
      // Banner surfaces the error; keep the sheet open so the user can retry.
    } finally {
      setSavingEdit(false);
    }
  };

  const handleTrade = () => {
    navigate('trade', { giveCard: c });
  };

  // Reset the catalog image + auto-fetched price. Used when wrong art got
  // attached (e.g. obscure card with no exact name match in the Pokemon TCG
  // catalog and the old lookupCardImage code picked candidates[0]). After
  // reset, the auto-fetch effect tries once more with the strict matcher;
  // if there's still no match it leaves both fields null and the UI falls
  // back to the placeholder / user photos.
  const [resettingArt, setResettingArt] = useStateDetail(false);
  const resetCardArt = async () => {
    if (!updateCard || !c.id || resettingArt) return;
    setResettingArt(true);
    try {
      await updateCard(c.id, {
        image_url: null,
        current_market_price: null,
      });
      // Reset the one-shot guard so the auto-image-fetch effect can try again.
      autoTriedRef.current = false;
      setEditing(false);
    } catch (_) {
      // BackendBanner surfaces the error.
    } finally {
      setResettingArt(false);
    }
  };

  const handleAdd = async () => {
    if (!addToCollection || adding) return;
    setAdding(true);
    try {
      await addToCollection({
        ...c, condition, lang,
        is_graded: grading === 'graded',
        grader:    grading === 'graded' ? grader : null,
        grade:     grading === 'graded' ? grade  : null,
      });
      navigate('browse');
    } catch (e) {
      // Error surfaced via the BackendBanner; keep the user on the screen.
    } finally {
      setAdding(false);
    }
  };

  // Price to show in the hero.
  //   - Dirty + quote landed → previewPrice (what Save will commit)
  //   - Dirty + quote still in flight → local multiplier estimate, so the
  //     number changes immediately on click (not after the 350ms debounce)
  //   - Clean → persisted c.usd
  const condMult = { NM: 1, LP: 0.78, MP: 0.55, HP: 0.34, DMG: 0.20 };
  const langMult = { EN: 1, JP: 0.86 };
  const localEstimate = (c.usd != null && grading === 'raw')
    ? c.usd * (condMult[condition] || 1) * (langMult[lang] || 1)
    : null;
  const adjUSD = isDirty
    ? (previewPrice != null ? previewPrice : localEstimate)
    : c.usd;

  return (
    <div className="screen" style={{ animation: 'pushIn 0.25s ease-out' }}>
      <NavBar
        title={c.set || 'Card'}
        left={<NavBackButton onClick={() => navigate('__back')} label=""/>}
        right={<>
          <button className="tap" onClick={handleRefresh} disabled={refreshing} style={{ color: 'var(--ink-2)', opacity: refreshing ? 0.5 : 1 }} title="Refresh price">
            <Icon name="refresh" size={20}/>
          </button>
          {isInCollection && (
            <button className="tap" onClick={() => setConfirmingDelete(true)} disabled={deleting} style={{ color: 'var(--neg)', opacity: deleting ? 0.5 : 1 }} title="Remove from collection">
              <Icon name="trash" size={20}/>
            </button>
          )}
          {/* Wishlist toggle — adds/removes the `wishlist` tag via PATCH.
              Only enabled for cards already saved in the backend (need a
              real id to PATCH). Tag lives in the same store as everything
              else, so Browse's Wishlist chip picks it up immediately. */}
          {isInCollection && (() => {
            const currentTags = normalizeTagList(c.tags);
            const onWishlist = currentTags.some(t => t.toLowerCase() === 'wishlist');
            const toggleWishlist = async () => {
              if (!updateCard || !c.id) return;
              const next = onWishlist
                ? currentTags.filter(t => t.toLowerCase() !== 'wishlist')
                : [...currentTags, 'wishlist'];
              try { await updateCard(c.id, { tags: next }); } catch (_) {}
            };
            return (
              <button
                className="tap"
                onClick={toggleWishlist}
                title={onWishlist ? 'Remove from wishlist' : 'Add to wishlist'}
                aria-pressed={onWishlist}
                style={{ color: onWishlist ? 'var(--accent)' : 'var(--ink-2)' }}
              >
                <Icon name="star" size={20} fill={onWishlist ? 'currentColor' : 'none'}/>
              </button>
            );
          })()}
        </>}
      />

      {confirmingDelete && (
        <div onClick={() => !deleting && setConfirmingDelete(false)} style={{
          position: 'absolute', inset: 0, background: 'oklch(0 0 0 / 0.55)',
          zIndex: 100, display: 'flex', alignItems: 'flex-end',
          animation: 'fadeIn 0.15s ease-out',
        }}>
          <div onClick={e => e.stopPropagation()} style={{
            width: '100%',
            background: 'var(--bg-1)',
            borderTopLeftRadius: 18, borderTopRightRadius: 18,
            padding: '20px 16px calc(env(safe-area-inset-bottom, 0px) + 20px)',
            boxShadow: '0 -8px 32px oklch(0 0 0 / 0.4)',
            animation: 'riseIn 0.2s ease-out',
          }}>
            <div style={{ fontSize: 17, fontWeight: 600, marginBottom: 4 }}>
              Remove {c.name || 'this card'}?
            </div>
            <div style={{ fontSize: 13, color: 'var(--ink-3)', marginBottom: 16 }}>
              This permanently removes the card from your collection. You can scan or add it again later.
            </div>
            <div className="col" style={{ gap: 8 }}>
              <button className="tap" onClick={handleDelete} disabled={deleting} style={{
                padding: '13px 16px', borderRadius: 12,
                background: 'var(--neg)', color: '#fff',
                fontSize: 15, fontWeight: 600, opacity: deleting ? 0.6 : 1,
              }}>{deleting ? 'Removing…' : 'Remove card'}</button>
              <button className="tap" onClick={() => setConfirmingDelete(false)} disabled={deleting} style={{
                padding: '13px 16px', borderRadius: 12,
                background: 'var(--bg-2)', color: 'var(--ink)',
                fontSize: 15, fontWeight: 500,
              }}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {editing && (
        <div onClick={() => !savingEdit && setEditing(false)} style={{
          position: 'absolute', inset: 0, background: 'oklch(0 0 0 / 0.55)',
          zIndex: 100, display: 'flex', alignItems: 'flex-end',
          animation: 'fadeIn 0.15s ease-out',
        }}>
          <div onClick={e => e.stopPropagation()} style={{
            width: '100%', maxHeight: '85%', overflowY: 'auto',
            background: 'var(--bg-1)',
            borderTopLeftRadius: 18, borderTopRightRadius: 18,
            padding: '20px 16px calc(env(safe-area-inset-bottom, 0px) + 20px)',
            boxShadow: '0 -8px 32px oklch(0 0 0 / 0.4)',
            animation: 'riseIn 0.2s ease-out',
          }}>
            <div style={{ fontSize: 17, fontWeight: 600, marginBottom: 2 }}>Edit details</div>
            <div style={{ fontSize: 12, color: 'var(--ink-3)', marginBottom: 18 }}>
              {c.name || 'This card'} · {c.code || ''}
            </div>

            {/* Purchase price */}
            <div style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 11, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 6 }}>
                Purchase price
              </div>
              <div className="row" style={{
                background: 'var(--bg-2)', borderRadius: 10, padding: '10px 12px', gap: 6,
                border: '1px solid var(--hairline-soft)',
              }}>
                <span style={{ color: 'var(--ink-3)', fontSize: 15 }}>$</span>
                <input
                  type="number"
                  inputMode="decimal"
                  step="0.01"
                  value={editPrice}
                  onChange={e => setEditPrice(e.target.value)}
                  placeholder="0.00"
                  style={{
                    flex: 1, background: 'transparent', border: 'none', outline: 'none',
                    color: 'var(--ink)', fontSize: 15, fontWeight: 500,
                  }}
                />
                {editPrice !== '' && (
                  <button className="tap" onClick={() => setEditPrice('')} style={{ color: 'var(--ink-3)', fontSize: 12 }}>Clear</button>
                )}
              </div>
              <div style={{ fontSize: 11, color: 'var(--ink-4)', marginTop: 4 }}>
                What you paid. Used for gain/loss vs. median.
              </div>
            </div>

            {/* Tags */}
            <div style={{ marginBottom: 18 }}>
              <div style={{ fontSize: 11, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 6 }}>
                Tags
              </div>
              {editTags.length > 0 && (
                <div className="row gap-1" style={{ flexWrap: 'wrap', marginBottom: 8 }}>
                  {editTags.map(t => (
                    <span key={t} className="chip" style={{ paddingRight: 4, gap: 4 }}>
                      #{t}
                      <button className="tap" onClick={() => removeEditTag(t)} aria-label={`Remove tag ${t}`} style={{
                        width: 16, height: 16, borderRadius: 8,
                        background: 'var(--bg-3)', color: 'var(--ink-2)',
                        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                        fontSize: 11, lineHeight: 1, padding: 0,
                      }}>×</button>
                    </span>
                  ))}
                </div>
              )}
              <div className="row gap-2">
                <input
                  value={newTag}
                  onChange={e => setNewTag(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addEditTag(); } }}
                  placeholder="for trade, binder, gift…"
                  style={{
                    flex: 1, background: 'var(--bg-2)',
                    border: '1px solid var(--hairline-soft)', borderRadius: 10,
                    padding: '10px 12px', color: 'var(--ink)', fontSize: 14, outline: 'none',
                  }}
                />
                <button className="tap" onClick={addEditTag} disabled={!newTag.trim()} style={{
                  padding: '10px 14px', borderRadius: 10,
                  background: 'var(--bg-2)', color: 'var(--ink-2)',
                  fontSize: 13, fontWeight: 600,
                  opacity: newTag.trim() ? 1 : 0.5,
                  border: '1px solid var(--hairline-soft)',
                }}>Add</button>
              </div>
              {/* Suggested tags */}
              {(() => {
                const suggested = ['for trade', 'binder', 'pc', 'graded', 'wishlist']
                  .filter(s => !editTags.includes(s));
                if (suggested.length === 0) return null;
                return (
                  <div className="row gap-1" style={{ flexWrap: 'wrap', marginTop: 8 }}>
                    {suggested.map(s => (
                      <button key={s} className="tap" onClick={() => setEditTags([...editTags, s])} style={{
                        padding: '4px 8px', borderRadius: 999,
                        background: 'transparent', color: 'var(--ink-3)',
                        border: '1px dashed var(--hairline-soft)',
                        fontSize: 11, fontWeight: 500,
                      }}>+ {s}</button>
                    ))}
                  </div>
                );
              })()}
            </div>

            {/* Card art — show current image + a reset for cards where the
                Pokemon TCG API attached the wrong art (Movie 2 promos like
                Ancient Mew, regional exclusives, etc. that have no exact
                match in the catalog). */}
            {c.image_url && (
              <div style={{ marginBottom: 18 }}>
                <div style={{ fontSize: 11, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 6 }}>
                  Card art
                </div>
                <div className="row gap-2" style={{
                  alignItems: 'center', padding: 10, background: 'var(--bg-2)',
                  borderRadius: 12, border: '1px solid var(--hairline-soft)',
                }}>
                  <img
                    src={c.image_url}
                    alt="Current card art"
                    style={{ width: 44, height: 60, borderRadius: 6, objectFit: 'cover', flexShrink: 0 }}
                  />
                  <div className="col" style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 12, color: 'var(--ink-2)' }}>
                      Wrong card showing?
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--ink-4)', lineHeight: 1.4, marginTop: 2 }}>
                      Clears the catalog image &amp; auto-fetched price. Your photos and edits are kept.
                    </div>
                  </div>
                  <button className="tap" onClick={resetCardArt} disabled={resettingArt || savingEdit} style={{
                    flexShrink: 0, padding: '8px 12px', borderRadius: 10,
                    background: 'var(--bg-3)', color: 'var(--neg)',
                    fontSize: 12, fontWeight: 600,
                    border: '1px solid var(--hairline-soft)',
                    opacity: resettingArt ? 0.6 : 1,
                  }}>{resettingArt ? 'Resetting…' : 'Reset'}</button>
                </div>
              </div>
            )}

            <div className="col" style={{ gap: 8 }}>
              <button className="tap" onClick={saveEdit} disabled={savingEdit || resettingArt} style={{
                padding: '13px 16px', borderRadius: 12,
                background: 'var(--accent)', color: 'var(--accent-ink)',
                fontSize: 15, fontWeight: 600, opacity: savingEdit ? 0.6 : 1,
              }}>{savingEdit ? 'Saving…' : 'Save'}</button>
              <button className="tap" onClick={() => setEditing(false)} disabled={savingEdit || resettingArt} style={{
                padding: '13px 16px', borderRadius: 12,
                background: 'var(--bg-2)', color: 'var(--ink)',
                fontSize: 15, fontWeight: 500,
              }}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      <div className="screen-scroll">
        {/* Lightbox for user photos */}
        {lightboxIdx != null && userPhotos[lightboxIdx] && (
          <div onClick={() => setLightboxIdx(null)} style={{
            position: 'absolute', inset: 0, zIndex: 110,
            background: 'oklch(0 0 0 / 0.92)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            padding: 24,
            animation: 'fadeIn 0.15s ease-out',
          }}>
            <img
              src={userPhotos[lightboxIdx].url}
              alt={`User photo ${lightboxIdx + 1}`}
              style={{ maxWidth: '100%', maxHeight: '85%', objectFit: 'contain', borderRadius: 12, boxShadow: '0 16px 40px oklch(0 0 0 / 0.6)' }}
              onClick={(e) => e.stopPropagation()}
            />
            <div style={{
              position: 'absolute', top: 16, left: 16, right: 16,
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            }}>
              <button className="tap" onClick={() => setLightboxIdx(null)} style={{
                width: 36, height: 36, borderRadius: 18,
                background: 'oklch(0 0 0 / 0.5)', backdropFilter: 'blur(12px)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                color: '#fff',
              }} aria-label="Close"><Icon name="x" size={18}/></button>
              <button className="tap" onClick={(e) => { e.stopPropagation(); handleRemovePhoto(lightboxIdx); }} style={{
                padding: '8px 14px', borderRadius: 18,
                background: 'oklch(0.45 0.18 25)',
                color: '#fff',
                fontSize: 13, fontWeight: 600,
              }}>
                Remove
              </button>
            </div>
            <div style={{
              position: 'absolute', bottom: 24, left: 16, right: 16, textAlign: 'center',
              color: 'oklch(1 0 0 / 0.5)', fontSize: 11,
            }}>
              {lightboxIdx + 1} of {userPhotos.length}
            </div>
          </div>
        )}

        {/* Hidden file input for photo uploads */}
        <input
          ref={photoInputRef}
          type="file"
          accept="image/*"
          multiple
          capture="environment"
          onChange={handlePhotoSelected}
          style={{ display: 'none' }}
        />

        {/* Hero */}
        <div style={{ padding: '20px 16px 16px', display: 'flex', gap: 16, alignItems: 'flex-start' }}>
          <div style={{ position: 'relative' }}>
            <CardArt card={c} renderMode={tweaks.cardRender} size="lg"/>
            {c.holo && (
              <div style={{
                position: 'absolute', top: -6, right: -6,
                background: 'oklch(0.85 0.14 80)', color: 'oklch(0.18 0.05 80)',
                fontSize: 9, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase',
                padding: '3px 6px', borderRadius: 5,
              }}>Foil</div>
            )}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 22, fontWeight: 600, letterSpacing: '-0.02em', lineHeight: 1.15 }}>{c.name}</div>
            <div className="mono" style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: 4 }}>{c.code}</div>
            <div className="row gap-1" style={{ marginTop: 10, flexWrap: 'wrap' }}>
              {c.variant && <span className="chip chip-strong">{c.variant}</span>}
              {c.is_graded && (c.grader || c.grade != null) && (
                <span className="chip" style={{
                  background: 'oklch(0.78 0.16 75)', color: 'oklch(0.18 0.05 75)',
                  border: 'none', fontWeight: 700, letterSpacing: '0.02em',
                }}>
                  {fullGradeLabel(c.grader, c.grade)}
                </span>
              )}
              {c.hp && <span className="chip">HP {c.hp}</span>}
              {(c.tags || []).map(t => (
                <span key={String(t)} className="chip">#{typeof t === 'object' ? (t.name || t.label || '') : t}</span>
              ))}
            </div>
            <div style={{ marginTop: 14, padding: 10, background: 'var(--bg-1)', borderRadius: 10, border: '1px solid var(--hairline-soft)' }}>
              <div style={{ fontSize: 10, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 2 }}>Median · 30d</div>
              <Price usd={adjUSD} currency={cur} size="lg"/>
              {change !== 0 && (
                <div className={`mono ${change >= 0 ? 'delta-pos' : 'delta-neg'}`} style={{ fontSize: 12, fontWeight: 500, marginTop: 2 }}>
                  {change >= 0 ? '+' : ''}{change.toFixed(1)}%
                  {c.gain_loss != null && ` · ${fmtUSD(c.gain_loss, { sign: true, decimals: 0 })}`}
                </div>
              )}
              {c.purchase_price != null && (
                <div style={{ marginTop: 6, paddingTop: 6, borderTop: '1px solid var(--hairline-soft)' }}>
                  <div style={{ fontSize: 10, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase' }}>Paid</div>
                  <div className="mono" style={{ fontSize: 13, fontWeight: 500, marginTop: 1 }}>{fmtUSD(c.purchase_price)}</div>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* User photo gallery — your own shots (slab, back, condition) — */}
        {/* doesn't replace the index image, just adds to the per-card library. */}
        <div style={{ padding: '4px 0 14px' }}>
          <div className="row" style={{ padding: '0 16px 8px', justifyContent: 'space-between', alignItems: 'baseline' }}>
            <div style={{ fontSize: 11, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase' }}>
              My photos {userPhotos.length > 0 ? `· ${userPhotos.length}` : ''}
            </div>
            <button className="tap" onClick={handlePickPhoto} disabled={uploadingPhoto || !c.id} style={{
              fontSize: 13, fontWeight: 600, color: 'var(--accent)',
              opacity: (uploadingPhoto || !c.id) ? 0.5 : 1,
            }}>{uploadingPhoto ? 'Uploading…' : '+ Add'}</button>
          </div>
          <div style={{ display: 'flex', gap: 8, padding: '0 16px', overflowX: 'auto', scrollbarWidth: 'none' }}>
            {/* Add tile */}
            <button className="tap" onClick={handlePickPhoto} disabled={uploadingPhoto || !c.id} style={{
              width: 80, height: 112, flexShrink: 0,
              borderRadius: 10,
              border: '1.5px dashed var(--hairline-soft)',
              background: 'var(--bg-1)',
              color: 'var(--ink-3)',
              display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 4,
              opacity: (uploadingPhoto || !c.id) ? 0.5 : 1,
            }}>
              <Icon name="plus" size={20}/>
              <span style={{ fontSize: 10, fontWeight: 500 }}>{uploadingPhoto ? 'Saving…' : 'Add photo'}</span>
            </button>
            {userPhotos.map((p, i) => (
              <button key={p.ts + '-' + i} className="tap" onClick={() => setLightboxIdx(i)} style={{
                width: 80, height: 112, flexShrink: 0,
                borderRadius: 10, overflow: 'hidden',
                background: 'var(--bg-1)',
                border: '1px solid var(--hairline-soft)',
                padding: 0,
                position: 'relative',
              }}>
                <img src={p.url} alt={`User photo ${i + 1}`} style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}/>
              </button>
            ))}
          </div>
          {userPhotos.length === 0 && (
            <div style={{ padding: '6px 16px 0', fontSize: 11, color: 'var(--ink-4)' }}>
              Add slab, back, or condition shots — stored on this device.
            </div>
          )}
        </div>

        {/* Lang + Raw/Graded toggle + sub-picker */}
        <div style={{ padding: '0 16px' }}>
          <div className="row gap-2" style={{ marginBottom: 8 }}>
            <div className="row" style={{ flex: 1, background: 'var(--bg-2)', borderRadius: 10, padding: 3 }}>
              {['EN', 'JP'].map(L => (
                <button key={L} className="tap" onClick={() => setLang(L)} style={{
                  flex: 1, padding: '7px 0', borderRadius: 8,
                  background: lang === L ? 'var(--bg-3)' : 'transparent',
                  color: lang === L ? 'var(--ink)' : 'var(--ink-3)',
                  fontWeight: 600, fontSize: 13,
                }}>{L}</button>
              ))}
            </div>
            <div className="row" style={{ flex: 1, background: 'var(--bg-2)', borderRadius: 10, padding: 3 }}>
              {[['raw', 'Raw'], ['graded', 'Graded']].map(([v, label]) => (
                <button key={v} className="tap" onClick={() => setGrading(v)} style={{
                  flex: 1, padding: '7px 0', borderRadius: 8,
                  background: grading === v ? 'var(--bg-3)' : 'transparent',
                  color: grading === v ? 'var(--ink)' : 'var(--ink-3)',
                  fontWeight: 600, fontSize: 13,
                }}>{label}</button>
              ))}
            </div>
          </div>

          {grading === 'raw' ? (
            <div className="row" style={{ background: 'var(--bg-2)', borderRadius: 10, padding: 3 }}>
              {['NM', 'LP', 'MP', 'HP', 'DMG'].map(g => (
                <button key={g} className="tap" onClick={() => setCondition(g)} style={{
                  flex: 1, padding: '7px 0', borderRadius: 8,
                  background: condition === g ? 'var(--bg-3)' : 'transparent',
                  color: condition === g ? 'var(--ink)' : 'var(--ink-3)',
                  fontWeight: 600, fontSize: 12,
                }}>{g}</button>
              ))}
            </div>
          ) : (
            <div className="col" style={{ gap: 6 }}>
              <div className="row" style={{ background: 'var(--bg-2)', borderRadius: 10, padding: 3 }}>
                {Object.keys(GRADE_OPTIONS).map(g => (
                  <button key={g} className="tap" onClick={() => {
                    setGrader(g);
                    // Snap grade to nearest valid value for the new grader.
                    if (!GRADE_OPTIONS[g].includes(grade)) setGrade(GRADE_OPTIONS[g][0]);
                  }} style={{
                    flex: 1, padding: '7px 0', borderRadius: 8,
                    background: grader === g ? 'var(--bg-3)' : 'transparent',
                    color: grader === g ? 'var(--ink)' : 'var(--ink-3)',
                    fontWeight: 600, fontSize: 12,
                  }}>{g}</button>
                ))}
              </div>
              <div className="row" style={{ background: 'var(--bg-2)', borderRadius: 10, padding: 3, gap: 2, overflowX: 'auto', scrollbarWidth: 'none' }}>
                {GRADE_OPTIONS[grader].map(n => (
                  <button key={n} className="tap" onClick={() => setGrade(n)} style={{
                    flex: '1 0 auto', minWidth: 38, padding: '7px 6px', borderRadius: 8,
                    background: grade === n ? 'var(--bg-3)' : 'transparent',
                    color: grade === n ? 'var(--ink)' : 'var(--ink-3)',
                    fontWeight: 600, fontSize: 12, whiteSpace: 'nowrap',
                  }}>{gradeButtonLabel(grader, n)}</button>
                ))}
              </div>
            </div>
          )}

          {/* Save / Discard banner — appears when draft selectors diverge
              from the saved card. Changes are preview-only until Save. */}
          {isInCollection && isDirty && (
            <div style={{
              marginTop: 10, padding: '10px 12px',
              background: 'var(--bg-1)', border: '1px solid var(--accent)',
              borderRadius: 12,
              display: 'flex', alignItems: 'center', gap: 10,
            }}>
              <div className="col" style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink)' }}>
                  Preview · not saved
                </div>
                <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {grading === 'graded'
                    ? fullGradeLabel(grader, grade)
                    : `${condition} · ${lang === 'JP' ? 'Japanese' : lang === 'CH' ? 'Chinese' : 'English'}`}
                  {quoting ? ' · quoting…' : (previewPrice != null ? ` · ${fmtUSD(previewPrice)}` : '')}
                </div>
              </div>
              <button className="tap" onClick={discardDraft} disabled={savingDraft} style={{
                padding: '8px 12px', borderRadius: 10,
                background: 'var(--bg-2)', color: 'var(--ink-2)',
                fontSize: 12, fontWeight: 600,
              }}>Discard</button>
              <button className="tap" onClick={saveDraft} disabled={savingDraft || quoting} style={{
                padding: '8px 14px', borderRadius: 10,
                background: 'var(--accent)', color: 'var(--accent-ink)',
                fontSize: 13, fontWeight: 700,
                opacity: (savingDraft || quoting) ? 0.6 : 1,
              }}>{savingDraft ? 'Saving…' : 'Save change'}</button>
            </div>
          )}
        </div>

        {/* Tabs */}
        <div className="row" style={{ padding: '4px 16px 0', gap: 16, borderBottom: '1px solid var(--hairline-soft)' }}>
          {['overview', 'sales', 'sets'].map(t => (
            <button key={t} className="tap" onClick={() => setTab(t)} style={{
              padding: '10px 0',
              fontSize: 13, fontWeight: 600,
              color: tab === t ? 'var(--ink)' : 'var(--ink-3)',
              borderBottom: tab === t ? '2px solid var(--accent)' : '2px solid transparent',
              textTransform: 'capitalize', letterSpacing: '0.01em',
            }}>{t === 'sales' ? 'Sold listings' : t === 'sets' ? 'Variants' : 'Overview'}</button>
          ))}
        </div>

        {tab === 'overview' && <OverviewTab card={c} cur={cur} points={historyPoints} view={{ lang, condition, grading, grader, grade }} refreshing={refreshing} onRefresh={handleRefresh}/>}
        {tab === 'sales' && <SalesTab cur={cur} card={c} view={{ lang, condition, grading, grader, grade }}/>}
        {tab === 'sets' && <VariantsTab card={c} cur={cur} tweaks={tweaks} navigate={navigate}/>}
      </div>

      {/* Sticky actions — owned cards get Trade + Edit; candidates get Add. */}
      <div style={{
        flexShrink: 0,
        padding: '10px 16px calc(env(safe-area-inset-bottom, 0px) + 12px)',
        background: 'oklch(0.16 0.01 250 / 0.85)',
        backdropFilter: 'blur(20px)',
        borderTop: '1px solid var(--hairline-soft)',
        display: 'flex', gap: 8,
      }}>
        {isInCollection ? (
          <>
            <button className="tap" onClick={handleTrade} style={{
              flex: 1, height: 48, borderRadius: 14,
              background: 'var(--bg-2)', color: 'var(--ink)',
              fontWeight: 600, fontSize: 15,
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
            }}>
              <Icon name="trade" size={18} stroke={2}/> Trade
            </button>
            <button className="tap" onClick={openEdit} style={{
              flex: 2, height: 48, borderRadius: 14,
              background: 'var(--accent)', color: 'var(--accent-ink)',
              fontWeight: 600, fontSize: 15,
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
            }}>
              <Icon name="tag" size={18} stroke={2.2}/> Edit details
            </button>
          </>
        ) : (
          <>
            <button className="tap" style={{
              flex: 1, height: 48, borderRadius: 14,
              background: 'var(--bg-2)', color: 'var(--ink)',
              fontWeight: 500, fontSize: 15,
            }}>Watch</button>
            <button className="tap" onClick={handleAdd} disabled={adding} style={{
              flex: 2, height: 48, borderRadius: 14,
              background: 'var(--accent)', color: 'var(--accent-ink)',
              fontWeight: 600, fontSize: 15,
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
              opacity: adding ? 0.6 : 1,
            }}>
              <Icon name="plus" size={18} stroke={2.4}/> {adding ? 'Adding…' : 'Add to collection'}
            </button>
          </>
        )}
      </div>
    </div>
  );
}

// Scatter chart: each dot = one eBay sold listing, line = rolling average.
function SoldPriceChart({ sales, range, w = 358, h = 160 }) {
  const filtered = React.useMemo(() => {
    if (!Array.isArray(sales) || sales.length === 0) return [];
    const now = Date.now();
    const days = range === '1W' ? 7 : range === '1M' ? 30 : range === '3M' ? 90 : range === '1Y' ? 365 : null;
    const cutoff = days ? now - days * 86400000 : 0;
    return sales
      .filter(s => s.price_usd != null && s.sold_date)
      .map(s => ({ t: new Date(s.sold_date).getTime(), p: Number(s.price_usd) }))
      .filter(s => !isNaN(s.t) && s.t >= cutoff && s.p > 0)
      .sort((a, b) => a.t - b.t);
  }, [sales, range]);

  if (filtered.length === 0) return null;

  const pad = { top: 14, right: 8, bottom: 10, left: 8 };
  const cw = w - pad.left - pad.right;
  const ch = h - pad.top - pad.bottom;

  const prices = filtered.map(d => d.p);
  const minP = Math.min(...prices);
  const maxP = Math.max(...prices);
  const minT = filtered[0].t;
  const maxT = filtered[filtered.length - 1].t;
  // 5% vertical padding so dots aren't clipped at edges
  const rawSpan = maxP - minP;
  const spanP = (rawSpan > 0 ? rawSpan : maxP * 0.2 || 1) * 1.1;
  const baseP = minP - (rawSpan > 0 ? rawSpan * 0.05 : maxP * 0.01);
  const spanT = maxT - minT || 1;

  const toX = t => pad.left + ((t - minT) / spanT) * cw;
  const toY = p => pad.top + (1 - (p - baseP) / spanP) * ch;

  // Centered rolling average
  const WIN = Math.min(7, Math.max(3, Math.ceil(filtered.length / 4)));
  const rolling = filtered.map((_, i) => {
    const half = Math.floor(WIN / 2);
    const s = Math.max(0, Math.min(i - half, filtered.length - WIN));
    const e = Math.min(filtered.length, s + WIN);
    const slice = filtered.slice(s, e);
    return slice.reduce((acc, x) => acc + x.p, 0) / slice.length;
  });

  const linePath = filtered.map((pt, i) =>
    `${i === 0 ? 'M' : 'L'} ${toX(pt.t).toFixed(1)} ${toY(rolling[i]).toFixed(1)}`
  ).join(' ');

  return (
    <svg width={w} height={h} className="spark" style={{ display: 'block', overflow: 'visible' }}>
      {/* Subtle grid lines */}
      {[0.25, 0.5, 0.75].map(f => (
        <line key={f}
          x1={pad.left} y1={(pad.top + f * ch).toFixed(1)}
          x2={pad.left + cw} y2={(pad.top + f * ch).toFixed(1)}
          stroke="var(--hairline-soft)" strokeWidth={1}
        />
      ))}
      {/* Rolling average trendline */}
      {filtered.length >= 2 && (
        <path d={linePath} fill="none" stroke="var(--accent)"
          strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"/>
      )}
      {/* Sold price dots */}
      {filtered.map((pt, i) => (
        <circle key={i}
          cx={toX(pt.t).toFixed(1)} cy={toY(pt.p).toFixed(1)}
          r={3.5} fill="var(--accent)" fillOpacity={0.5}
          stroke="var(--accent)" strokeWidth={1} strokeOpacity={0.85}
        />
      ))}
    </svg>
  );
}

function OverviewTab({ card, cur, points, view, refreshing, onRefresh }) {
  const [range, setRange] = useStateDetail('1M');
  // Sold listings for the scatter chart: null = loading, [] = empty/error
  const [soldListings, setSoldListings] = useStateDetail(null);

  React.useEffect(() => {
    if (!card?.name || !window.api?.getSoldListings) { setSoldListings([]); return; }
    let cancelled = false;
    setSoldListings(null);
    (async () => {
      try {
        const isGraded = view?.grading === 'graded';
        const res = await window.api.getSoldListings(card, {
          lang:      view?.lang,
          condition: view?.condition,
          is_graded: isGraded,
          grader:    isGraded ? view?.grader : null,
          grade:     isGraded ? view?.grade  : null,
        });
        if (!cancelled) setSoldListings(Array.isArray(res?.sales) ? res.sales : []);
      } catch (_) {
        if (!cancelled) setSoldListings([]);
      }
    })();
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [card?.id, card?.name, view?.lang, view?.condition, view?.grading, view?.grader, view?.grade]);

  // Filter sold listings by selected range for min/max labels.
  // Listings without a parseable sold_date are placed at now (best-effort).
  const chartPts = React.useMemo(() => {
    if (!Array.isArray(soldListings) || soldListings.length === 0) return [];
    const now = Date.now();
    const days = range === '1W' ? 7 : range === '1M' ? 30 : range === '3M' ? 90 : range === '1Y' ? 365 : null;
    const cutoff = days ? now - days * 86400000 : 0;
    return soldListings
      .filter(s => s.price_usd != null && Number(s.price_usd) > 0)
      .map(s => {
        const raw = new Date(s.sold_date).getTime();
        return { t: isNaN(raw) ? now : raw, p: Number(s.price_usd) };
      })
      .filter(s => s.t >= cutoff);
  }, [soldListings, range]);

  // Fallback: slice historyPoints by range when sold listings are unavailable
  const historySeries = React.useMemo(() => {
    if (!Array.isArray(points) || points.length === 0) return null;
    const now = Date.now();
    const days = range === '1W' ? 7 : range === '1M' ? 30 : range === '3M' ? 90 : range === '1Y' ? 365 : null;
    const cutoff = days ? now - days * 86400000 : 0;
    const filtered = days ? points.filter(p => new Date(p.at).getTime() >= cutoff) : points;
    const used = filtered.length ? filtered : points.slice(-1);
    const nums = used.map(p => p.price);
    return nums.length === 1 ? [nums[0], nums[0]] : nums;
  }, [points, range]);

  const hasSoldData = chartPts.length > 0;
  const hasHistory = Array.isArray(historySeries) && historySeries.length >= 2;
  const hasSeries = hasSoldData || hasHistory;
  const chartPrices = hasSoldData ? chartPts.map(p => p.p) : (historySeries || []);
  const min = hasSeries ? Math.min(...chartPrices) : null;
  const max = hasSeries ? Math.max(...chartPrices) : null;
  // Real price-quote metadata. _quote is attached by api.refreshPrice when
  // the user refreshes during this session. price_source / source might be
  // stored on the backend row — check both shapes defensively.
  const quoteSource = card._quote?.source
    || card.raw?.price_source || card.raw?.source || card.raw?.last_source
    || null;
  const lastRefreshed = fmtAgo(card.last_priced_at);

  return (
    <div style={{ padding: '20px 16px 0' }}>
      {/* Chart — dots = sold listings + rolling avg line; falls back to price snapshot */}
      <div style={{ position: 'relative', height: 160, marginBottom: 6 }}>
        {soldListings === null && !hasHistory ? (
          <div style={{
            position: 'absolute', inset: 0,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            background: 'var(--bg-1)', borderRadius: 12, border: '1px dashed var(--hairline-soft)',
          }}>
            <div style={{ fontSize: 12, color: 'var(--ink-4)' }}>Loading…</div>
          </div>
        ) : hasSoldData ? (
          <>
            <SoldPriceChart sales={soldListings} range={range} w={358} h={160}/>
            <div className="mono" style={{ position: 'absolute', top: 4, right: 6, fontSize: 11, color: 'var(--ink-3)' }}>
              high {fmtUSD(max, { decimals: 0 })}
            </div>
            <div className="mono" style={{ position: 'absolute', bottom: 4, right: 6, fontSize: 11, color: 'var(--ink-3)' }}>
              low {fmtUSD(min, { decimals: 0 })}
            </div>
          </>
        ) : hasHistory ? (
          <>
            <Sparkline data={historySeries} w={358} h={160} stroke={1.6} fill={true} color="var(--accent)"/>
            <div className="mono" style={{ position: 'absolute', top: 4, right: 6, fontSize: 11, color: 'var(--ink-3)' }}>
              high {fmtUSD(max, { decimals: 0 })}
            </div>
            <div className="mono" style={{ position: 'absolute', bottom: 4, right: 6, fontSize: 11, color: 'var(--ink-3)' }}>
              low {fmtUSD(min, { decimals: 0 })}
            </div>
            <div className="mono" style={{ position: 'absolute', bottom: 4, left: 6, fontSize: 10, color: 'var(--ink-4)' }}>
              price snapshots
            </div>
          </>
        ) : (
          <div style={{
            position: 'absolute', inset: 0,
            display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
            gap: 6,
            background: 'var(--bg-1)', borderRadius: 12, border: '1px dashed var(--hairline-soft)',
          }}>
            <Icon name="sparkle" size={20} style={{ color: 'var(--ink-3)' }}/>
            <div style={{ fontSize: 12, color: 'var(--ink-3)', fontWeight: 500 }}>No price data yet</div>
            <div style={{ fontSize: 11, color: 'var(--ink-4)' }}>Refresh to fetch eBay sold data.</div>
          </div>
        )}
      </div>
      <div className="row" style={{ gap: 4, marginBottom: 22 }}>
        {['1W', '1M', '3M', '1Y', 'ALL'].map(r => (
          <button key={r} className="tap" onClick={() => setRange(r)} style={{
            flex: 1, padding: '6px 0', borderRadius: 8,
            background: range === r ? 'var(--bg-2)' : 'transparent',
            color: range === r ? 'var(--ink)' : 'var(--ink-3)',
            fontSize: 12, fontWeight: 600,
          }}>{r}</button>
        ))}
      </div>

      {/* Price snapshot — what we actually know about the current quote */}
      <div style={{ fontSize: 11, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 8 }}>Price snapshot</div>
      <div className="col" style={{ background: 'var(--bg-1)', borderRadius: 14, border: '1px solid var(--hairline-soft)', overflow: 'hidden' }}>
        <div className="row" style={{ padding: '12px 14px' }}>
          <div style={{ flex: 1, color: 'var(--ink-3)', fontSize: 13 }}>Current median</div>
          <Price usd={card.usd} currency={cur === 'BOTH' ? 'USD' : cur} size="sm"/>
        </div>
        <div className="row" style={{ padding: '12px 14px', borderTop: '1px solid var(--hairline-soft)' }}>
          <div style={{ flex: 1, color: 'var(--ink-3)', fontSize: 13 }}>Source</div>
          <div className="mono" style={{ fontSize: 13, color: quoteSource ? 'var(--ink)' : 'var(--ink-4)' }}>
            {quoteSource || '—'}
          </div>
        </div>
        <div className="row" style={{ padding: '12px 14px', borderTop: '1px solid var(--hairline-soft)' }}>
          <div style={{ flex: 1, color: 'var(--ink-3)', fontSize: 13 }}>Last refreshed</div>
          <div className="mono" style={{ fontSize: 13, color: lastRefreshed ? 'var(--ink)' : 'var(--ink-4)' }}>
            {lastRefreshed || 'never'}
          </div>
        </div>
        {onRefresh && (
          <button className="tap" onClick={onRefresh} disabled={refreshing} style={{
            padding: '12px 14px', borderTop: '1px solid var(--hairline-soft)',
            background: 'transparent', color: 'var(--accent)',
            fontSize: 13, fontWeight: 600, textAlign: 'center',
            opacity: refreshing ? 0.5 : 1,
          }}>{refreshing ? 'Refreshing…' : 'Refresh price now'}</button>
        )}
      </div>

      {/* Card meta — only fields we actually have */}
      <div style={{ fontSize: 11, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', margin: '22px 0 8px' }}>Card</div>
      <div className="col" style={{ background: 'var(--bg-1)', borderRadius: 14, border: '1px solid var(--hairline-soft)', overflow: 'hidden' }}>
        {(() => {
          const rows = [];
          if (card.set)     rows.push(['Set', card.set]);
          if (card.code)    rows.push(['Number', card.code]);
          if (card.variant) rows.push(['Rarity', card.variant]);
          if (card.hp)      rows.push(['HP', card.hp]);
          if (card.raw?.artist) rows.push(['Artist', card.raw.artist]);
          const release = card.raw?._set_release || card.raw?.set_release || card.raw?.released_at;
          if (release) {
            const d = new Date(release);
            rows.push(['Released', isNaN(d.getTime()) ? release : d.toLocaleDateString(undefined, { year: 'numeric', month: 'short' })]);
          }
          if (rows.length === 0) {
            return (
              <div style={{ padding: '14px', fontSize: 12, color: 'var(--ink-4)', textAlign: 'center' }}>
                No metadata loaded yet.
              </div>
            );
          }
          return rows.map(([k, v], i) => (
            <div key={k} className="row" style={{ padding: '12px 14px', borderTop: i === 0 ? 'none' : '1px solid var(--hairline-soft)' }}>
              <div style={{ flex: 1, color: 'var(--ink-3)', fontSize: 13 }}>{k}</div>
              <div className="mono" style={{ fontSize: 13 }}>{v}</div>
            </div>
          ));
        })()}
      </div>

      <div style={{ height: 100 }}/>
    </div>
  );
}

function SalesTab({ cur, card, view }) {
  const links = buildSourceLinks({ card, ...view });
  const isGraded = view?.grading === 'graded';
  // null = loading, [] = no relevant sales found, {sales,...} = fetched
  const [data, setData] = useStateDetail(null);
  const [error, setError] = useStateDetail(null);

  // Re-fetch whenever the card or grading context changes so the right comps
  // surface (PSA 10 ≠ PSA 8 ≠ raw NM).
  React.useEffect(() => {
    if (!card?.name || !window.api?.getSoldListings) {
      setData({ sales: [] });
      return;
    }
    let cancelled = false;
    setData(null);
    setError(null);
    (async () => {
      try {
        const res = await window.api.getSoldListings(card, {
          lang:      view?.lang,
          condition: view?.condition,
          is_graded: isGraded,
          grader:    isGraded ? view?.grader : null,
          grade:     isGraded ? view?.grade  : null,
        });
        if (cancelled) return;
        if (!res) {
          // Backend returned null (eBay 502 or anti-bot) — show empty + links.
          setData({ sales: [], note: 'eBay sold listings unavailable right now.' });
          return;
        }
        setData(res);
      } catch (e) {
        if (cancelled) return;
        setError(String(e.message || e));
        setData({ sales: [] });
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [card?.id, card?.name, card?.code, view?.lang, view?.condition, view?.grading, view?.grader, view?.grade]);

  const sales = data?.sales || [];
  const isLoading = data === null && !error;

  return (
    <div style={{ padding: '16px 16px 0' }}>
      {/* Section header with optional summary stats */}
      <div className="row" style={{ marginBottom: 8, alignItems: 'baseline', justifyContent: 'space-between' }}>
        <div style={{ fontSize: 11, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase' }}>
          Recent sold listings{data?.period_days ? ` · ${data.period_days}d` : ''}
        </div>
        {data?.median_usd != null && (
          <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>
            median {fmtUSD(data.median_usd, { decimals: 0 })} · n={data.sample_size}
          </div>
        )}
      </div>

      {/* Listings table */}
      <div className="col" style={{ background: 'var(--bg-1)', borderRadius: 14, border: '1px solid var(--hairline-soft)', overflow: 'hidden' }}>
        {isLoading && (
          <div style={{ padding: '24px 14px', fontSize: 13, color: 'var(--ink-3)', textAlign: 'center' }}>
            Fetching eBay sold listings…
          </div>
        )}
        {!isLoading && sales.length === 0 && (
          <div style={{ padding: '20px 14px', fontSize: 13, color: 'var(--ink-3)', textAlign: 'center' }}>
            {data?.note || (isGraded
              ? `No recent ${view?.grader} ${view?.grade} sales found on eBay.`
              : 'No recent sales found on eBay for this card.')}
          </div>
        )}
        {sales.map((s, i) => (
          s.url ? (
            <a key={i} href={s.url} target="_blank" rel="noopener noreferrer" style={{
              display: 'flex', alignItems: 'center', gap: 10,
              padding: '12px 14px',
              borderTop: i === 0 ? 'none' : '1px solid var(--hairline-soft)',
              color: 'inherit', textDecoration: 'none',
            }}>
              <SaleRow s={s} cur={cur}/>
            </a>
          ) : (
            <div key={i} className="row" style={{
              padding: '12px 14px',
              borderTop: i === 0 ? 'none' : '1px solid var(--hairline-soft)',
              gap: 10,
            }}>
              <SaleRow s={s} cur={cur}/>
            </div>
          )
        ))}
      </div>

      {data?.sold_url && (
        <a href={data.sold_url} target="_blank" rel="noopener noreferrer" style={{
          display: 'inline-flex', alignItems: 'center', gap: 4,
          marginTop: 10, fontSize: 12, fontWeight: 600,
          color: 'var(--accent)', textDecoration: 'none',
        }}>
          View all on eBay <Icon name="chevron-right" size={14} stroke={2}/>
        </a>
      )}

      {/* Other marketplaces (deep links — for cross-checking comps) */}
      {links.length > 1 && (
        <>
          <div style={{ fontSize: 11, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', margin: '22px 0 8px' }}>
            Other marketplaces
          </div>
          <div className="col" style={{ gap: 8 }}>
            {links.filter(L => L.key !== 'ebay').map(L => (
              <a key={L.key} href={L.url} target="_blank" rel="noopener noreferrer" style={{
                display: 'flex', alignItems: 'center', gap: 12,
                padding: '12px 14px', borderRadius: 14,
                background: 'var(--bg-1)', border: '1px solid var(--hairline-soft)',
                color: 'inherit', textDecoration: 'none',
              }}>
                <div className="col" style={{ flex: 1, minWidth: 0 }}>
                  <div className="row gap-2" style={{ alignItems: 'center' }}>
                    <span style={{ fontSize: 14, fontWeight: 600 }}>{L.label}</span>
                    <span className="chip" style={{ fontSize: 10, padding: '2px 6px' }}>{L.lang}</span>
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 2 }}>{L.sub}</div>
                </div>
                <Icon name="chevron-right" size={16} stroke={2} style={{ color: 'var(--accent)' }}/>
              </a>
            ))}
          </div>
        </>
      )}

      {error && (
        <div style={{ marginTop: 12, padding: 12, background: 'var(--bg-1)', borderRadius: 12, border: '1px solid var(--hairline-soft)', fontSize: 12, color: 'var(--ink-3)' }}>
          Couldn't load sold listings: {error.slice(0, 120)}
        </div>
      )}
      <div style={{ height: 100 }}/>
    </div>
  );
}

// Individual sold-listing row. Extracted so we can use it inside both an
// <a> wrapper (when the listing has a URL) and a plain <div> (when it doesn't).
function SaleRow({ s, cur }) {
  return (
    <>
      <div className="col" style={{ minWidth: 0, flex: 1 }}>
        <div style={{ fontSize: 13, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {s.title || 'eBay sale'}
        </div>
        <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 2 }}>
          {fmtAgo(s.sold_date) || s.sold_date} · {s.source || 'ebay'}
        </div>
      </div>
      <Price usd={s.price_usd} currency={cur === 'BOTH' ? 'USD' : cur} size="sm"/>
    </>
  );
}

function VariantsTab({ card, cur, tweaks, navigate }) {
  const [variants, setVariants] = useStateDetail(null); // null = loading, [] = none
  const [error, setError] = useStateDetail(null);

  React.useEffect(() => {
    if (!card?.name || !window.api?.searchPokemonTCG) {
      setVariants([]);
      return;
    }
    let cancelled = false;
    (async () => {
      setVariants(null);
      setError(null);
      try {
        const hits = await window.api.searchPokemonTCG(
          { name: card.name },
          { pageSize: 16 }
        );
        if (cancelled) return;
        // Dedup by (set + code) and drop the card we're already on.
        const seen = new Set();
        const list = [];
        for (const h of hits) {
          const key = `${h.set || ''}|${h.code || ''}`;
          if (seen.has(key)) continue;
          // Skip the currently-viewed print (best-effort match by code + set).
          if (card.code && card.set
              && String(h.code) === String(card.code)
              && (h.set || '').toLowerCase() === (card.set || '').toLowerCase()) {
            continue;
          }
          seen.add(key);
          list.push(h);
        }
        setVariants(list);
      } catch (e) {
        if (cancelled) return;
        setError(String(e.message || e));
        setVariants([]);
      }
    })();
    return () => { cancelled = true; };
  }, [card?.name]);

  if (variants === null) {
    return (
      <div style={{ padding: '40px 16px', textAlign: 'center', color: 'var(--ink-3)', fontSize: 13 }}>
        Loading other printings of {card.name || 'this card'}…
      </div>
    );
  }

  if (variants.length === 0) {
    return (
      <div style={{ padding: '32px 16px', textAlign: 'center' }}>
        <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--ink-2)' }}>
          No other printings found
        </div>
        <div style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: 4, lineHeight: 1.5 }}>
          {error ? error : `Pokemon TCG API didn't return additional prints of ${card.name}.`}
        </div>
        <div style={{ height: 100 }}/>
      </div>
    );
  }

  return (
    <div style={{ padding: '16px 16px 0' }}>
      <div style={{ fontSize: 11, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 10 }}>
        Other printings · {variants.length}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 14 }}>
        {variants.map((v) => (
          <button key={v.id} className="tap" onClick={() => navigate && navigate('detail', { card: v })} style={{
            display: 'flex', flexDirection: 'column', gap: 6,
            textAlign: 'left', background: 'transparent', padding: 0,
          }}>
            <CardArt card={v} renderMode={tweaks.cardRender} size="lg"/>
            <div style={{ fontSize: 12, fontWeight: 500, marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {v.set || '—'}
            </div>
            <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>
              {v.code}{v.variant ? ` · ${v.variant}` : ''}
            </div>
            <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
              <Price usd={v.usd} currency={cur === 'BOTH' ? 'USD' : cur} size="xs"/>
              {v.bulk && <span className="chip" style={{ fontSize: 9, padding: '1px 5px' }}>BULK</span>}
            </div>
          </button>
        ))}
      </div>
      <div style={{ height: 100 }}/>
    </div>
  );
}

window.DetailScreen = DetailScreen;
