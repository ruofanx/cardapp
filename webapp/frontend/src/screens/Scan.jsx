import React, { useState, useRef, useEffect } from 'react'
import api from '../api.js'
import { CardArt, Icon, Price } from '../components.jsx'

/* Scan — real photo+search identify flow.
 * Visual style preserved from the original camera UI; pipeline log now reflects
 * actual API stages. Tapping the shutter opens a file picker (camera on mobile);
 * the typed search input hits /identify with a query. Results land in scanQueue
 * which the Bulk screen flushes into the real backend on commit.
 */

function ScanScreen({ tweaks, navigate, scanQueue, identifyCard, addToCollection, collection, backend }) {
  const [phase, setPhase] = useState('idle');         // idle | scanning | result
  const [flash, setFlash] = useState(false);
  const [pipelineLog, setPipelineLog] = useState([]);
  const [query, setQuery] = useState('');
  const [candidates, setCandidates] = useState([]);
  const [error, setError] = useState(null);
  // Blob URL of the user's captured photo — used as a fallback "this is the
  // actual card you scanned" image when no catalogued image exists for the
  // selected printing.
  const [capturedPhotoUrl, setCapturedPhotoUrl] = useState(null);
  const [capturedPhotoFile, setCapturedPhotoFile] = useState(null);
  // Pre-scan TYPE hint — 'auto' | 'card' | 'sealed'. Lets the user tell the
  // OCR what they're photographing BEFORE shooting, so a sealed product
  // (booster box/ETB/tin/bundle) doesn't get routed through the individual
  // card search/pricing pipeline (which returns unrelated card images).
  const [scanType, setScanType] = useState('auto');
  const [scanUsage, setScanUsage] = useState(null); // { used, limit } or null
  const fileInputRef = useRef(null);

  useEffect(() => {
    api.getAccount().then(acct => {
      if (acct?.scan_limit != null) {
        setScanUsage({ used: acct.scan_used ?? 0, limit: acct.scan_limit })
      }
    }).catch(() => {})
  }, []);

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
      let found = identifyCard
        ? await identifyCard({ query, image, productTypeHint: scanType })
        : [];
      // Update scan counter in the UI after a successful API call
      if (image && found?.length > 0 && scanUsage) {
        setScanUsage(u => u ? { ...u, used: Math.min(u.used + 1, u.limit) } : u);
      }

      // The EN-only catalogue search (Pokemon TCG API) returns nothing for
      // JP/CH card names typed directly off the physical card — those
      // names don't exist in its English database. Before giving up, try
      // TCGdex's localized databases directly with the raw query text (no
      // PokeAPI translation needed since the query IS already in that
      // language). This is the only path that can find e.g. a Chinese-set
      // card by its printed Chinese name.
      if ((!found || found.length === 0) && !image && query.trim() && api?.searchTCGdex) {
        log('Widening (TCGdex)', `"${query.slice(0, 24)}" — no catalogue match`);
        const q = query.trim();
        // TCGdex does exact-name matching. For EN queries the user often appends
        // set/rarity modifiers ("bulbasaur first partner black star promo…") that
        // aren't part of the Pokémon name — strip everything after the first word
        // so we find "bulbasaur" instead of matching nothing. JP/ZH queries ARE
        // the Pokémon name in full, so keep those unchanged.
        const enName = q.split(/\s+/)[0];
        const fallbackTasks = [
          ['TCGdex EN',    api.searchTCGdex({ name: enName }, { pageSize: 15, lang: 'en' })],
          ['TCGdex JA',    api.searchTCGdex({ name: q }, { pageSize: 15, lang: 'ja', dbLang: 'ja' })],
          ['TCGdex ZH-TW', api.searchTCGdex({ name: q }, { pageSize: 15, lang: 'ch', dbLang: 'zh-tw' })],
          ['TCGdex ZH-CN', api.searchTCGdex({ name: q }, { pageSize: 15, lang: 'ch', dbLang: 'zh-cn' })],
        ];
        const fallbackResults = await Promise.allSettled(fallbackTasks.map(([, p]) => p));
        const fallbackHits = [];
        const fallbackSeen = new Set();
        fallbackResults.forEach((r, i) => {
          const label = fallbackTasks[i][0];
          const hits = (r.status === 'fulfilled' && Array.isArray(r.value)) ? r.value : [];
          log(label, `${hits.length} hit${hits.length === 1 ? '' : 's'}`);
          for (const h of hits) {
            if (!fallbackSeen.has(h.id)) { fallbackHits.push(h); fallbackSeen.add(h.id); }
          }
        });
        if (fallbackHits.length > 0) found = fallbackHits;
      }

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
      // Skip entirely for sealed products — those APIs only return individual
      // cards, so widening a "Booster Box"/"ETB" name just floods the results
      // with unrelated single-card matches (e.g. "Mega" matching every "Mega
      // ___ ex" card).
      const seedName = (found[0]?.name || query || '').trim();
      const widened = [...found];
      const seen = new Set(found.map(c => c.id));
      const seedIsSealed = api?.isSealedProduct?.(found[0]);

      if (seedIsSealed) {
        log('Widening skipped', 'sealed product — card search not applicable');
      } else if (seedName && api) {
        log('Widening search', `"${seedName.slice(0, 24)}"`);

        // Translate the Pokemon name to JA + ZH so we can query TCGdex's
        // localized databases (which use Japanese / Chinese names, not EN).
        let names = null;
        try {
          if (api.lookupPokemonNames) {
            names = await api.lookupPokemonNames(seedName);
            if (names) log('Names', `ja:${names.ja || '—'} · zh:${names.zh || '—'}`);
          }
        } catch (_) {}

        const tasks = [];
        if (api.searchPokemonTCG) tasks.push(['Pokemon TCG', api.searchPokemonTCG({ name: seedName }, { pageSize: 20 })]);
        if (api.searchTCGdex)     tasks.push(['TCGdex EN',   api.searchTCGdex({ name: seedName }, { pageSize: 20, lang: 'en' })]);
        if (api.searchTCGdex && names?.ja) tasks.push(['TCGdex JA',   api.searchTCGdex({ name: names.ja }, { pageSize: 15, lang: 'ja', dbLang: 'ja' })]);
        if (api.searchTCGdex && names?.zh) tasks.push(['TCGdex ZH-TW', api.searchTCGdex({ name: names.zh }, { pageSize: 15, lang: 'ch', dbLang: 'zh-tw' })]);
        if (api.searchTCGdex && names?.zh) tasks.push(['TCGdex ZH-CN', api.searchTCGdex({ name: names.zh }, { pageSize: 15, lang: 'ch', dbLang: 'zh-cn' })]);
        // Final fallback: PriceCharting indexes some Chinese-exclusive sets
        // (e.g. "Pokemon Chinese CSV4C") that TCGdex has registered but never
        // populated with card data. Only contributes identities the other
        // sources missed entirely — see dedup below.
        if (api.searchPriceCharting) tasks.push(['PriceCharting', api.searchPriceCharting(seedName, { pageSize: 10 })]);

        const results = await Promise.allSettled(tasks.map(([, p]) => p));
        const cardKey = (c) => `${(c.name || '').toLowerCase().trim()}|${String(c.code || '').trim()}`;
        results.forEach((r, i) => {
          const label = tasks[i][0];
          const hits = (r.status === 'fulfilled' && Array.isArray(r.value)) ? r.value : [];
          log(label, `${hits.length} hit${hits.length === 1 ? '' : 's'}`);
          for (const h of hits) {
            if (seen.has(h.id)) continue;
            // PriceCharting results often duplicate cards already found via
            // Pokemon TCG API / TCGdex (same name + number, better image &
            // pricing there) — only add ones that fill a genuine gap.
            if (label === 'PriceCharting' && widened.some(c => cardKey(c) === cardKey(h))) continue;
            widened.push(h); seen.add(h.id);
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
          // The OCR seed (widened[0]) with a known card number but no
          // image means the backend already checked and found no
          // catalogue entry for THAT printing — a same-name sibling here
          // is a different printing with different art (e.g. a brand-new
          // promo vs. an older base print). Don't borrow its image; let
          // ScanResultSheet fall back to the user's captured photo.
          if (c === widened[0] && c.code) continue;
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
          if (c === widened[0] && c.code) continue;
          const bn = baseNameOf(c);
          if (anyLangMap.has(bn)) c.image_url = anyLangMap.get(bn);
        }

        // Set-aware JP sort: when the query names a JP set, float JP results
        // from that set to the top so the user sees the correct printing first.
        const JP_SET_ALIASES = {
          "terastal festival": "SV8a", "terastal fes": "SV8a",
          "crimson haze": "SV5a",
          "paradise dragona": "SV7a",
          "stellar miracle": "SV7", "super electric breaker": "SV8",
          "shiny treasure": "SV4a",
          "wild force": "SV5K", "cyber judge": "SV5M",
          "mask of change": "SV6",
          "battle partners": "SV9",
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

  // Generic bottom toast — { icon, text }. Used for both the wishlist path
  // and the direct "Add to Collection" path below.
  const [toast, setToast] = useState(null);

  // Wishlist path — writes the card straight to the backend tagged
  // `wishlist`, bypassing the scan cart (since the user doesn't own this
  // card yet). purchase_price is null so it doesn't pollute gain/loss
  // calculations. Browse hides wishlist cards from default views and
  // surfaces them under the Wishlist filter chip.
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

        {/* Pre-scan TYPE toggle — tells the OCR what's being photographed
            (individual card vs. sealed booster/box/ETB/tin/bundle) so the
            result isn't routed through the wrong pricing/image pipeline. */}
        <div className="row gap-2" style={{ marginTop: 8 }}>
          {[
            { id: 'auto',   label: 'Auto' },
            { id: 'card',   label: 'Card' },
            { id: 'sealed', label: 'Sealed' },
          ].map(opt => (
            <button key={opt.id} className="tap" onClick={() => setScanType(opt.id)} style={{
              flex: 1, padding: '6px 0', borderRadius: 999,
              background: scanType === opt.id ? 'var(--accent)' : 'oklch(0 0 0 / 0.5)',
              color: scanType === opt.id ? 'var(--accent-ink)' : 'oklch(1 0 0 / 0.85)',
              backdropFilter: 'blur(12px)',
              fontSize: 11, fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase',
              border: '1px solid oklch(1 0 0 / 0.1)',
            }}>{opt.label}</button>
          ))}
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

      {/* Scan usage badge — free users only */}
      {scanUsage && phase === 'idle' && (
        <div style={{
          position: 'absolute', top: 64, right: 16,
          padding: '4px 10px', borderRadius: 999,
          background: scanUsage.used >= scanUsage.limit
            ? 'oklch(0.40 0.15 30 / 0.85)'
            : 'oklch(0 0 0 / 0.55)',
          color: 'oklch(1 0 0 / 0.9)',
          fontSize: 11, fontWeight: 600, letterSpacing: '0.02em',
          backdropFilter: 'blur(8px)',
        }}>
          {scanUsage.used}/{scanUsage.limit} scans
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
          existingTags={Array.from(new Set((collection || []).flatMap(c => c.tags || []).filter(Boolean))).sort()}
          onAddToCollection={handleAddToCollection}
          onAddWishlist={handleAddWishlist}
          onSkip={handleSkip}
          onDetail={(card) => navigate('detail', { card })}
        />
      )}

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
    </div>
  );
}

const SUGGESTED_TAGS = ['for trade', 'binder', 'pc', 'graded', 'gift'];

function ScanResultSheet({ candidates, tweaks, capturedPhotoUrl, capturedPhotoFile, existingTags, onAddToCollection, onAddWishlist, onSkip, onDetail }) {
  const cur = tweaks.currency;
  const [picked, setPicked] = useState(0);
  const [setFilter, setSetFilter] = useState('');
  const [rarityFilter, setRarityFilter] = useState('');
  const [langFilter, setLangFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState(() =>
    api?.isSealedProduct?.(candidates[0]) ? 'sealed' : 'card');
  const [isAdding, setIsAdding] = useState(false);
  const [purchasePrice, setPurchasePrice] = useState('');
  const [selectedTags, setSelectedTags] = useState([]);
  const [newTag, setNewTag] = useState('');

  const toggleTag = (tag) => setSelectedTags(prev =>
    prev.includes(tag) ? prev.filter(t => t !== tag) : [...prev, tag]);

  const commitNewTag = () => {
    const t = newTag.trim().toLowerCase();
    if (t && !selectedTags.includes(t)) setSelectedTags(prev => [...prev, t]);
    setNewTag('');
  };
  // 1st Edition toggle — for JP/CH cards where the search results don't
  // distinguish 1st Ed from Unlimited (TCGdex JA often lacks this data for
  // older XY/BW-era sets like Wild Blaze). Resets when the selected card
  // changes so picking a different candidate starts clean.
  const [isFirstEd, setIsFirstEd] = useState(false);

  const uniq = (arr) => Array.from(new Set(arr.filter(Boolean)));
  const setOptions    = uniq(candidates.map(c => c.set));
  const rarityOptions = uniq(candidates.map(c => c.variant || c._rarity));
  const hasCardCandidates   = candidates.some(c => !api?.isSealedProduct?.(c));
  const hasSealedCandidates = candidates.some(c => api?.isSealedProduct?.(c));
  const showTypeFilter = hasCardCandidates && hasSealedCandidates;

  const filtered = candidates.filter(c => {
    if (showTypeFilter) {
      const sealed = api?.isSealedProduct?.(c);
      if (typeFilter === 'card'   && sealed)  return false;
      if (typeFilter === 'sealed' && !sealed) return false;
    }
    if (setFilter    && c.set                    !== setFilter)    return false;
    if (rarityFilter && (c.variant || c._rarity) !== rarityFilter) return false;
    if (langFilter   && (c.lang || 'EN')         !== langFilter)   return false;
    return true;
  });
  const langOptions = ['EN', 'JP', 'CH'];
  const safePicked = Math.min(picked, Math.max(0, filtered.length - 1));
  const card = filtered[safePicked] || filtered[0] || candidates[0];

  // Reset 1st Edition toggle whenever the selected card changes.
  React.useEffect(() => { setIsFirstEd(false); }, [card?.id]);

  const fallbackImage = capturedPhotoUrl || card?.raw?.image_url || card?.raw?.image || null;
  const cardForHero = card && !card.image_url ? { ...card, image_url: fallbackImage } : card;

  const paidNum = Number(purchasePrice);
  const paidValue = purchasePrice !== '' && Number.isFinite(paidNum) && paidNum >= 0 ? paidNum : null;

  const defaultCardProps = card ? {
    condition: 'NM',
    lang: card.lang || 'EN',
    grader: null,
    grade: null,
    is_graded: false,
    usd: card.usd,
    purchase_price: paidValue,
    tags: selectedTags,
  } : {};

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
        maxHeight: '90%',
        overflowY: 'auto',
        display: 'flex', flexDirection: 'column', gap: 10,
      }}>
        <div style={{ width: 36, height: 4, borderRadius: 2, background: 'var(--ink-4)', margin: '4px auto 2px' }}/>

        {/* Header */}
        <div className="row" style={{ alignItems: 'flex-start', justifyContent: 'space-between' }}>
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

        {/* Hero card — tap to open Detail for full editing */}
        {card && (
          <button className="tap col" onClick={() => onDetail({ ...card, ...defaultCardProps })}
            style={{ alignItems: 'center', gap: 8, background: 'none', border: 'none', padding: 0 }}>
            <div style={{ position: 'relative' }}>
              <CardArt card={cardForHero} renderMode={tweaks.cardRender} size="md"/>
              <div style={{
                position: 'absolute', top: 6, right: 6,
                background: 'oklch(0 0 0 / 0.55)', backdropFilter: 'blur(8px)',
                borderRadius: 6, padding: '3px 6px',
                fontSize: 9, color: 'oklch(1 0 0 / 0.7)', fontWeight: 600,
              }}>tap to edit ↗</div>
            </div>
            <div style={{ textAlign: 'center' }}>
              <div className="row gap-2" style={{ justifyContent: 'center', alignItems: 'center' }}>
                <span style={{
                  fontSize: 9, fontWeight: 700, padding: '1px 5px', borderRadius: 4, flexShrink: 0,
                  background: card.lang === 'JP' ? 'oklch(0.45 0.16 25)'
                             : card.lang === 'CH' ? 'oklch(0.45 0.14 80)'
                             : 'oklch(0.40 0.06 250)',
                  color: '#fff', letterSpacing: '0.05em',
                }}>{card.lang || 'EN'}</span>
                <span style={{ fontSize: 15, fontWeight: 700 }}>{card.name}</span>
              </div>
              {!api?.isSealedProduct?.(card) && (
                <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 2 }}>
                  {card.code}{card.set ? ` · ${card.set}` : ''}
                </div>
              )}
              {(card.variant || card._rarity || isFirstEd) && (
                <div style={{ fontSize: 10, color: isFirstEd ? 'var(--accent)' : 'var(--ink-3)', fontWeight: isFirstEd ? 600 : 400 }}>
                  {isFirstEd ? '1st Edition · ' : ''}{card.variant || card._rarity || ''}
                </div>
              )}
              <div style={{ marginTop: 4 }}>
                <Price usd={card.usd} currency={cur === 'BOTH' ? 'USD' : cur} size="sm"/>
              </div>
            </div>
          </button>
        )}

        {/* Filter chips */}
        <div className="col gap-2">
          {showTypeFilter && (
            <div className="row" style={{ gap: 6, overflowX: 'auto', paddingBottom: 4, scrollbarWidth: 'none' }}>
              <div style={{ fontSize: 11, color: 'var(--ink-3)', alignSelf: 'center', flexShrink: 0, marginRight: 4 }}>TYPE</div>
              <Chip active={typeFilter === 'card'} onClick={() => setTypeFilter('card')}>
                Cards · {candidates.filter(c => !api?.isSealedProduct?.(c)).length}
              </Chip>
              <Chip active={typeFilter === 'sealed'} onClick={() => setTypeFilter('sealed')}>
                Sealed · {candidates.filter(c => api?.isSealedProduct?.(c)).length}
              </Chip>
            </div>
          )}
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
          {(setOptions.length > 1 || rarityOptions.length > 1) && (
            <>
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
            </>
          )}
        </div>

        {/* Candidate filmstrip */}
        {filtered.length === 0 ? (
          <div style={{ padding: '16px 0', color: 'var(--ink-3)', fontSize: 13, textAlign: 'center' }}>
            No matches with these filters.
          </div>
        ) : (
          <div style={{ overflowX: 'auto', scrollbarWidth: 'none', marginLeft: -16, marginRight: -16, paddingLeft: 16, paddingRight: 16 }}>
            <div style={{ display: 'flex', gap: 8, paddingBottom: 4 }}>
              {filtered.map((cand, i) => {
                const active = i === safePicked;
                const candFallback = capturedPhotoUrl || cand.raw?.image_url || cand.raw?.image || null;
                const candForDisplay = cand.image_url ? cand : { ...cand, image_url: candFallback };
                const alreadyOwned = cand.id && (collection || []).some(x => x.id === cand.id && !((x.tags || []).some(t => {
                  const n = typeof t === 'object' ? (t.name || '') : String(t);
                  return n.toLowerCase() === 'wishlist';
                })));
                return (
                  <button key={cand.id || i} className="tap col" onClick={() => setPicked(i)} style={{
                    alignItems: 'center', gap: 3,
                    background: 'none', border: 'none', flexShrink: 0,
                    padding: '4px 4px 6px', borderRadius: 10,
                    outline: active ? '2px solid var(--accent)' : '2px solid transparent',
                    outlineOffset: 1,
                    position: 'relative',
                  }}>
                    <div style={{ position: 'relative' }}>
                      <CardArt card={candForDisplay} renderMode={tweaks.cardRender} size="sm" flat/>
                      {alreadyOwned && (
                        <div style={{
                          position: 'absolute', top: 2, right: 2,
                          width: 14, height: 14, borderRadius: 7,
                          background: 'var(--accent)', color: 'var(--accent-ink)',
                          fontSize: 9, fontWeight: 700, lineHeight: '14px', textAlign: 'center',
                          border: '1px solid var(--bg)',
                        }}>✓</div>
                      )}
                    </div>
                    <div style={{
                      fontSize: 8, color: active ? 'var(--ink)' : 'var(--ink-3)',
                      fontWeight: active ? 700 : 400,
                      width: 64, textAlign: 'center',
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}>{cand.name}</div>
                    <Price usd={cand.usd} currency={cur === 'BOTH' ? 'USD' : cur} size="xs"/>
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {/* 1st Edition toggle — JP/CH XY/BW-era cards often have both 1st
            Edition and Unlimited printings but TCGdex doesn't always provide
            that distinction. Let the user flag it here before adding. */}
        {card && (card.lang === 'JP' || card.lang === 'CH') && !api?.isSealedProduct?.(card) && (
          <button className="tap" onClick={() => setIsFirstEd(v => !v)} style={{
            width: '100%', padding: '8px 0', borderRadius: 12,
            background: isFirstEd ? 'oklch(0.45 0.16 25)' : 'var(--bg-2)',
            color: isFirstEd ? '#fff' : 'var(--ink-3)',
            fontWeight: 600, fontSize: 12,
            border: isFirstEd ? '1.5px solid oklch(0.55 0.18 25)' : '1.5px solid var(--hairline-soft)',
          }}>
            {isFirstEd ? '✓ 1st Edition' : '1st Edition?  Tap to mark'}
          </button>
        )}

        {/* Paid price + tag picker */}
        {card && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {/* PAID $ */}
            <div className="row" style={{ gap: 8, alignItems: 'center' }}>
              <div style={{ fontSize: 10, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.05em', flexShrink: 0 }}>PAID $</div>
              <input
                type="number" inputMode="decimal" step="0.01" min="0"
                value={purchasePrice}
                onChange={e => setPurchasePrice(e.target.value)}
                placeholder="what you paid (optional)"
                style={{
                  flex: 1, padding: '7px 10px', borderRadius: 8,
                  background: 'var(--bg-2)', color: 'var(--ink)',
                  border: '1px solid transparent',
                  fontSize: 13, fontWeight: 600, fontFamily: 'var(--mono)',
                  outline: 'none',
                }}
              />
            </div>

            {/* Tags */}
            <div>
              <div style={{ fontSize: 10, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.05em', marginBottom: 6 }}>TAGS</div>

              {/* Selected tags */}
              {selectedTags.length > 0 && (
                <div className="row gap-1" style={{ flexWrap: 'wrap', marginBottom: 6 }}>
                  {selectedTags.map(t => (
                    <button key={t} className="tap" onClick={() => toggleTag(t)} style={{
                      padding: '3px 8px 3px 6px', borderRadius: 999,
                      background: 'var(--accent-soft)', color: 'var(--accent)',
                      border: '1px solid var(--accent)',
                      fontSize: 11, fontWeight: 600,
                      display: 'inline-flex', alignItems: 'center', gap: 4,
                    }}>#{t} <span style={{ fontSize: 13, lineHeight: 1 }}>×</span></button>
                  ))}
                </div>
              )}

              {/* Suggestions from collection + hardcoded */}
              {(() => {
                const pool = Array.from(new Set([...SUGGESTED_TAGS, ...(existingTags || [])]));
                const unselected = pool.filter(t => !selectedTags.includes(t));
                if (unselected.length === 0) return null;
                return (
                  <div style={{ overflowX: 'auto', scrollbarWidth: 'none', marginLeft: -16, marginRight: -16, paddingLeft: 16, paddingRight: 16, marginBottom: 6 }}>
                    <div className="row gap-1" style={{ flexWrap: 'nowrap', paddingBottom: 2 }}>
                      {unselected.map(t => (
                        <button key={t} className="tap" onClick={() => toggleTag(t)} style={{
                          padding: '3px 8px', borderRadius: 999, flexShrink: 0,
                          background: 'transparent', color: 'var(--ink-3)',
                          border: '1px dashed var(--hairline-soft)',
                          fontSize: 11, fontWeight: 500, whiteSpace: 'nowrap',
                        }}>+ {t}</button>
                      ))}
                    </div>
                  </div>
                );
              })()}

              {/* New tag input */}
              <div className="row gap-2">
                <input
                  value={newTag}
                  onChange={e => setNewTag(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); commitNewTag(); }
                  }}
                  placeholder="type a new tag…"
                  style={{
                    flex: 1, padding: '7px 10px', borderRadius: 8,
                    background: 'var(--bg-2)', color: 'var(--ink)',
                    border: '1px solid var(--hairline-soft)',
                    fontSize: 13, outline: 'none',
                  }}
                />
                <button className="tap" onClick={commitNewTag} disabled={!newTag.trim()} style={{
                  padding: '7px 12px', borderRadius: 8,
                  background: 'var(--bg-2)', color: 'var(--ink-2)',
                  fontSize: 12, fontWeight: 600,
                  border: '1px solid var(--hairline-soft)',
                  opacity: newTag.trim() ? 1 : 0.4,
                }}>Add</button>
              </div>
            </div>
          </div>
        )}

        {/* Action bar — Done | Wishlist | Add to Collection (NM/EN/Raw defaults) */}
        {card && (
          <div className="row gap-2">
            <button className="tap" onClick={onSkip} style={{
              flex: 1, height: 46, borderRadius: 14,
              background: 'var(--bg-2)', color: 'var(--ink)',
              fontWeight: 500, fontSize: 14,
            }}>Done</button>
            {onAddWishlist && (
              <button className="tap" title="Add to wishlist" aria-label="Add to wishlist"
                onClick={() => onAddWishlist({ ...card, ...defaultCardProps })}
                style={{
                  width: 46, height: 46, borderRadius: 14, flexShrink: 0,
                  background: 'var(--accent-soft)', color: 'var(--accent)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                }}>
                <Icon name="star" size={20}/>
              </button>
            )}
            <button className="tap" disabled={isAdding} onClick={async () => {
              setIsAdding(true);
              try {
                const baseVariant = card.variant || card._rarity || null;
                const variantOverride = isFirstEd
                  ? (baseVariant && !baseVariant.toLowerCase().includes('1st')
                      ? `1st Edition ${baseVariant}`
                      : (baseVariant || '1st Edition'))
                  : baseVariant;
                await onAddToCollection({
                  ...card,
                  variant: variantOverride,
                  ...defaultCardProps,
                  ...(capturedPhotoFile ? { _capturedPhotoFile: capturedPhotoFile } : {}),
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
      </div>
    </>
  );
}

export default ScanScreen
