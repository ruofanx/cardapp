/* PokeCollect — Home / Portfolio screen */

import React, { useState, useMemo, useRef, useEffect } from 'react'
import api from '../api.js'
import { CardArt, Icon, Price, Sparkline, Section, fmtUSD, fmtJPY, fmtPrice } from '../components.jsx'

const RANGE_META = {
  '1W':  { days: 7,    label: 'past week' },
  '1M':  { days: 30,   label: 'past 30 days' },
  '3M':  { days: 90,   label: 'past 90 days' },
  '1Y':  { days: 365,  label: 'past year' },
  'ALL': { days: 1095, label: 'all time' },
};

function HomeScreen({ tweaks, navigate, collection, currentUser, refreshPrice, backend }) {
  const cur = tweaks.currency;
  const [valueHidden, setValueHidden] = useState(false);
  const [range, setRange] = useState('1M');
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQ, setSearchQ] = useState('');
  // Always show the real collection — never substitute mock data here. Empty
  // state is handled below so the user sees the true backend state.
  const cards = Array.isArray(collection) ? collection : [];
  // Wishlist is stored as the lowercase tag `wishlist`. Owned cards exclude
  // wishlist items so portfolio totals / movers / watchlist stay accurate.
  // Wishlist items get their own surface further down.
  const hasWishlistTag = (c) => (c?.tags || []).some(t => {
    const name = typeof t === 'object' && t ? (t.name || t.label || '') : String(t);
    return name.trim().toLowerCase() === 'wishlist';
  });
  const ownedCards    = cards.filter(c => !hasWishlistTag(c));
  const wishlistCards = cards.filter(hasWishlistTag);
  const isLoading = backend?.online == null && cards.length === 0;
  const isEmpty = backend?.online === true && ownedCards.length === 0 && wishlistCards.length === 0;

  // ---- Backfill missing card images ----
  // Cards that haven't been priced yet have image_url === null. Calling
  // refreshPrice pulls a quote from the Pokemon TCG API / TCGdex and persists
  // both the price and image_url onto the row. We do this one card at a time
  // and remember which IDs we've already tried so we don't loop forever on
  // ones the API can't resolve.
  const triedRef = useRef(new Set());
  useEffect(() => {
    if (!refreshPrice || backend?.online === false) return;
    const needy = cards
      .filter(c => !c.image_url && !c._refreshing && !c._priceUnavailable && !triedRef.current.has(c.id))
      .slice(0, 6);
    if (needy.length === 0) return;
    let cancelled = false;
    (async () => {
      for (const c of needy) {
        if (cancelled) break;
        triedRef.current.add(c.id);
        try { await refreshPrice(c); } catch (_) { /* swallow — try next */ }
      }
    })();
    return () => { cancelled = true; };
  }, [cards, refreshPrice, backend?.online]);

  const totalUSD = ownedCards.reduce((s, c) => s + (Number(c.usd) || 0), 0);
  const sealedUSD = ownedCards.filter(c => api.isSealedProduct?.(c)).reduce((s, c) => s + (Number(c.usd) || 0), 0);
  const cardsUSD  = totalUSD - sealedUSD;

  // Real portfolio history from price_history DB. Fetched once; filtered by range client-side.
  const [allHistory, setAllHistory] = useState(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  useEffect(() => {
    if (!currentUser?.id || backend?.online === false) return;
    setHistoryLoading(true);
    const base = api.state?.base || 'http://localhost:8000';
    const token = api.state?.token;
    const headers = token ? { Authorization: `Bearer ${token}` } : {};
    fetch(`${base}/api/users/${currentUser.id}/portfolio-history?days=1095`, { headers })
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data?.points) setAllHistory(data.points); })
      .catch(() => {})
      .finally(() => setHistoryLoading(false));
  }, [currentUser?.id, backend?.online]);

  // Filter all history to the selected range and extract values for Sparkline.
  const meta = RANGE_META[range] || RANGE_META['1M'];
  const cutoff = useMemo(() => {
    const d = new Date();
    d.setDate(d.getDate() - meta.days);
    return d.toISOString().slice(0, 10);
  }, [meta.days]);

  const portfolio = useMemo(() => {
    if (!allHistory || allHistory.length === 0) return [totalUSD, totalUSD];
    const pts = allHistory.filter(p => p.date >= cutoff);
    if (pts.length === 0) {
      // All history is older than range — show flat line at current value
      return [totalUSD, totalUSD];
    }
    const values = pts.map(p => p.value);
    // Ensure the last point matches current total so the chart ends at reality
    values[values.length - 1] = totalUSD;
    return values;
  }, [allHistory, cutoff, totalUSD]);

  // 24h change: compare yesterday's last known price to today.
  const change24h = useMemo(() => {
    if (!allHistory || allHistory.length < 2) return 0;
    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);
    const cutoff24h = yesterday.toISOString().slice(0, 10);
    const before = [...allHistory].filter(p => p.date <= cutoff24h).pop();
    return before ? totalUSD - before.value : 0;
  }, [allHistory, totalUSD]);

  const changeRange = totalUSD - portfolio[0];
  const changeRangePct = portfolio[0] ? (changeRange / portfolio[0]) * 100 : 0;
  const cfg = meta;

  // Only cards with a recorded purchase_price have a meaningful gain_loss_pct
  const moversList = [...ownedCards].filter(c => c.change != null && c.change !== 0).sort((a, b) => (b.change || 0) - (a.change || 0));
  const movers = moversList.slice(0, 3);
  const losers = moversList.filter(c => c.change < 0).slice(-2).reverse();
  const recentScans = ownedCards.slice(0, 4);
  const watchlist = ownedCards.filter(c => (c.usd || 0) > 5).slice(0, 3);

  // Per-card price history for Watchlist sparklines — fetched for the top 3 watchlist cards.
  const [cardHistories, setCardHistories] = useState({});
  useEffect(() => {
    if (!watchlist.length || backend?.online === false) return;
    const base = api.state?.base || 'http://localhost:8000';
    const token = api.state?.token;
    const headers = token ? { Authorization: `Bearer ${token}` } : {};
    watchlist.forEach(c => {
      if (cardHistories[c.id]) return;
      fetch(`${base}/api/cards/${c.id}/price-history`, { headers })
        .then(r => r.ok ? r.json() : null)
        .then(data => {
          if (data?.points?.length) {
            setCardHistories(prev => ({
              ...prev,
              [c.id]: data.points.slice(-16).map(p => p.price),
            }));
          }
        })
        .catch(() => {});
    });
  // Only refetch when watchlist card IDs change
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [watchlist.map(c => c.id).join(','), backend?.online]);
  // Wishlist preview — top 6 by market value so the most valuable targets are front-and-center.
  const wishlistPreview = [...wishlistCards].sort((a, b) => (b.usd || 0) - (a.usd || 0)).slice(0, 6);

  // ---- Search ----
  const q = searchQ.trim().toLowerCase();
  const searchResults = q
    ? cards.filter(c =>
        (c.name && c.name.toLowerCase().includes(q)) ||
        (c.set && c.set.toLowerCase().includes(q)) ||
        (c.code && String(c.code).toLowerCase().includes(q))
      ).slice(0, 12)
    : [];

  const userInitial = (currentUser?.name || 'You').trim().charAt(0).toUpperCase();

  // Loading skeleton (first fetch in flight). Backend status is still unknown
  // and we have no cards yet — show a quiet skeleton instead of mock data.
  if (isLoading) {
    return (
      <div className="screen">
        <div className="screen-scroll" style={{ paddingBottom: 24 }}>
          <div style={{ padding: '14px 16px 0', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div className="row gap-2">
              <div className="foil" style={{ width: 26, height: 26, borderRadius: 7, animation: 'foilRot 18s linear infinite' }} />
              <div style={{ fontWeight: 600, fontSize: 15, letterSpacing: '-0.01em' }}>PokeCollect</div>
            </div>
          </div>
          <div style={{ padding: '40px 16px', textAlign: 'center', color: 'var(--ink-3)' }}>
            <div style={{ fontSize: 13, fontWeight: 500 }}>Loading your collection…</div>
          </div>
        </div>
      </div>
    );
  }

  // True empty state — backend is reachable but the user hasn't added cards
  // for the active account yet. Point them at Scan / Binder rather than
  // pretending they own demo cards.
  if (isEmpty) {
    return (
      <div className="screen">
        <div className="screen-scroll" style={{ paddingBottom: 24 }}>
          <div style={{ padding: '14px 16px 0', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div className="row gap-2">
              <div className="foil" style={{ width: 26, height: 26, borderRadius: 7, animation: 'foilRot 18s linear infinite' }} />
              <div style={{ fontWeight: 600, fontSize: 15, letterSpacing: '-0.01em' }}>PokeCollect</div>
            </div>
            <button className="tap" onClick={() => navigate('settings')} style={{
              width: 28, height: 28, borderRadius: 14,
              background: 'linear-gradient(135deg, var(--accent), oklch(0.55 0.12 calc(var(--accent-hue) - 40)))',
              color: 'var(--accent-ink)', display: 'grid', placeItems: 'center',
              fontWeight: 700, fontSize: 12,
            }}>{userInitial}</button>
          </div>
          <div style={{ padding: '64px 24px 0', textAlign: 'center' }}>
            <div className="foil" style={{ width: 72, height: 72, borderRadius: 18, margin: '0 auto 16px', animation: 'foilRot 18s linear infinite' }}/>
            <div style={{ fontSize: 20, fontWeight: 600, letterSpacing: '-0.01em' }}>No cards yet</div>
            <div style={{ fontSize: 13, color: 'var(--ink-3)', marginTop: 6, lineHeight: 1.5 }}>
              {currentUser?.name ? `${currentUser.name}'s` : 'Your'} collection is empty. Scan a card or open Binder to add your first one.
            </div>
            <div className="row gap-2" style={{ justifyContent: 'center', marginTop: 22 }}>
              <button className="tap" onClick={() => navigate('scan')} style={{
                padding: '10px 18px', borderRadius: 12,
                background: 'var(--accent)', color: 'var(--accent-ink)',
                fontWeight: 600, fontSize: 14,
              }}>Scan a card</button>
              <button className="tap" onClick={() => navigate('browse')} style={{
                padding: '10px 18px', borderRadius: 12,
                background: 'var(--bg-2)', color: 'var(--ink)',
                fontWeight: 600, fontSize: 14,
              }}>Open Binder</button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="screen">
      <div className="screen-scroll" style={{ paddingBottom: 24 }}>
        {/* Status header */}
        <div style={{ padding: '14px 16px 0', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div className="row gap-2">
            <div className="foil" style={{ width: 26, height: 26, borderRadius: 7, animation: 'foilRot 18s linear infinite' }} />
            <div style={{ fontWeight: 600, fontSize: 15, letterSpacing: '-0.01em' }}>PokeCollect</div>
          </div>
          <div className="row gap-3" style={{ color: 'var(--ink-2)', alignItems: 'center' }}>
            <button className="tap" onClick={() => setSearchOpen(v => !v)} style={{ color: searchOpen ? 'var(--accent)' : 'inherit' }}>
              <Icon name="search" size={20}/>
            </button>
            <button className="tap" style={{ position: 'relative' }} aria-label="Notifications" onClick={() => navigate('browse')}>
              <Icon name="bell" size={20}/>
            </button>
            {/* Profile avatar — opens You / settings */}
            <button className="tap" onClick={() => navigate('settings')} style={{
              width: 28, height: 28, borderRadius: 14,
              background: 'linear-gradient(135deg, var(--accent), oklch(0.55 0.12 calc(var(--accent-hue) - 40)))',
              color: 'var(--accent-ink)',
              display: 'grid', placeItems: 'center',
              fontWeight: 700, fontSize: 12, letterSpacing: 0,
              boxShadow: '0 1px 4px var(--accent-glow)',
            }} aria-label="Open profile">{userInitial}</button>
          </div>
        </div>

        {/* Inline search panel */}
        {searchOpen && (
          <div style={{ padding: '12px 16px 0' }}>
            <div style={{
              display: 'flex', alignItems: 'center', gap: 8,
              background: 'var(--bg-1)', border: '1px solid var(--hairline-soft)',
              borderRadius: 12, padding: '8px 12px',
            }}>
              <Icon name="search" size={16}/>
              <input
                autoFocus
                value={searchQ}
                onChange={e => setSearchQ(e.target.value)}
                placeholder="Search your cards"
                style={{
                  flex: 1, background: 'transparent', border: 'none', outline: 'none',
                  color: 'var(--ink)', fontSize: 14, fontWeight: 500,
                }}
              />
              {searchQ && (
                <button className="tap" onClick={() => setSearchQ('')} style={{ color: 'var(--ink-3)', fontSize: 12 }}>Clear</button>
              )}
              <button className="tap" onClick={() => { setSearchOpen(false); setSearchQ(''); }} style={{ color: 'var(--ink-3)', fontSize: 12 }}>Done</button>
            </div>
            {q && (
              <div className="col" style={{ marginTop: 8, gap: 0 }}>
                {searchResults.length === 0 && (
                  <div style={{ padding: '12px 4px', color: 'var(--ink-3)', fontSize: 13 }}>
                    No cards matching "{searchQ}".
                  </div>
                )}
                {searchResults.map((c, i) => (
                  <button key={c.id} className="tap" onClick={() => { setSearchOpen(false); setSearchQ(''); navigate('detail', { card: c }); }} style={{
                    display: 'grid', gridTemplateColumns: '40px 1fr auto', gap: 10, alignItems: 'center',
                    padding: '10px 4px', textAlign: 'left',
                    borderTop: i === 0 ? 'none' : '1px solid var(--hairline-soft)',
                  }}>
                    <CardArt card={c} renderMode={tweaks.cardRender} size="xs" flat/>
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontSize: 14, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</div>
                      <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>{c.code} · {c.set}</div>
                    </div>
                    <Price usd={c.usd} currency={cur === 'BOTH' ? 'USD' : cur} size="sm"/>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Portfolio value */}
        <div style={{ padding: '20px 16px 8px' }}>
          <div className="row gap-2" style={{ color: 'var(--ink-3)', fontSize: 12, fontWeight: 500, letterSpacing: '0.04em', textTransform: 'uppercase' }}>
            <span>Portfolio · {ownedCards.length} card{ownedCards.length === 1 ? '' : 's'}</span>
            <button className="tap" onClick={() => setValueHidden(v => !v)} style={{ color: 'var(--ink-3)', display: 'inline-flex' }}>
              <Icon name={valueHidden ? 'eye-off' : 'eye'} size={14} stroke={1.8}/>
            </button>
          </div>
          <div style={{ marginTop: 6, display: 'flex', alignItems: 'baseline', gap: 10 }}>
            {valueHidden ? (
              <span className="mono" style={{ fontSize: 40, fontWeight: 600, letterSpacing: '-0.04em' }}>••••••</span>
            ) : (
              <Price usd={totalUSD} currency={cur} size="xxl" decimals={0} />
            )}
            <span className={`mono ${change24h >= 0 ? 'delta-pos' : 'delta-neg'}`} style={{ fontSize: 14, fontWeight: 500 }}>
              {change24h >= 0 ? '+' : ''}{fmtUSD(change24h, { decimals: 0 })} 24h
            </span>
          </div>
          {cur === 'BOTH' && !valueHidden && (
            <div className="mono" style={{ color: 'var(--ink-3)', fontSize: 14, marginTop: 2 }}>
              {fmtJPY(totalUSD)}
            </div>
          )}
          {sealedUSD > 0 && !valueHidden && (
            <div style={{ fontSize: 13, color: 'var(--ink-3)', marginTop: 2 }}>
              Cards {fmtUSD(cardsUSD, { decimals: 0 })} · Sealed {fmtUSD(sealedUSD, { decimals: 0 })}
            </div>
          )}
          <div className="row gap-3" style={{ marginTop: 4, fontSize: 13 }}>
            <span className={changeRange >= 0 ? 'delta-pos' : 'delta-neg'}>
              <span className="mono">{changeRange >= 0 ? '+' : ''}{fmtUSD(changeRange, { decimals: 0 })}</span>
              <span className="mono" style={{ marginLeft: 6 }}>({changeRangePct >= 0 ? '+' : ''}{changeRangePct.toFixed(1)}%)</span>
            </span>
            <span style={{ color: 'var(--ink-3)' }}>{cfg.label}</span>
          </div>
        </div>

        {/* Chart */}
        {(() => {
          const minP = Math.min(...portfolio);
          const maxP = Math.max(...portfolio);
          const range_ = maxP - minP;
          const midP = (minP + maxP) / 2;
          const base = portfolio[0] || 1;
          const pct = (v) => ((v - base) / base) * 100;
          const ticks = range_ > 0.01 ? [
            { v: maxP, label: 'High' },
            { v: midP, label: 'Mid'  },
            { v: minP, label: 'Low'  },
          ] : [{ v: maxP, label: '' }];
          const hasRealHistory = allHistory && allHistory.length > 1 &&
            allHistory.some(p => p.date >= cutoff);
          return (
        <div style={{ padding: '12px 0 18px' }}>
          <div style={{ position: 'relative', height: 120, paddingRight: 60 }}>
            <Sparkline data={portfolio} w={298} h={120} stroke={1.5} fill={true} color="var(--accent)" />
            {!hasRealHistory && !historyLoading && (
              <div style={{
                position: 'absolute', inset: 0, display: 'flex', alignItems: 'center',
                justifyContent: 'center', pointerEvents: 'none',
              }}>
                <span style={{ fontSize: 11, color: 'var(--ink-4)', background: 'var(--bg-0)', padding: '2px 8px', borderRadius: 6 }}>
                  Price history building…
                </span>
              </div>
            )}
            <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', display: 'flex', flexDirection: 'column', justifyContent: 'space-between', padding: '0 16px' }}>
              {[0, 1, 2].map(i => <div key={i} style={{ height: 1, background: 'var(--hairline-soft)' }}/>)}
            </div>
            {/* Y-axis labels: price + % vs start of selected range */}
            <div style={{
              position: 'absolute', top: 0, bottom: 0, right: 12, width: 56,
              display: 'flex', flexDirection: 'column', justifyContent: 'space-between',
              pointerEvents: 'none',
            }}>
              {ticks.map((t, i) => {
                const p = pct(t.v);
                const align = i === 0 ? 'flex-start' : i === 2 ? 'flex-end' : 'center';
                return (
                  <div key={t.label} style={{
                    display: 'flex', flexDirection: 'column', alignItems: 'flex-end',
                    justifyContent: align, lineHeight: 1.1,
                  }}>
                    <span className="mono" style={{ fontSize: 11, fontWeight: 600, color: 'var(--ink-2)' }}>
                      {fmtUSD(t.v, { decimals: 0 })}
                    </span>
                    <span className={`mono ${p >= 0 ? 'delta-pos' : 'delta-neg'}`} style={{ fontSize: 10, fontWeight: 500 }}>
                      {p >= 0 ? '+' : ''}{p.toFixed(1)}%
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
          <div className="row" style={{ padding: '10px 16px 0', gap: 6, justifyContent: 'space-between' }}>
            {['1W', '1M', '3M', '1Y', 'ALL'].map((r) => {
              const active = r === range;
              return (
                <button key={r} className="tap" onClick={() => setRange(r)} style={{
                  flex: 1, padding: '6px 0', borderRadius: 8,
                  background: active ? 'var(--bg-2)' : 'transparent',
                  color: active ? 'var(--ink)' : 'var(--ink-3)',
                  fontSize: 12, fontWeight: 600, letterSpacing: '0.02em',
                  transition: 'background 120ms ease, color 120ms ease',
                }}>{r}</button>
              );
            })}
          </div>
        </div>
          );
        })()}

        {/* Movers — only shown when at least one card has purchase price tracked */}
        {moversList.length > 0 && (
        <Section title="Biggest movers" right={<button className="tap" onClick={() => navigate('browse')} style={{ color: 'var(--ink-3)', fontSize: 13 }}>See all</button>}>
          <div className="col gap-2" style={{ padding: '0 16px' }}>
            {[...movers, ...losers].slice(0, 4).map(c => (
              <button key={c.id} className="tap" onClick={() => navigate('detail', { card: c })} style={{
                display: 'grid', gridTemplateColumns: '52px 1fr auto', gap: 12, alignItems: 'center',
                padding: '10px 12px', background: 'var(--bg-1)', borderRadius: 14, textAlign: 'left',
                border: '1px solid var(--hairline-soft)',
              }}>
                <CardArt card={c} renderMode={tweaks.cardRender} size="sm" flat/>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 14, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</div>
                  <div className="row gap-2" style={{ fontSize: 11, color: 'var(--ink-3)' }}>
                    <span className="mono">{c.code}</span>
                    <span>·</span>
                    <span>{c.set}</span>
                  </div>
                </div>
                <div style={{ textAlign: 'right' }}>
                  <Price usd={c.usd} currency={cur} size="sm"/>
                  <div className={`mono ${c.change >= 0 ? 'delta-pos' : 'delta-neg'}`} style={{ fontSize: 11, fontWeight: 500 }}>
                    {c.change >= 0 ? '+' : ''}{(c.change).toFixed(1)}%
                  </div>
                </div>
              </button>
            ))}
          </div>
        </Section>
        )}

        {/* Recent scans */}
        <Section title="Recent scans" right={<button className="tap" onClick={() => navigate('browse')} style={{ color: 'var(--ink-3)', fontSize: 13 }}>Browse</button>}>
          <div style={{ display: 'flex', gap: 10, padding: '0 16px', overflowX: 'auto', scrollbarWidth: 'none' }}>
            {recentScans.map(c => (
              <button key={c.id} className="tap" onClick={() => navigate('detail', { card: c })} style={{ width: 110, flexShrink: 0, textAlign: 'left' }}>
                <CardArt card={c} renderMode={tweaks.cardRender} size="md"/>
                <div style={{ fontSize: 12, fontWeight: 500, marginTop: 6, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</div>
                <Price usd={c.usd} currency={cur === 'BOTH' ? 'USD' : cur} size="xs"/>
              </button>
            ))}
          </div>
        </Section>

        {/* Wishlist — cards tagged "wishlist". Hidden when empty so the home
            screen doesn't grow a permanent dead section. "See all" deep-links
            into Browse with the Wishlist filter chip pre-selected. */}
        {wishlistCards.length > 0 && (
          <Section
            title={`Wishlist · ${wishlistCards.length}`}
            right={
              <button className="tap"
                onClick={() => navigate('browse', { filter: 'wishlist' })}
                style={{ color: 'var(--accent)', fontSize: 13, fontWeight: 600 }}>
                See all
              </button>
            }
          >
            <div style={{ display: 'flex', gap: 10, padding: '0 16px', overflowX: 'auto', scrollbarWidth: 'none' }}>
              {wishlistPreview.map(c => (
                <button key={c.id} className="tap" onClick={() => navigate('detail', { card: c })}
                  style={{ width: 110, flexShrink: 0, textAlign: 'left', position: 'relative' }}>
                  <div style={{ position: 'relative' }}>
                    <CardArt card={c} renderMode={tweaks.cardRender} size="md"/>
                    {/* Star badge so wishlist tiles are visually distinct
                        from Recent scans on first glance. */}
                    <div style={{
                      position: 'absolute', top: 4, right: 4,
                      width: 22, height: 22, borderRadius: 11,
                      background: 'var(--accent)', color: 'var(--accent-ink)',
                      display: 'grid', placeItems: 'center',
                      boxShadow: '0 2px 6px oklch(0 0 0 / 0.4)',
                    }}>
                      <Icon name="star" size={12} fill="currentColor" stroke={0}/>
                    </div>
                  </div>
                  <div style={{ fontSize: 12, fontWeight: 500, marginTop: 6, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</div>
                  <Price usd={c.usd} currency={cur === 'BOTH' ? 'USD' : cur} size="xs"/>
                </button>
              ))}
              {/* Trailing add-more affordance so the user can extend the
                  list without leaving Home first. */}
              <button className="tap" onClick={() => navigate('scan')} style={{
                width: 110, flexShrink: 0, height: 154, borderRadius: 9,
                border: '1px dashed var(--hairline)',
                background: 'transparent', color: 'var(--ink-3)',
                display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 6,
              }}>
                <Icon name="plus" size={18}/>
                <span style={{ fontSize: 11, fontWeight: 500 }}>Add to wishlist</span>
              </button>
            </div>
          </Section>
        )}

        {/* Watchlist */}
        <Section title="Watchlist">
          <div className="col" style={{ padding: '0 16px', gap: 0 }}>
            {watchlist.map((c, i) => (
              <button key={c.id} className="tap" onClick={() => navigate('detail', { card: c })} style={{
                display: 'grid', gridTemplateColumns: 'auto 1fr 70px auto', gap: 12, alignItems: 'center',
                padding: '12px 0', textAlign: 'left',
                borderTop: i === 0 ? 'none' : '1px solid var(--hairline-soft)',
              }}>
                <CardArt card={c} renderMode={tweaks.cardRender} size="sm" flat/>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 14, fontWeight: 500 }}>{c.name}</div>
                  <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>{c.code} · {c.set}</div>
                </div>
                <Sparkline data={cardHistories[c.id]?.length > 1 ? cardHistories[c.id] : [c.usd, c.usd]} w={70} h={28} stroke={1.4} color={c.change >= 0 ? 'var(--pos)' : 'var(--neg)'}/>
                <div style={{ textAlign: 'right' }}>
                  <Price usd={c.usd} currency={cur === 'BOTH' ? 'USD' : cur} size="sm"/>
                  <div className={`mono ${c.change >= 0 ? 'delta-pos' : 'delta-neg'}`} style={{ fontSize: 11 }}>
                    {c.change >= 0 ? '+' : ''}{(c.change).toFixed(1)}%
                  </div>
                </div>
              </button>
            ))}
          </div>
        </Section>

        {/* Bulk threshold info card */}
        {(() => {
          const bulk = ownedCards.filter(c => c.bulk || (c.usd > 0 && c.usd < 5));
          const bulkVal = bulk.reduce((s, c) => s + (c.usd || 0), 0);
          if (bulk.length === 0) return null;
          return (
            <div style={{ padding: '8px 16px 0' }}>
              <div style={{
                background: 'var(--bg-1)',
                border: '1px solid var(--hairline-soft)',
                borderRadius: 14,
                padding: 14,
                display: 'flex', gap: 12, alignItems: 'center',
              }}>
                <div className="foil-soft" style={{ width: 36, height: 36, borderRadius: 10, flexShrink: 0 }}/>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>{bulk.length} bulk card{bulk.length === 1 ? '' : 's'} · {fmtPrice(bulkVal, cur, { decimals: 0 })}</div>
                  <div style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: 2 }}>Cards under {fmtPrice(5, cur === 'BOTH' ? 'USD' : cur, { decimals: 0 })} median, hidden from active tracking.</div>
                </div>
                <Icon name="chevron-right" size={18} />
              </div>
            </div>
          );
        })()}
      </div>
    </div>
  );
}

export default HomeScreen
