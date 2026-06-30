import React, { useState, useEffect, useRef } from 'react'
import api from '../api.js'
import { CardArt, Icon, NavBar, NavBackButton, fmtPrice, tagNamesOf } from '../components.jsx'

/* Trade show helper — scan or search a card you want, get offer options from your binder */

// Shared by the main search/scan flow and the "add another card" sheet:
// run the photo/text search, then widen by name across both card APIs so
// printing variants (1st Edition / Unlimited / etc.) all show up as choices.
async function findCandidates({ query, image, identifyCard }) {
  const found = identifyCard ? await identifyCard({ query, image }) : [];
  if (!found?.length) return [];
  const seedName = (found[0]?.name || query || '').trim();
  const widened = [...found];
  const seen = new Set(found.map(c => c.id));

  if (seedName && api) {
    const tasks = [];
    if (api.searchPokemonTCG) tasks.push(api.searchPokemonTCG({ name: seedName }, { pageSize: 20 }));
    if (api.searchTCGdex)     tasks.push(api.searchTCGdex({ name: seedName }, { pageSize: 20, lang: 'en' }));
    const results = await Promise.allSettled(tasks);
    results.forEach(r => {
      if (r.status === 'fulfilled' && Array.isArray(r.value)) {
        r.value.forEach(h => { if (!seen.has(h.id)) { widened.push(h); seen.add(h.id); } });
      }
    });
  }
  return widened;
}

async function priceCard(card) {
  let price = card.usd ?? null;
  let source = price != null ? 'catalog' : null;
  if (!price && api?.quotePrice) {
    const q = await api.quotePrice(card);
    price  = q?.estimated_price ?? null;
    source = q?.source ?? null;
  }
  return { price, source };
}

