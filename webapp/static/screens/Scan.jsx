/* Scan — real photo+search identify flow.
 * Visual style preserved from the original camera UI; pipeline log now reflects
 * actual API stages. Tapping the shutter opens a file picker (camera on mobile);
 * the typed search input hits /identify with a query. Results land in scanQueue
 * which the Bulk screen flushes into the real backend on commit.
 */

const { useState: useStateScan, useEffect: useEffectScan, useRef: useRefScan } = React;

function ScanScreen({ tweaks, navigate, scanQueue, setScanQueue, identifyCard, addToCollection, backend }) {
  const [phase, setPhase] = useStateScan('idle');         // idle | scanning | result
  const [flash, setFlash] = useStateScan(false);
  const [pipelineLog, setPipelineLog] = useStateScan([]);
  const [query, setQuery] = useStateScan('');
  const [candidates, setCandidates] = useStateScan([]);
  const [error, setError] = useStateScan(null);
  // Blob URL of the user's captured photo — used as a fallback "this is the
  // actual card you scanned" image when no catalogued image exists for the
  // selected printing.
  const [capturedPhotoUrl, setCapturedPhotoUrl] = useStateScan(null);
  const [capturedPhotoFile, setCapturedPhotoFile] = useStateScan(null);
  const fileInputRef = useRefScan(null);

  const log = (label, detail, state = 'ok') =>
    setPipelineLog(prev => [...prev, { label, detail, state }]);

  const runIdentify = async ({ image }) => {
    setPhase('scanning');
    setPipelineLog([]);
    setCandidates([]);
    setError(null);
    // Stash the captured photo so we can use it as a fallback image / save
    // it to the card's gallery on Add to cart.
    if (capturedPhotoUrl) URL.revokeObjectURL(capturedPhotoUrl);
    if (image) {
      setCapturedPhotoFile(image);
      setCapturedPhotoUrl(URL.createObjectURL(image));
    } else {
      setCapturedPhotoFile(null);
      setCapturedPhotoUrl(null);
    }
    log(image ? 'Photo captured' : 'Query submitted', image ? `${(image.size/1024).toFixed(0)} KB` : `"${query.slice(0, 24)}"`);
    try {
      log('Identifying', backend?.online === false ? 'demo mode' : 'POST /identify');
      const found = identifyCard
        ? await identifyCard({ query, image })
        : [];
      if (!found || found.length === 0) {
        log('No matches', 'try a different query', 'miss');
        setError('No candidates returned by /api/identify.');
        setPhase('idle');
        return;
      }
      log('Pricing fan-out', `${found.length} candidate${found.length === 1 ? '' : 's'}`);

      // Widen the candidate set across BOTH Pokemon TCG API and TCGdex
      // (EN + JA). TCGdex covers promos and Japanese sets that Pokemon TCG
      // API doesn't have. We run the queries in parallel, then de-dupe.
      const seedName = (found[0]?.name || query || '').trim();
      const widened = [...found];
      const seen = new Set(found.map(c => c.id));

      if (seedName && window.api) {
        log('Widening search', `"${seedName.slice(0, 24)}"`);

        // Translate the Pokemon name to JA + ZH so we can query TCGdex's
        // localized databases (which use Japanese / Chinese names, not EN).
        let names = null;
        try {
          if (window.api.lookupPokemonNames) {
            names = await window.api.lookupPokemonNames(seedName);
            if (names) log('Names', `ja:${names.ja || '—'} · zh:${names.zh || '—'}`);
          }
        } catch (_) {}

        const tasks = [];
        if (window.api.searchPokemonTCG) tasks.push(['Pokemon TCG', window.api.searchPokemonTCG({ name: seedName }, { pageSize: 20 })]);
        if (window.api.searchTCGdex)     tasks.push(['TCGdex EN',   window.api.searchTCGdex({ name: seedName }, { pageSize: 20, lang: 'en' })]);
        if (window.api.searchTCGdex && names?.ja) tasks.push(['TCGdex JA',   window.api.searchTCGdex({ name: names.ja }, { pageSize: 15, lang: 'ja', dbLang: 'ja' })]);
        if (window.api.searchTCGdex && names?.zh) tasks.push(['TCGdex ZH-TW', window.api.searchTCGdex({ name: names.zh }, { pageSize: 15, lang: 'ch', dbLang: 'zh-tw' })]);
        if (window.api.searchTCGdex && names?.zh) tasks.push(['TCGdex ZH-CN', window.api.searchTCGdex({ name: names.zh }, { pageSize: 15, lang: 'ch', dbLang: 'zh-cn' })]);

        const results = await Promise.allSettled(tasks.map(([, p]) => p));
        results.forEach((r, i) => {
          const label = tasks[i][0];
          const hits = (r.status === 'fulfilled' && Array.isArray(r.value)) ? r.value : [];
          log(label, `${hits.length} hit${hits.length === 1 ? '' : 's'}`);
          for (const h of hits) {
            if (!seen.has(h.id)) { widened.push(h); seen.add(h.id); }
          }
        });

        // Image backfill — same-language only.
        // JP/CH cards without their own TCGdex art show a placeholder —
        // applying EN card art to a JP result is misleading because the
        // EN and JP printings look different (different set text, card
        // back, language). Users need to see the correct language card.
        const baseNameOf = (c) => (c.name || '').replace(/[\s]*(ex|EX|V|VMAX|GX|VSTAR|VUNION)$/i, '').trim().toLowerCase();
        const sameLangMap = new Map();   // "LANG|name" → image_url
        for (const c of widened) {
          if (!c.image_url) continue;
          const lang = c.lang || 'EN';
          const bn   = baseNameOf(c);
          const lk   = `${lang}|${bn}`;
          if (!sameLangMap.has(lk)) sameLangMap.set(lk, c.image_url);
        }
        for (const c of widened) {
          if (c.image_url) continue;
          const lang = c.lang || 'EN';
          const bn   = baseNameOf(c);
          const lk   = `${lang}|${bn}`;
          if (sameLangMap.has(lk)) { c.image_url = sameLangMap.get(lk); }
        }
        // Cross-language fallback (last resort): for JP/CH cards that still
        // have no image, borrow from EN. This covers vintage cards where
        // TCGdex JA has no scan but the Pokémon illustration is identical.
        // Modern SV JP cards come from TCGdex JA with their own art, so they
        // already have images and skip this loop entirely.
        const anyLangMap = new Map();
        for (const c of widened) {
          if (!c.image_url) continue;
          const bn = baseNameOf(c);
          const prev = anyLangMap.get(bn);
          // Prefer assets.tcgdex.net — normalizeCard won't strip it for JP/CH.
          // images.pokemontcg.io is EN-only and gets stripped by normalizeCard.
          const isBetter = !prev || (c.image_url.includes('assets.tcgdex.net') && !prev.includes('assets.tcgdex.net'));
          if (isBetter) anyLangMap.set(bn, c.image_url);
        }
        for (const c of widened) {
          if (c.image_url) continue;
          const bn = baseNameOf(c);
          if (anyLangMap.has(bn)) c.image_url = anyLangMap.get(bn);
        }

        // Set-aware JP sort: when the query names a JP set, float JP results
        // from that set to the top so the user sees the correct printing first.
        const JP_SET_ALIASES = {
          "terastal festival": "SV8a", "terastal fes": "SV8a",
          "crimson haze": "SV5a",
          "paradise dragona": "SV8",
          "stellar miracle": "SV7", "super electric breaker": "SV7a",
          "shiny treasure": "SV4a",
          "wild force": "SV5K", "cyber judge": "SV5M",
          "mask of change": "SV6",
          "battle partners": "SV9a",
          "151": "SV2a", "pokemon 151": "SV2a",
          "glory of team rocket": "SV10", "rocket gang": "SV10",
        };
        const queryLc = (query + ' ' + seedName).toLowerCase();
        let jpSetId = null;
        for (const [alias, id] of Object.entries(JP_SET_ALIASES)) {
          if (queryLc.includes(alias)) { jpSetId = id; break; }
        }
        if (jpSetId) {
          widened.sort((a, b) => {
            // _set_id lives on the raw normalizeCard input (e.g. "SV8a" from TCGdex)
            const aIsTarget = a.lang === 'JP' && a.raw?._set_id === jpSetId;
            const bIsTarget = b.lang === 'JP' && b.raw?._set_id === jpSetId;
            if (aIsTarget && !bIsTarget) return -1;
            if (!aIsTarget && bIsTarget) return 1;
            return 0;
          });
        }
      }

      setCandidates(widened);
      setPhase('result');
    } catch (e) {
      log('Identify failed', String(e.message || e).slice(0, 80), 'miss');
      setError(String(e.message || e));
      setPhase('idle');
    }
  };

  const handleSearch = () => {
    if (!query.trim()) return;
    runIdentify({});
  };

  const onFile = (e) => {
    const f = e.target.files && e.target.files[0];
    if (!f) return;
    runIdentify({ image: f });
    e.target.value = '';
  };

  const triggerCapture = () => {
    if (fileInputRef.current) fileInputRef.current.click();
  };

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

  // Wishlist path — writes the card straight to the backend tagged
  // `wishlist`, bypassing the scan cart (since the user doesn't own this
  // card yet). purchase_price is null so it doesn't pollute gain/loss
  // calculations. Browse hides wishlist cards from default views and
  // surfaces them under the Wishlist filter chip.
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

  const handleSkip = () => {
    setPhase('idle');
    setPipelineLog([]);
    setCandidates([]);
  };

  return (
    <div className="screen" style={{ background: '#000' }}>
      {/* Hidden file input — triggered by shutter or "From library" */}
      <input ref={fileInputRef} type="file" accept="image/*" capture="environment" onChange={onFile} style={{ display: 'none' }}/>

      {/* Camera viewfinder */}
      <div style={{
        position: 'absolute', inset: 0,
        background: 'radial-gradient(ellipse at center, oklch(0.18 0.01 260) 0%, oklch(0.05 0 0) 100%)',
        overflow: 'hidden',
      }}>
        <div style={{
          position: 'absolute', inset: '-20% -20% 0 -20%',
          background: 'radial-gradient(ellipse at 50% 80%, oklch(0.28 0.02 250 / 0.6), transparent 60%)',
        }}/>
        <div style={{
          position: 'absolute',
          left: '50%', top: '50%', transform: 'translate(-50%, -50%)',
          width: 240, aspectRatio: '5/7', borderRadius: 14,
          background: 'linear-gradient(135deg, oklch(0.30 0.04 260), oklch(0.18 0.02 250))',
          boxShadow: '0 30px 80px -20px oklch(0 0 0 / 0.8)',
        }}>
          <div style={{
            position: 'absolute', inset: '8% 8% 22%',
            borderRadius: 6,
            background: 'linear-gradient(160deg, oklch(0.50 0.10 30) 0%, oklch(0.28 0.06 30) 100%)',
            opacity: 0.85,
          }}/>
          <div style={{
            position: 'absolute', inset: 0,
            background: 'linear-gradient(115deg, transparent 40%, oklch(1 0 0 / 0.06) 50%, transparent 60%)',
          }}/>
        </div>
      </div>

      {/* Top chrome */}
      <div style={{ position: 'relative', zIndex: 2, padding: '14px 16px' }}>
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <button className="tap" onClick={() => navigate('home')} style={{
            width: 36, height: 36, borderRadius: 18,
            background: 'oklch(0 0 0 / 0.5)', backdropFilter: 'blur(12px)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'oklch(1 0 0 / 0.95)',
          }}>
            <Icon name="x" size={20}/>
          </button>
          <div style={{
            padding: '6px 12px', borderRadius: 999,
            background: 'oklch(0 0 0 / 0.5)', backdropFilter: 'blur(12px)',
            color: 'oklch(1 0 0 / 0.95)', fontSize: 12, fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase',
          }}>
            Scan · {backend?.online === false ? 'Demo' : 'Live'}
          </div>
          <button className="tap" onClick={() => setFlash(f => !f)} style={{
            width: 36, height: 36, borderRadius: 18,
            background: flash ? 'oklch(0.85 0.14 80)' : 'oklch(0 0 0 / 0.5)', backdropFilter: 'blur(12px)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            color: flash ? 'oklch(0.18 0.05 80)' : 'oklch(1 0 0 / 0.95)',
          }}>
            <Icon name={flash ? 'flash-on' : 'flash'} size={18}/>
          </button>
        </div>

        {/* Search field — alternative path to identify by name */}
        <div style={{ marginTop: 12 }}>
          <div className="row gap-2" style={{
            background: 'oklch(0 0 0 / 0.5)', backdropFilter: 'blur(12px)',
            borderRadius: 12, padding: '8px 12px', border: '1px solid oklch(1 0 0 / 0.1)',
          }}>
            <Icon name="search" size={16} style={{ color: 'oklch(1 0 0 / 0.6)' }}/>
            <input
              value={query}
              onChange={e => setQuery(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') handleSearch(); }}
              placeholder="Search card name or number"
              style={{
                flex: 1, background: 'transparent', border: 0, outline: 'none',
                color: 'oklch(1 0 0 / 0.95)', fontSize: 14,
              }}
            />
            {query && (
              <button className="tap" onClick={handleSearch} style={{
                padding: '4px 10px', borderRadius: 8,
                background: 'var(--accent)', color: 'var(--accent-ink)',
                fontSize: 12, fontWeight: 600,
              }}>Find</button>
            )}
          </div>
        </div>
      </div>

      {/* Reticle */}
      {phase !== 'result' && (
        <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', pointerEvents: 'none' }}>
          <div style={{
            position: 'relative',
            width: 280, aspectRatio: '5/7', borderRadius: 18,
          }}>
            {[0,1,2,3].map(i => {
              const positions = [
                { top: 0, left: 0, borderTop: '2px solid', borderLeft: '2px solid' },
                { top: 0, right: 0, borderTop: '2px solid', borderRight: '2px solid' },
                { bottom: 0, left: 0, borderBottom: '2px solid', borderLeft: '2px solid' },
                { bottom: 0, right: 0, borderBottom: '2px solid', borderRight: '2px solid' },
              ];
              return <div key={i} style={{
                position: 'absolute', width: 28, height: 28, borderRadius: 6,
                borderColor: 'oklch(0.85 0.14 200)',
                ...positions[i],
              }}/>
            })}
            {phase === 'scanning' && (
              <div style={{
                position: 'absolute', left: 0, right: 0, height: 2,
                background: 'linear-gradient(90deg, transparent, oklch(0.85 0.14 200), transparent)',
                boxShadow: '0 0 18px oklch(0.85 0.14 200 / 0.7)',
                animation: 'scanLine 1.4s ease-in-out infinite',
                top: 0,
              }}/>
            )}
          </div>
        </div>
      )}

      {/* Pipeline diagnostics overlay */}
      {tweaks.showDiagnostics && (phase === 'scanning' || error) && (
        <div style={{
          position: 'absolute', left: 16, right: 16, top: 130,
          background: 'oklch(0 0 0 / 0.7)', backdropFilter: 'blur(12px)',
          borderRadius: 12, padding: 10,
          border: '1px solid oklch(1 0 0 / 0.1)',
          color: 'oklch(1 0 0 / 0.9)', fontFamily: 'var(--mono)', fontSize: 11,
          zIndex: 3,
        }}>
          <div style={{ fontSize: 10, opacity: 0.6, letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 6 }}>Pipeline</div>
          {pipelineLog.map((s, i) => (
            <div key={i} className="row gap-2" style={{ padding: '2px 0' }}>
              <span style={{
                width: 10, height: 10, borderRadius: 5, flexShrink: 0,
                background: s.state === 'ok' ? 'oklch(0.78 0.14 155)' : s.state === 'miss' ? 'oklch(0.82 0.14 80)' : 'oklch(0.6 0.02 250)',
              }}/>
              <span style={{ flex: 1, opacity: 0.9 }}>{s.label}</span>
              <span style={{ opacity: 0.6 }}>{s.detail}</span>
            </div>
          ))}
          {error && (
            <div className="row gap-2" style={{ padding: '4px 0', color: 'oklch(0.82 0.14 30)' }}>
              <span style={{ width: 10, height: 10, borderRadius: 5, background: 'oklch(0.65 0.18 30)' }}/>
              <span style={{ flex: 1 }}>{error.slice(0, 80)}</span>
            </div>
          )}
        </div>
      )}

      {/* Scan queue chip */}
      {scanQueue.length > 0 && phase === 'idle' && (
        <button className="tap" onClick={() => navigate('bulk')} style={{
          position: 'absolute', left: '50%', transform: 'translateX(-50%)',
          bottom: 188,
          padding: '8px 12px 8px 8px', borderRadius: 999,
          background: 'oklch(0.85 0.14 200)',
          color: 'oklch(0.16 0.05 200)',
          display: 'flex', alignItems: 'center', gap: 8,
          fontWeight: 600, fontSize: 13,
          boxShadow: '0 8px 24px oklch(0.85 0.14 200 / 0.4)',
        }}>
          <span style={{
            background: 'oklch(0.16 0.05 200)', color: 'oklch(0.85 0.14 200)',
            width: 24, height: 24, borderRadius: 12,
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 12,
          }}>{scanQueue.length}</span>
          Review scan cart
          <Icon name="chevron-right" size={16}/>
        </button>
      )}

      {/* Bottom controls */}
      <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, padding: '0 0 calc(env(safe-area-inset-bottom, 0px) + 24px)' }}>
        <div className="row" style={{ justifyContent: 'space-around', alignItems: 'center', padding: '16px 32px' }}>
          <button className="tap col" onClick={triggerCapture} style={{ alignItems: 'center', gap: 4, color: 'oklch(1 0 0 / 0.85)' }}>
            <div style={{ width: 44, height: 44, borderRadius: 22, background: 'oklch(0 0 0 / 0.45)', backdropFilter: 'blur(12px)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <Icon name="gallery" size={20}/>
            </div>
            <span style={{ fontSize: 10, fontWeight: 500, opacity: 0.8 }}>Library</span>
          </button>
          <button onClick={triggerCapture} disabled={phase === 'scanning'} style={{
            width: 76, height: 76, borderRadius: 38,
            background: 'oklch(1 0 0 / 0.95)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            position: 'relative',
            transition: 'transform 0.1s',
            transform: phase === 'scanning' ? 'scale(0.92)' : 'scale(1)',
          }}>
            <div className="foil" style={{
              width: 60, height: 60, borderRadius: 30,
              animation: phase === 'scanning' ? 'foilRot 1.4s linear infinite' : 'foilRot 8s linear infinite',
            }}/>
          </button>
          <button className="tap col" onClick={() => navigate('bulk')} style={{ alignItems: 'center', gap: 4, color: 'oklch(1 0 0 / 0.85)' }}>
            <div style={{ width: 44, height: 44, borderRadius: 22, background: 'oklch(0 0 0 / 0.45)', backdropFilter: 'blur(12px)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <Icon name="binders" size={20}/>
            </div>
            <span style={{ fontSize: 10, fontWeight: 500, opacity: 0.8 }}>Cart · {scanQueue.length}</span>
          </button>
        </div>
      </div>

      {/* Result sheet */}
      {phase === 'result' && candidates.length > 0 && (
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
      )}

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
    </div>
  );
}

// Price multipliers for editor selectors (kept in sync with Detail.jsx).
const CONDITIONS = ['NM', 'LP', 'MP', 'HP', 'DMG'];
const CONDITION_MULT = { NM: 1, LP: 0.78, MP: 0.55, HP: 0.34, DMG: 0.20 };
const LANG_MULT      = { EN: 1, JP: 0.86, CH: 0.78 };
const GRADERS = ['Raw', 'PSA', 'CGC', 'BGS', 'SGC'];
// Grade multipliers — averaged across major graders. Raw = base price.
const GRADE_MULT = { 10: 4.0, 9.5: 2.4, 9: 1.6, 8.5: 1.2, 8: 1.0, 7: 0.75, 6: 0.55, 5: 0.4, 4: 0.3, 3: 0.22, 2: 0.18, 1: 0.15 };

function ScanResultSheet({ candidates, tweaks, capturedPhotoUrl, capturedPhotoFile, onAdd, onAddWishlist, onSkip, onDetail }) {
  const cur = tweaks.currency;
  const [picked, setPicked] = useStateScan(0);
  const [setFilter, setSetFilter] = useStateScan('');
  const [rarityFilter, setRarityFilter] = useStateScan('');
  const [langFilter, setLangFilter] = useStateScan('');
  // --- Edit-details state for the selected candidate ---
  const [condition, setCondition] = useStateScan('NM');
  const [lang, setLang]           = useStateScan('EN');
  const [grader, setGrader]       = useStateScan('Raw');
  const [grade, setGrade]         = useStateScan(10);
  // Manual price override — when user fills this in from eBay sold listings,
  // it bypasses the auto-computed adjusted price.
  const [manualPrice, setManualPrice] = useStateScan('');
  // What the user actually paid for this card (cost basis, separate from
  // market value). Used for gain/loss tracking on Home/Detail.
  const [purchasePrice, setPurchasePrice] = useStateScan('');
  // Comma-separated user tags (e.g. "favorite, trade-bait, sleeve").
  const [tagsInput, setTagsInput] = useStateScan('');

  // Build filter option lists from the candidate set.
  const uniq = (arr) => Array.from(new Set(arr.filter(Boolean)));
  const setOptions    = uniq(candidates.map(c => c.set));
  const rarityOptions = uniq(candidates.map(c => c.variant || c._rarity));

  const filtered = candidates.filter(c => {
    if (setFilter    && c.set                  !== setFilter)    return false;
    if (rarityFilter && (c.variant || c._rarity) !== rarityFilter) return false;
    if (langFilter   && (c.lang || 'EN')         !== langFilter)   return false;
    return true;
  });
  // Always present the language options so user can pick even when only one
  // is available (it's a guarantee the engine looked across all three).
  const langOptions = ['EN', 'JP', 'CH'];
  // Clamp picked into bounds whenever filters change.
  const safePicked = Math.min(picked, Math.max(0, filtered.length - 1));
  const card = filtered[safePicked] || filtered[0] || candidates[0];

  // When the picked candidate has a non-EN print language, default the
  // editor's LANG to match so the adjusted price comes out right.
  React.useEffect(() => {
    if (!card) return;
    if (card.lang === 'JP') setLang('JP');
    else if (card.lang === 'CH') setLang('CH');
    setManualPrice(''); // clear override when picking a different candidate
    setPurchasePrice('');
    setTagsInput('');
  }, [card?.id, card?.lang]);

  const isGraded = grader !== 'Raw';
  const hasBase  = card?.usd != null && Number.isFinite(Number(card.usd));
  const baseUSD  = hasBase ? Number(card.usd) : null;
  const autoAdj  = hasBase
    ? baseUSD
      * (CONDITION_MULT[isGraded ? 'NM' : condition] || 1)
      * (LANG_MULT[lang] || 1)
      * (isGraded ? (GRADE_MULT[grade] || 1) : 1)
    : null;
  // Manual override wins if provided.
  const manualNum = Number(manualPrice);
  const useManual = manualPrice !== '' && Number.isFinite(manualNum) && manualNum >= 0;
  const adjUSD    = useManual ? manualNum : autoAdj;

  const Chip = ({ active, onClick, children }) => (
    <button className="tap" onClick={onClick} style={{
      padding: '5px 10px', borderRadius: 999, flexShrink: 0,
      background: active ? 'var(--ink)' : 'var(--bg-2)',
      color: active ? 'var(--bg)' : 'var(--ink-2)',
      fontSize: 11, fontWeight: 600,
      whiteSpace: 'nowrap',
      border: '1px solid var(--hairline-soft)',
    }}>{children}</button>
  );

  return (
    <>
      <div style={{
        position: 'absolute', inset: 0, background: 'oklch(0 0 0 / 0.5)',
        animation: 'fadeIn 0.2s', zIndex: 5,
      }} onClick={onSkip}/>
      <div style={{
        position: 'absolute', left: 0, right: 0, bottom: 0,
        background: 'var(--bg)',
        borderTopLeftRadius: 24, borderTopRightRadius: 24,
        padding: '12px 16px calc(env(safe-area-inset-bottom, 0px) + 20px)',
        animation: 'riseIn 0.3s ease-out',
        zIndex: 6,
        boxShadow: '0 -20px 60px oklch(0 0 0 / 0.5)',
        maxHeight: '85%',
        display: 'flex', flexDirection: 'column',
      }}>
        <div style={{ width: 36, height: 4, borderRadius: 2, background: 'var(--ink-4)', margin: '4px auto 14px' }}/>

        <div className="row gap-2" style={{ marginBottom: 10, color: 'var(--pos)' }}>
          <Icon name="check" size={16} stroke={2.4}/>
          <span style={{ fontSize: 12, fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase' }}>
            {filtered.length} of {candidates.length} match{candidates.length === 1 ? '' : 'es'}
          </span>
        </div>

        {/* Filter chips */}
        <div className="col gap-2" style={{ marginBottom: 10 }}>
          {/* LANG row — always shown so user can switch between EN/JP/CH */}
          <div className="row" style={{ gap: 6, overflowX: 'auto', paddingBottom: 4, scrollbarWidth: 'none' }}>
            <div style={{ fontSize: 11, color: 'var(--ink-3)', alignSelf: 'center', flexShrink: 0, marginRight: 4 }}>LANG</div>
            <Chip active={!langFilter} onClick={() => setLangFilter('')}>All</Chip>
            {langOptions.map(L => {
              const n = candidates.filter(c => (c.lang || 'EN') === L).length;
              return (
                <Chip key={L} active={langFilter === L} onClick={() => setLangFilter(L === langFilter ? '' : L)}>
                  {L}{n > 0 ? ` · ${n}` : ''}
                </Chip>
              );
            })}
          </div>
        </div>
        {(setOptions.length > 1 || rarityOptions.length > 1) && (
          <div className="col gap-2" style={{ marginBottom: 10 }}>
            {setOptions.length > 1 && (
              <div className="row" style={{ gap: 6, overflowX: 'auto', paddingBottom: 4, scrollbarWidth: 'none' }}>
                <div style={{ fontSize: 11, color: 'var(--ink-3)', alignSelf: 'center', flexShrink: 0, marginRight: 4 }}>SET</div>
                <Chip active={!setFilter} onClick={() => setSetFilter('')}>All</Chip>
                {setOptions.map(s => (
                  <Chip key={s} active={setFilter === s} onClick={() => setSetFilter(s === setFilter ? '' : s)}>{s}</Chip>
                ))}
              </div>
            )}
            {rarityOptions.length > 1 && (
              <div className="row" style={{ gap: 6, overflowX: 'auto', paddingBottom: 4, scrollbarWidth: 'none' }}>
                <div style={{ fontSize: 11, color: 'var(--ink-3)', alignSelf: 'center', flexShrink: 0, marginRight: 4 }}>RARITY</div>
                <Chip active={!rarityFilter} onClick={() => setRarityFilter('')}>All</Chip>
                {rarityOptions.map(r => (
                  <Chip key={r} active={rarityFilter === r} onClick={() => setRarityFilter(r === rarityFilter ? '' : r)}>{r}</Chip>
                ))}
              </div>
            )}
          </div>
        )}

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
                    <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {cand.code}{cand.set ? ` · ${cand.set}` : ''}
                    </div>
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

        {/* Edit details + adjusted price */}
        {card && (
          <div style={{
            marginTop: 12, padding: 10, borderRadius: 14,
            background: 'var(--bg-1)', border: '1px solid var(--hairline-soft)',
            display: 'flex', flexDirection: 'column', gap: 8,
          }}>
            {/* Language */}
            <div className="row" style={{ gap: 6, alignItems: 'center' }}>
              <div style={{ width: 56, fontSize: 10, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.05em' }}>LANG</div>
              <div style={{ display: 'flex', gap: 4, flex: 1 }}>
                {['EN', 'JP', 'CH'].map(L => (
                  <button key={L} className="tap" onClick={() => setLang(L)} style={{
                    flex: 1, padding: '6px 0', borderRadius: 8,
                    background: lang === L ? 'var(--bg-3)' : 'var(--bg-2)',
                    color: lang === L ? 'var(--ink)' : 'var(--ink-3)',
                    fontWeight: 600, fontSize: 12, border: lang === L ? '1px solid var(--accent)' : '1px solid transparent',
                  }}>{L}</button>
                ))}
              </div>
            </div>
            {/* Condition (only when raw) */}
            {!isGraded && (
              <div className="row" style={{ gap: 6, alignItems: 'center' }}>
                <div style={{ width: 56, fontSize: 10, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.05em' }}>COND</div>
                <div style={{ display: 'flex', gap: 4, flex: 1 }}>
                  {CONDITIONS.map(C => (
                    <button key={C} className="tap" onClick={() => setCondition(C)} style={{
                      flex: 1, padding: '6px 0', borderRadius: 8,
                      background: condition === C ? 'var(--bg-3)' : 'var(--bg-2)',
                      color: condition === C ? 'var(--ink)' : 'var(--ink-3)',
                      fontWeight: 600, fontSize: 11, border: condition === C ? '1px solid var(--accent)' : '1px solid transparent',
                    }}>{C}</button>
                  ))}
                </div>
              </div>
            )}
            {/* Grader */}
            <div className="row" style={{ gap: 6, alignItems: 'center' }}>
              <div style={{ width: 56, fontSize: 10, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.05em' }}>GRADE</div>
              <div style={{ display: 'flex', gap: 4, flex: 1 }}>
                {GRADERS.map(G => (
                  <button key={G} className="tap" onClick={() => setGrader(G)} style={{
                    flex: 1, padding: '6px 0', borderRadius: 8,
                    background: grader === G ? 'var(--bg-3)' : 'var(--bg-2)',
                    color: grader === G ? 'var(--ink)' : 'var(--ink-3)',
                    fontWeight: 600, fontSize: 11, border: grader === G ? '1px solid var(--accent)' : '1px solid transparent',
                  }}>{G}</button>
                ))}
              </div>
            </div>
            {/* Grade number (only if graded) */}
            {isGraded && (
              <div className="row" style={{ gap: 6, alignItems: 'center' }}>
                <div style={{ width: 56, fontSize: 10, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.05em' }}>GRD #</div>
                <div style={{ display: 'flex', gap: 4, flex: 1, overflowX: 'auto', scrollbarWidth: 'none' }}>
                  {[10, 9.5, 9, 8.5, 8, 7, 6, 5, 4, 3, 2, 1].map(n => (
                    <button key={n} className="tap" onClick={() => setGrade(n)} style={{
                      flexShrink: 0, minWidth: 36, padding: '6px 8px', borderRadius: 8,
                      background: grade === n ? 'var(--bg-3)' : 'var(--bg-2)',
                      color: grade === n ? 'var(--ink)' : 'var(--ink-3)',
                      fontWeight: 600, fontSize: 11, border: grade === n ? '1px solid var(--accent)' : '1px solid transparent',
                    }}>{n}</button>
                  ))}
                </div>
              </div>
            )}
            {/* Manual price override + eBay lookup */}
            <div className="row" style={{ gap: 6, alignItems: 'center' }}>
              <div style={{ width: 56, fontSize: 10, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.05em' }}>PRICE $</div>
              <input
                type="number"
                inputMode="decimal"
                step="0.01"
                min="0"
                value={manualPrice}
                onChange={e => setManualPrice(e.target.value)}
                placeholder={hasBase ? `auto · ${fmtUSD(autoAdj || 0, { decimals: 2 }).replace('$','')}` : 'enter from eBay'}
                style={{
                  flex: 1, padding: '7px 10px', borderRadius: 8,
                  background: 'var(--bg-2)', color: 'var(--ink)',
                  border: `1px solid ${useManual ? 'var(--accent)' : 'transparent'}`,
                  fontSize: 13, fontWeight: 600, fontFamily: 'var(--mono)',
                  outline: 'none', minWidth: 0,
                }}
              />
              <button className="tap" onClick={() => {
                const url = window.api?.buildEbayUrl?.(card, { grader: isGraded ? grader : null, grade: isGraded ? grade : null });
                if (url) window.open(url, '_blank', 'noopener,noreferrer');
              }} style={{
                flexShrink: 0, padding: '7px 10px', borderRadius: 8,
                background: 'var(--bg-2)', color: 'var(--ink-2)',
                fontSize: 11, fontWeight: 600, border: '1px solid var(--hairline-soft)',
                display: 'inline-flex', alignItems: 'center', gap: 4,
              }} title="Open eBay sold-listings in a new tab">
                eBay ↗
              </button>
            </div>

            {/* Paid (cost basis) */}
            <div className="row" style={{ gap: 6, alignItems: 'center' }}>
              <div style={{ width: 56, fontSize: 10, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.05em' }}>PAID $</div>
              <input
                type="number"
                inputMode="decimal"
                step="0.01"
                min="0"
                value={purchasePrice}
                onChange={e => setPurchasePrice(e.target.value)}
                placeholder="what you paid (optional)"
                style={{
                  flex: 1, padding: '7px 10px', borderRadius: 8,
                  background: 'var(--bg-2)', color: 'var(--ink)',
                  border: '1px solid transparent',
                  fontSize: 13, fontWeight: 600, fontFamily: 'var(--mono)',
                  outline: 'none', minWidth: 0,
                }}
              />
            </div>

            {/* Tags */}
            <div className="row" style={{ gap: 6, alignItems: 'center' }}>
              <div style={{ width: 56, fontSize: 10, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.05em' }}>TAGS</div>
              <input
                type="text"
                value={tagsInput}
                onChange={e => setTagsInput(e.target.value)}
                placeholder="favorite, trade-bait, sleeve…"
                style={{
                  flex: 1, padding: '7px 10px', borderRadius: 8,
                  background: 'var(--bg-2)', color: 'var(--ink)',
                  border: '1px solid transparent',
                  fontSize: 13, fontWeight: 500,
                  outline: 'none', minWidth: 0,
                }}
              />
            </div>

            {/* Adjusted price */}
            <div className="row" style={{ marginTop: 2, justifyContent: 'space-between', alignItems: 'baseline' }}>
              <div style={{ fontSize: 10, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.05em' }}>
                {useManual ? 'YOUR PRICE' : 'ADJUSTED PRICE'}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end' }}>
                <Price usd={adjUSD} currency={cur === 'BOTH' ? 'USD' : cur} size="md"/>
                {hasBase && !useManual && adjUSD != null && Math.abs(adjUSD - baseUSD) > 0.005 && (
                  <span className="mono" style={{ fontSize: 10, color: 'var(--ink-4)', textDecoration: 'line-through' }}>
                    base {fmtUSD(baseUSD)}
                  </span>
                )}
                {!hasBase && !useManual && (
                  <span style={{ fontSize: 10, color: 'var(--ink-4)' }}>tap eBay to look up the real price</span>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Action buttons.
            Skip / Details / Wishlist / Add — Wishlist is icon-only to keep
            the row legible on narrow phones. It writes the card to the
            backend tagged `wishlist` (purchase_price null) instead of going
            through the scan cart. */}
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
          </div>
        )}
      </div>
    </>
  );
}

window.ScanScreen = ScanScreen;