function TradeScreen({ tweaks, navigate, identifyCard, currentUser, collection, backend, params = {} }) {
  const cur = tweaks.currency === 'BOTH' ? 'USD' : tweaks.currency;

  const [phase, setPhase]           = useState('idle');   // idle | scanning | picking | loading | offers
  const [query, setQuery]           = useState('');
  const [candidates, setCandidates] = useState([]);
  const [loadingCard, setLoadingCard] = useState(null);
  const [targets, setTargets]       = useState([]); // [{ card, price, source }, ...] — cards you want
  const [offers, setOffers]         = useState(null);
  const [error, setError]           = useState(null);
  const [tradeInFactor, setTradeInFactor] = useState(0.85);
  const [manualOptions, setManualOptions] = useState([[], []]); // two binder-built offers
  const [pickerFor, setPickerFor]   = useState(null); // null | 0 | 1 — which option the picker is adding to
  const [addTargetOpen, setAddTargetOpen] = useState(false);
  const fileInputRef = useRef(null);

  const pricedTargets = targets.filter(t => t.price != null);
  const targetTotal = pricedTargets.length > 0
    ? pricedTargets.reduce((s, t) => s + t.price, 0)
    : null;

  const reset = () => {
    setPhase('idle'); setQuery(''); setCandidates([]);
    setLoadingCard(null); setTargets([]);
    setOffers(null); setError(null);
    setTradeInFactor(0.85); setManualOptions([[], []]); setPickerFor(null);
    setAddTargetOpen(false);
  };

  const addTarget = ({ card, price, source }) => {
    setTargets(prev => [...prev, { card, price, source }]);
    setAddTargetOpen(false);
  };

  const removeTarget = (idx) => {
    setTargets(prev => prev.filter((_, i) => i !== idx));
  };

  const addToOption = (idx, card) => {
    setManualOptions(prev => {
      const next = prev.map(arr => [...arr]);
      next[idx] = [...next[idx], card];
      return next;
    });
    setPickerFor(null);
  };

  const removeFromOption = (idx, cardId) => {
    setManualOptions(prev => {
      const next = prev.map(arr => [...arr]);
      next[idx] = next[idx].filter(c => c.id !== cardId);
      return next;
    });
  };

  const runFind = async ({ image } = {}) => {
    if (!image && !query.trim()) return;
    setPhase('scanning');
    setCandidates([]); setTargets([]); setOffers(null); setError(null);
    try {
      const widened = await findCandidates({ query, image, identifyCard });
      if (!widened.length) {
        setError('No matches — try a different search or photo.');
        setPhase('idle');
        return;
      }
      setCandidates(widened);
      if (widened.length === 1) {
        await selectTarget(widened[0]);
      } else {
        setPhase('picking');
      }
    } catch (e) {
      setError(String(e.message || e).slice(0, 100));
      setPhase('idle');
    }
  };

  const selectTarget = async (card) => {
    setLoadingCard(card);
    setPhase('loading');
    setOffers(null);
    setManualOptions([[], []]);
    try {
      const { price, source } = await priceCard(card);
      setTargets([{ card, price, source }]);
      if (price == null) {
        setError("Couldn't get a market price — try a different printing.");
        setOffers({ options: [], candidate_count: 0, priced_candidate_count: 0 });
      }
      setPhase('offers');
    } catch (e) {
      setError(String(e.message || e).slice(0, 100));
      setTargets([{ card, price: null, source: null }]);
      setOffers({ options: [], candidate_count: 0, priced_candidate_count: 0 });
      setPhase('offers');
    }
  };

  // Re-run the binder subset-sum search whenever the combined target value or
  // the trade-in factor changes. Required value = what the dealer needs to see
  // in raw market value from your binder to credit `targetTotal` worth of
  // trade-in (e.g. at 80% trade-in, you need targetTotal / 0.80 in market value).
  useEffect(() => {
    if (targetTotal == null) return;
    let cancelled = false;
    setOffers(null);
    (async () => {
      try {
        const requiredValue = targetTotal / tradeInFactor;
        const tolerance = Math.min(20, Math.max(3, requiredValue * 0.12));
        const result = await api.proposeTrade({
          user_id:   currentUser?.id,
          target_value: requiredValue,
          tolerance,
          max_results: 12,
        });
        if (!cancelled) setOffers(result);
      } catch (e) {
        if (!cancelled) setOffers({ options: [], candidate_count: 0, priced_candidate_count: 0 });
      }
    })();
    return () => { cancelled = true; };
  }, [targetTotal, tradeInFactor]);

  return (
    <div className="screen" style={{ animation: 'pushIn 0.25s ease-out' }}>
      <NavBar
        title="Trade"
        left={phase === 'idle'
          ? <NavBackButton onClick={() => navigate('home')} label="Home"/>
          : <NavBackButton onClick={reset} label="Back"/>}
      />

      {phase === 'idle' && (
        <TradeIdleView
          query={query} setQuery={setQuery}
          onSearch={() => runFind({})}
          onCapture={() => fileInputRef.current?.click()}
          error={error}
        />
      )}

      {phase === 'scanning' && (
        <div style={{ display: 'grid', placeItems: 'center', flex: 1, flexDirection: 'column', gap: 14 }}>
          <div style={{ fontSize: 13, color: 'var(--ink-3)' }}>Identifying card…</div>
        </div>
      )}

      {phase === 'picking' && (
        <TradeCandidatePicker
          candidates={candidates}
          tweaks={tweaks}
          cur={cur}
          onPick={selectTarget}
        />
      )}

      {phase === 'loading' && loadingCard && (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', paddingTop: 32, gap: 16 }}>
          <CardArt card={loadingCard} renderMode={tweaks.cardRender} size="md"/>
          <div style={{ fontSize: 13, color: 'var(--ink-3)' }}>Searching your binder…</div>
        </div>
      )}

      {phase === 'offers' && targets.length > 0 && (
        <TradeOffersView
          targets={targets} targetPrice={targetTotal}
          cur={cur} offers={offers} tweaks={tweaks} error={error}
          onReset={reset}
          tradeInFactor={tradeInFactor} onTradeInFactorChange={setTradeInFactor}
          manualOptions={manualOptions}
          onAddCard={setPickerFor}
          onRemoveCard={removeFromOption}
          onAddTarget={() => setAddTargetOpen(true)}
          onRemoveTarget={removeTarget}
        />
      )}

      {pickerFor != null && (
        <BinderPickerSheet
          collection={collection}
          excludeIds={new Set(manualOptions.flat().map(c => c.id))}
          onPick={card => addToOption(pickerFor, card)}
          onClose={() => setPickerFor(null)}
          cur={cur} tweaks={tweaks}
        />
      )}

      {addTargetOpen && (
        <AddTargetSheet
          identifyCard={identifyCard}
          tweaks={tweaks} cur={cur}
          onAdd={addTarget}
          onClose={() => setAddTargetOpen(false)}
        />
      )}

      <input ref={fileInputRef} type="file" accept="image/*" capture="environment"
        style={{ display: 'none' }} onChange={e => {
          const f = e.target.files?.[0];
          if (f) runFind({ image: f });
          e.target.value = '';
        }}/>
    </div>
  );
}

/* ── Idle: scan/search landing ─────────────────────────────────────────── */

function TradeIdleView({ query, setQuery, onSearch, onCapture, error }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '40px 24px 24px', gap: 24 }}>
      {/* Camera button */}
      <button className="tap" onClick={onCapture} style={{
        width: 120, height: 120, borderRadius: 60,
        background: 'var(--accent)',
        display: 'grid', placeItems: 'center',
        boxShadow: '0 8px 32px oklch(0.55 0.18 250 / 0.35)',
      }}>
        <Icon name="camera" size={44} stroke={1.5} color="#fff"/>
      </button>
      <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink-2)' }}>
        Scan the card you want
      </div>

      {/* Divider */}
      <div className="row" style={{ width: '100%', alignItems: 'center', gap: 10 }}>
        <div style={{ flex: 1, height: 1, background: 'var(--hairline)' }}/>
        <span style={{ fontSize: 11, color: 'var(--ink-4)', fontWeight: 600 }}>OR</span>
        <div style={{ flex: 1, height: 1, background: 'var(--hairline)' }}/>
      </div>

      {/* Search bar */}
      <div className="row" style={{ width: '100%', gap: 8 }}>
        <input
          type="text" value={query} onChange={e => setQuery(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && onSearch()}
          placeholder="Search by name, set, or number…"
          style={{
            flex: 1, background: 'var(--bg-2)', border: '1px solid var(--hairline)',
            borderRadius: 12, padding: '10px 14px', color: 'var(--ink)', fontSize: 14,
            outline: 'none',
          }}
        />
        <button className="tap" onClick={onSearch} style={{
          padding: '10px 16px', background: 'var(--accent)', borderRadius: 12,
          color: '#fff', fontSize: 13, fontWeight: 600, flexShrink: 0,
        }}>
          Search
        </button>
      </div>

      {error && (
        <div style={{ fontSize: 12, color: 'var(--neg)', textAlign: 'center' }}>{error}</div>
      )}

      <div style={{ fontSize: 12, color: 'var(--ink-4)', textAlign: 'center', maxWidth: 260, lineHeight: 1.5 }}>
        The app will look up the market price and show what you can offer from your "for trade" cards.
      </div>
    </div>
  );
}

/* ── Candidate picker: choose the right printing ───────────────────────── */

function TradeCandidatePicker({ candidates, tweaks, cur, onPick }) {
  const [picked, setPicked] = useState(0);

  // Group by language for quick filter chips
  const langs = Array.from(new Set(candidates.map(c => c.lang || 'EN')));
  const [langFilter, setLangFilter] = useState('');
  const filtered = langFilter ? candidates.filter(c => (c.lang || 'EN') === langFilter) : candidates;
  const safe = Math.min(picked, filtered.length - 1);
  const card = filtered[safe] || candidates[0];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, overflow: 'hidden' }}>
      {/* Selected card big preview */}
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '16px 0 12px', gap: 6 }}>
        <CardArt card={card} renderMode={tweaks.cardRender} size="md"/>
        <div style={{ fontSize: 14, fontWeight: 600 }}>{card?.name}</div>
        {card?.variant && (
          <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--accent)' }}>{card.variant}</div>
        )}
        {/^unlimited/i.test(card?.variant || '') && (
          <div style={{ fontSize: 10, color: 'var(--ink-4)', textAlign: 'center', maxWidth: 220, lineHeight: 1.4 }}>
            Photo is a reference scan and may show a "1st Edition" stamp that your Unlimited card won't have.
          </div>
        )}
        <div style={{ fontSize: 11, color: 'var(--ink-3)' }}>
          {card?.set}{card?.code ? ` · ${card.code}` : ''}{card?.lang && card.lang !== 'EN' ? ` · ${card.lang}` : ''}
          {card?.usd ? ` · ${fmtPrice(card.usd, cur)}` : ''}
        </div>
      </div>

      {/* Language filter chips */}
      {langs.length > 1 && (
        <div className="row" style={{ padding: '0 16px 8px', gap: 6, overflowX: 'auto', scrollbarWidth: 'none' }}>
          <button className="tap" onClick={() => setLangFilter('')} style={{
            padding: '4px 10px', borderRadius: 999, fontSize: 11, fontWeight: 600, flexShrink: 0,
            background: !langFilter ? 'var(--ink)' : 'var(--bg-2)',
            color: !langFilter ? 'var(--bg)' : 'var(--ink-2)',
            border: '1px solid var(--hairline-soft)',
          }}>All</button>
          {langs.map(l => (
            <button key={l} className="tap" onClick={() => setLangFilter(l)} style={{
              padding: '4px 10px', borderRadius: 999, fontSize: 11, fontWeight: 600, flexShrink: 0,
              background: langFilter === l ? 'var(--ink)' : 'var(--bg-2)',
              color: langFilter === l ? 'var(--bg)' : 'var(--ink-2)',
              border: '1px solid var(--hairline-soft)',
            }}>{l}</button>
          ))}
        </div>
      )}

      {/* Scrollable thumbnail strip */}
      <div style={{ overflowX: 'auto', scrollbarWidth: 'none', padding: '0 16px 12px' }}>
        <div className="row" style={{ gap: 8, width: 'max-content' }}>
          {filtered.map((c, i) => (
            <button key={i} className="tap" onClick={() => setPicked(i)} style={{
              flexShrink: 0, display: 'flex', flexDirection: 'column', alignItems: 'center',
              gap: 4, padding: 4, borderRadius: 10,
              background: i === safe ? 'var(--bg-2)' : 'transparent',
              border: i === safe ? '2px solid var(--accent)' : '2px solid transparent',
            }}>
              <CardArt card={c} renderMode={tweaks.cardRender} size="sm"/>
              <div style={{ maxWidth: 76, textAlign: 'center', lineHeight: 1.35 }}>
                <div style={{ fontSize: 9, color: 'var(--ink-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {c.set || c.id}
                </div>
                {c.variant && (
                  <div style={{ fontSize: 9, color: 'var(--ink-2)', fontWeight: 700, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {c.variant}
                  </div>
                )}
                {c.usd != null && (
                  <div className="mono" style={{ fontSize: 9, color: 'var(--accent)', fontWeight: 600 }}>
                    {fmtPrice(c.usd, cur)}
                  </div>
                )}
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Confirm */}
      <div style={{ padding: '8px 16px 32px', marginTop: 'auto' }}>
        <button className="tap" onClick={() => onPick(card)} style={{
          width: '100%', padding: '14px', background: 'var(--accent)', borderRadius: 14,
          color: '#fff', fontSize: 15, fontWeight: 700,
        }}>
          Use this card →
        </button>
      </div>
    </div>
  );
}

/* ── Offers view: target card + binder combinations ────────────────────── */

function TradeOffersView({
  targets, targetPrice, cur, offers, tweaks, error, onReset,
  tradeInFactor, onTradeInFactorChange, manualOptions, onAddCard, onRemoveCard,
  onAddTarget, onRemoveTarget,
}) {
  const options = offers?.options || [];
  const candidateCount = offers?.candidate_count ?? 0;
  const pricedCount    = offers?.priced_candidate_count ?? 0;
  const unpricedCount  = targets.filter(t => t.price == null).length;

  return (
    <div className="screen-scroll">
      {/* Target card header */}
      <div style={{
        margin: '0 16px 16px',
        background: 'var(--bg-1)', border: '1px solid var(--hairline-soft)',
        borderRadius: 16, padding: 14,
      }}>
        <div style={{ fontSize: 11, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 10 }}>
          You want
        </div>

        {targets.map((t, i) => (
          <div key={i} className="row" style={{ gap: 14, alignItems: 'center', marginBottom: i < targets.length - 1 ? 12 : 0 }}>
            <CardArt card={t.card} renderMode={tweaks.cardRender} size="sm"/>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 2 }}>{t.card.name}</div>
              <div style={{ fontSize: 11, color: 'var(--ink-3)', marginBottom: 6 }}>
                {t.card.set}{t.card.code ? ` · ${t.card.code}` : ''}{t.card.lang && t.card.lang !== 'EN' ? ` · ${t.card.lang}` : ''}
              </div>
              {t.price != null ? (
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
                  <span className="mono" style={{ fontSize: targets.length > 1 ? 18 : 22, fontWeight: 700, color: 'var(--ink)' }}>
                    {fmtPrice(t.price, cur)}
                  </span>
                  {t.source && (
                    <span style={{ fontSize: 10, color: 'var(--ink-4)' }}>{t.source}</span>
                  )}
                </div>
              ) : (
                <span style={{ fontSize: 13, color: 'var(--neg)' }}>Price unavailable</span>
              )}
            </div>
            {targets.length > 1 && (
              <button className="tap" onClick={() => onRemoveTarget(i)} style={{
                flexShrink: 0, width: 28, height: 28, borderRadius: 14,
                background: 'var(--bg-2)', color: 'var(--ink-3)',
                fontSize: 16, lineHeight: 1, display: 'grid', placeItems: 'center',
              }}>×</button>
            )}
          </div>
        ))}

        {targets.length > 1 && (
          <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--hairline-soft)' }}>
            <span style={{ fontSize: 12, color: 'var(--ink-3)', fontWeight: 600 }}>
              Total{unpricedCount > 0 ? ` (${unpricedCount} unpriced not included)` : ''}
            </span>
            <span className="mono" style={{ fontSize: 18, fontWeight: 700 }}>
              {targetPrice != null ? fmtPrice(targetPrice, cur) : '—'}
            </span>
          </div>
        )}

        <button className="tap" onClick={onAddTarget} style={{
          width: '100%', marginTop: 12, padding: '10px', borderRadius: 10,
          background: 'var(--bg-2)', border: '1px dashed var(--hairline)',
          color: 'var(--ink-2)', fontSize: 12, fontWeight: 600,
        }}>
          + Add another card
        </button>

        {/* Trade-in value factor */}
        {targetPrice != null && (
          <div style={{ marginTop: 14, paddingTop: 14, borderTop: '1px solid var(--hairline-soft)' }}>
            <div className="row" style={{ justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8 }}>
              <span style={{ fontSize: 11, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase' }}>
                Trade-in value
              </span>
            </div>
            <div className="row" style={{ gap: 6, marginBottom: 8 }}>
              {[0.80, 0.85, 0.90].map(f => (
                <button key={f} className="tap" onClick={() => onTradeInFactorChange(f)} style={{
                  flex: 1, padding: '8px 0', borderRadius: 10, fontSize: 13, fontWeight: 700,
                  background: tradeInFactor === f ? 'var(--accent)' : 'var(--bg-2)',
                  color: tradeInFactor === f ? '#fff' : 'var(--ink-2)',
                  border: '1px solid var(--hairline-soft)',
                }}>
                  {Math.round(f * 100)}%
                </button>
              ))}
            </div>
            <div style={{ fontSize: 12, color: 'var(--ink-3)', lineHeight: 1.5 }}>
              Your binder cards count at {Math.round(tradeInFactor * 100)}% of their market price toward {targets.length > 1 ? 'these' : 'this'}{' '}
              <span className="mono" style={{ fontWeight: 700, color: 'var(--ink)' }}>{fmtPrice(targetPrice, cur)}</span>{' '}
              {targets.length > 1 ? 'cards' : 'card'}.
            </div>
          </div>
        )}
      </div>

      {/* Build your own offer */}
      {targetPrice != null && (
        <div style={{ padding: '0 16px' }}>
          <div className="row" style={{ justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 10 }}>
            <span style={{ fontSize: 11, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase' }}>
              Build your offer
            </span>
          </div>
          {[0, 1].map(idx => (
            <ManualOptionPanel
              key={idx}
              label={`Option ${idx + 1}`}
              cards={manualOptions[idx]}
              targetPrice={targetPrice}
              tradeInFactor={tradeInFactor}
              cur={cur} tweaks={tweaks}
              onAdd={() => onAddCard(idx)}
              onRemove={cardId => onRemoveCard(idx, cardId)}
            />
          ))}
        </div>
      )}

      {/* Suggested combinations */}
      <div style={{ padding: '0 16px' }}>
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 10 }}>
          <span style={{ fontSize: 11, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase' }}>
            Suggested combinations
          </span>
          <span style={{ fontSize: 11, color: 'var(--ink-4)' }}>
            {pricedCount} of {candidateCount} cards priced
          </span>
        </div>

        {error && <div style={{ fontSize: 12, color: 'var(--neg)', marginBottom: 12 }}>{error}</div>}

        {offers === null && !error && (
          <div style={{ fontSize: 12, color: 'var(--ink-3)', textAlign: 'center', padding: '16px 0' }}>
            Searching your binder…
          </div>
        )}

        {offers !== null && options.length === 0 && !error && (
          <div style={{
            background: 'var(--bg-1)', border: '1px solid var(--hairline-soft)',
            borderRadius: 14, padding: 20, textAlign: 'center',
          }}>
            <div style={{ fontSize: 14, color: 'var(--ink-2)', marginBottom: 6 }}>No matches found</div>
            <div style={{ fontSize: 12, color: 'var(--ink-3)', lineHeight: 1.5 }}>
              {pricedCount === 0
                ? 'None of your "for trade" cards have prices yet — try refreshing prices first.'
                : `None of your ${pricedCount} priced "for trade" cards have a trade-in value close enough to ${fmtPrice(targetPrice, cur)}.`
              }
            </div>
          </div>
        )}

        {options.map((opt, i) => (
          <TradeOfferRow key={i} opt={opt} targetPrice={targetPrice} tradeInFactor={tradeInFactor} cur={cur} tweaks={tweaks}/>
        ))}
      </div>

      <div style={{ padding: '20px 16px 60px' }}>
        <button className="tap" onClick={onReset} style={{
          width: '100%', padding: 14, borderRadius: 14,
          background: 'var(--bg-2)', border: '1px solid var(--hairline-soft)',
          color: 'var(--ink-2)', fontSize: 14, fontWeight: 600,
        }}>
          Search another card
        </button>
      </div>
    </div>
  );
}

function TradeOfferRow({ opt, targetPrice, tradeInFactor, cur, tweaks }) {
  const [expanded, setExpanded] = useState(false);
  const tradeInValue = opt.total_value * tradeInFactor;
  // delta_to_target is computed against the *required* market value, so
  // scaling it by the trade-in factor gives the delta in trade-in terms
  // (i.e. against the original card's price): tradeInValue - targetPrice.
  const delta = opt.delta_to_target * tradeInFactor;
  const pct   = targetPrice ? (delta / targetPrice) * 100 : 0;
  const fair  = Math.abs(pct) <= 10;

  return (
    <button className="tap" onClick={() => setExpanded(x => !x)} style={{
      width: '100%', textAlign: 'left',
      background: 'var(--bg-1)', border: '1px solid var(--hairline-soft)',
      borderRadius: 14, padding: 14, marginBottom: 10,
    }}>
      {/* Row header */}
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', marginBottom: expanded ? 12 : 0 }}>
        <div className="row" style={{ gap: 10, alignItems: 'center' }}>
          {/* Card stack thumbnails */}
          <div className="row" style={{ gap: -6 }}>
            {opt.cards.slice(0, 3).map((c, j) => (
              <div key={j} style={{ marginLeft: j > 0 ? -14 : 0, zIndex: 3 - j }}>
                <CardArt card={{ ...c, usd: c.current_market_price }} renderMode={tweaks.cardRender} size="xs"/>
              </div>
            ))}
            {opt.cards.length > 3 && (
              <div style={{
                marginLeft: -14, width: 44, height: 62, borderRadius: 6,
                background: 'var(--bg-3)', border: '1px solid var(--hairline)',
                display: 'grid', placeItems: 'center',
                fontSize: 11, color: 'var(--ink-3)', fontWeight: 700,
                zIndex: 0,
              }}>+{opt.cards.length - 3}</div>
            )}
          </div>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600 }}>
              {opt.n_cards} card{opt.n_cards !== 1 ? 's' : ''}
            </div>
            <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>
              {fmtPrice(opt.total_value, cur)} → {fmtPrice(tradeInValue, cur)}
            </div>
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div className="mono" style={{
            fontSize: 12, fontWeight: 700,
            color: fair ? 'var(--accent)' : (delta > 0 ? 'var(--accent)' : 'var(--neg)'),
          }}>
            {delta >= 0 ? '+' : ''}{fmtPrice(delta, cur)}
          </div>
          <div style={{
            fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em',
            color: fair ? 'var(--accent)' : 'var(--ink-3)',
          }}>
            {fair ? 'fair' : (delta > 0 ? 'you win' : 'short')}
          </div>
        </div>
      </div>

      {/* Expanded card list */}
      {expanded && (
        <div style={{ borderTop: '1px solid var(--hairline-soft)', paddingTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {opt.cards.map((c, j) => (
            <div key={j} className="row" style={{ gap: 10, alignItems: 'center' }}>
              <CardArt card={{ ...c, usd: c.current_market_price }} renderMode={tweaks.cardRender} size="xs"/>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</div>
                <div style={{ fontSize: 10, color: 'var(--ink-3)' }}>{c.set_name} · {c.condition}</div>
              </div>
              <span className="mono" style={{ fontSize: 13, fontWeight: 600, flexShrink: 0 }}>
                {fmtPrice(c.current_market_price || 0, cur)}
              </span>
            </div>
          ))}
        </div>
      )}
    </button>
  );
}

/* ── Build-your-own offer panel ────────────────────────────────────────── */

function ManualOptionPanel({ label, cards, targetPrice, tradeInFactor, cur, tweaks, onAdd, onRemove }) {
  const total = cards.reduce((s, c) => s + (c.usd || 0), 0);
  const tradeInValue = total * tradeInFactor;
  const delta = targetPrice != null ? tradeInValue - targetPrice : null;
  const pct   = targetPrice ? (delta / targetPrice) * 100 : 0;
  const fair  = delta != null && Math.abs(pct) <= 10;

  return (
    <div style={{
      background: 'var(--bg-1)', border: '1px solid var(--hairline-soft)',
      borderRadius: 14, padding: 14, marginBottom: 10,
    }}>
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', marginBottom: cards.length ? 10 : 0 }}>
        <span style={{ fontSize: 13, fontWeight: 700 }}>{label}</span>
        {cards.length > 0 && (
          <div style={{ textAlign: 'right' }}>
            <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>
              {fmtPrice(total, cur)} → {fmtPrice(tradeInValue, cur)}
            </div>
            {delta != null && (
              <div className="mono" style={{
                fontSize: 12, fontWeight: 700,
                color: fair ? 'var(--accent)' : (delta > 0 ? 'var(--accent)' : 'var(--neg)'),
              }}>
                {delta >= 0 ? '+' : ''}{fmtPrice(delta, cur)} {fair ? '· fair' : (delta > 0 ? '· you win' : '· short')}
              </div>
            )}
          </div>
        )}
      </div>

      {cards.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 10 }}>
          {cards.map(c => (
            <div key={c.id} className="row" style={{ gap: 10, alignItems: 'center' }}>
              <CardArt card={c} renderMode={tweaks.cardRender} size="xs"/>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</div>
                <div style={{ fontSize: 10, color: 'var(--ink-3)' }}>
                  {c.set}{c.code ? ` · ${c.code}` : ''}
                </div>
              </div>
              <span className="mono" style={{ fontSize: 13, fontWeight: 600, flexShrink: 0 }}>
                {fmtPrice(c.usd || 0, cur)}
              </span>
              <button className="tap" onClick={() => onRemove(c.id)} style={{
                flexShrink: 0, width: 24, height: 24, borderRadius: 12,
                background: 'var(--bg-2)', color: 'var(--ink-3)',
                fontSize: 14, lineHeight: 1, display: 'grid', placeItems: 'center',
              }}>×</button>
            </div>
          ))}
        </div>
      )}

      <button className="tap" onClick={onAdd} style={{
        width: '100%', padding: '10px', borderRadius: 10,
        background: 'var(--bg-2)', border: '1px dashed var(--hairline)',
        color: 'var(--ink-2)', fontSize: 12, fontWeight: 600,
      }}>
        + Add card from binder
      </button>
    </div>
  );
}

/* ── Binder picker sheet ────────────────────────────────────────────────── */

function BinderPickerSheet({ collection, excludeIds, onPick, onClose, cur, tweaks }) {
  const [search, setSearch] = useState('');
  const [showAll, setShowAll] = useState(false);

  const pool = (collection || []).filter(c => c && c.name && !excludeIds.has(c.id));
  const tradeOnly = pool.filter(c => tagNamesOf(c).some(t => t.toLowerCase().includes('trade')));
  const base = (showAll || tradeOnly.length === 0) ? pool : tradeOnly;
  const q = search.trim().toLowerCase();
  const list = q
    ? base.filter(c => (c.name || '').toLowerCase().includes(q) || (c.set || '').toLowerCase().includes(q))
    : base;

  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, background: 'oklch(0 0 0 / 0.4)',
      zIndex: 50, display: 'flex', alignItems: 'flex-end',
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        background: 'var(--bg)', borderRadius: '20px 20px 0 0',
        width: '100%', maxHeight: '78vh', display: 'flex', flexDirection: 'column',
        boxShadow: '0 -8px 32px oklch(0 0 0 / 0.25)',
      }}>
        <div style={{ padding: '14px 16px 10px' }}>
          <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
            <span style={{ fontSize: 15, fontWeight: 700 }}>Pick a card</span>
            <button className="tap" onClick={onClose} style={{ fontSize: 13, fontWeight: 600, color: 'var(--accent)' }}>
              Done
            </button>
          </div>
          <input
            type="text" value={search} onChange={e => setSearch(e.target.value)}
            placeholder="Search your binder…"
            style={{
              width: '100%', background: 'var(--bg-2)', border: '1px solid var(--hairline-soft)',
              borderRadius: 10, padding: '8px 12px', color: 'var(--ink)', fontSize: 13,
              outline: 'none', marginBottom: (tradeOnly.length > 0 && tradeOnly.length !== pool.length) ? 8 : 0,
            }}
          />
          {tradeOnly.length > 0 && tradeOnly.length !== pool.length && (
            <div className="row" style={{ gap: 6 }}>
              <button className="tap" onClick={() => setShowAll(false)} style={{
                padding: '4px 10px', borderRadius: 999, fontSize: 11, fontWeight: 600,
                background: !showAll ? 'var(--ink)' : 'var(--bg-2)',
                color: !showAll ? 'var(--bg)' : 'var(--ink-2)',
                border: '1px solid var(--hairline-soft)',
              }}>For trade ({tradeOnly.length})</button>
              <button className="tap" onClick={() => setShowAll(true)} style={{
                padding: '4px 10px', borderRadius: 999, fontSize: 11, fontWeight: 600,
                background: showAll ? 'var(--ink)' : 'var(--bg-2)',
                color: showAll ? 'var(--bg)' : 'var(--ink-2)',
                border: '1px solid var(--hairline-soft)',
              }}>All cards ({pool.length})</button>
            </div>
          )}
        </div>

        <div style={{ overflowY: 'auto', flex: 1, padding: '0 16px 24px', display: 'flex', flexDirection: 'column', gap: 8 }}>
          {list.map(c => (
            <button key={c.id} className="tap" onClick={() => onPick(c)} style={{
              width: '100%', textAlign: 'left',
              display: 'flex', gap: 10, alignItems: 'center',
              background: 'var(--bg-1)', border: '1px solid var(--hairline-soft)',
              borderRadius: 10, padding: 8,
            }}>
              <CardArt card={c} renderMode={tweaks.cardRender} size="xs"/>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</div>
                <div style={{ fontSize: 10, color: 'var(--ink-3)' }}>{c.set}{c.code ? ` · ${c.code}` : ''}</div>
              </div>
              <span className="mono" style={{ fontSize: 13, fontWeight: 600, flexShrink: 0 }}>
                {fmtPrice(c.usd || 0, cur)}
              </span>
            </button>
          ))}
          {list.length === 0 && (
            <div style={{ fontSize: 12, color: 'var(--ink-3)', textAlign: 'center', padding: '24px 0' }}>
              No cards found.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ── Add another wanted card sheet ─────────────────────────────────────── */

function AddTargetSheet({ identifyCard, tweaks, cur, onAdd, onClose }) {
  const [mode, setMode]             = useState('search'); // search | scanning | picking | pricing
  const [query, setQuery]           = useState('');
  const [candidates, setCandidates] = useState([]);
  const [error, setError]           = useState(null);
  const fileInputRef = useRef(null);

  const pickCard = async (card) => {
    setMode('pricing');
    try {
      const { price, source } = await priceCard(card);
      onAdd({ card, price, source });
    } catch (e) {
      onAdd({ card, price: null, source: null });
    }
  };

  const runFind = async ({ image } = {}) => {
    if (!image && !query.trim()) return;
    setMode('scanning'); setError(null); setCandidates([]);
    try {
      const widened = await findCandidates({ query, image, identifyCard });
      if (!widened.length) {
        setError('No matches — try a different search or photo.');
        setMode('search');
        return;
      }
      if (widened.length === 1) {
        await pickCard(widened[0]);
      } else {
        setCandidates(widened);
        setMode('picking');
      }
    } catch (e) {
      setError(String(e.message || e).slice(0, 100));
      setMode('search');
    }
  };

  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, background: 'oklch(0 0 0 / 0.4)',
      zIndex: 50, display: 'flex', alignItems: 'flex-end',
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        background: 'var(--bg)', borderRadius: '20px 20px 0 0',
        width: '100%', maxHeight: '85vh', display: 'flex', flexDirection: 'column',
        boxShadow: '0 -8px 32px oklch(0 0 0 / 0.25)',
      }}>
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', padding: '14px 16px 0', flexShrink: 0 }}>
          <span style={{ fontSize: 15, fontWeight: 700 }}>Add another card</span>
          <button className="tap" onClick={onClose} style={{ fontSize: 13, fontWeight: 600, color: 'var(--accent)' }}>
            Cancel
          </button>
        </div>

        <div style={{ overflowY: 'auto', flex: 1, minHeight: 0 }}>
          {mode === 'search' && (
            <TradeIdleView
              query={query} setQuery={setQuery}
              onSearch={() => runFind({})}
              onCapture={() => fileInputRef.current?.click()}
              error={error}
            />
          )}

          {(mode === 'scanning' || mode === 'pricing') && (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '40px 24px', gap: 14 }}>
              <div style={{ fontSize: 13, color: 'var(--ink-3)' }}>
                {mode === 'scanning' ? 'Identifying card…' : 'Getting price…'}
              </div>
            </div>
          )}

          {mode === 'picking' && (
            <div style={{ padding: '10px 16px 24px', display: 'flex', flexDirection: 'column', gap: 8 }}>
              {candidates.map((c, i) => (
                <button key={i} className="tap" onClick={() => pickCard(c)} style={{
                  width: '100%', textAlign: 'left',
                  display: 'flex', gap: 10, alignItems: 'center',
                  background: 'var(--bg-1)', border: '1px solid var(--hairline-soft)',
                  borderRadius: 10, padding: 8,
                }}>
                  <CardArt card={c} renderMode={tweaks.cardRender} size="xs"/>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</div>
                    <div style={{ fontSize: 10, color: 'var(--ink-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {c.set}{c.code ? ` · ${c.code}` : ''}{c.lang && c.lang !== 'EN' ? ` · ${c.lang}` : ''}
                    </div>
                    {c.variant && (
                      <div style={{ fontSize: 10, color: 'var(--accent)', fontWeight: 700 }}>{c.variant}</div>
                    )}
                  </div>
                  {c.usd != null && (
                    <span className="mono" style={{ fontSize: 13, fontWeight: 600, flexShrink: 0 }}>
                      {fmtPrice(c.usd, cur)}
                    </span>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>

        <input ref={fileInputRef} type="file" accept="image/*" capture="environment"
          style={{ display: 'none' }} onChange={e => {
            const f = e.target.files?.[0];
            if (f) runFind({ image: f });
            e.target.value = '';
          }}/>
      </div>
    </div>
  );
}

export default TradeScreen
