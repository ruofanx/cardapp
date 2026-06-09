/* PokeCollect — Home / Portfolio screen */

const { useState: useStateHome, useMemo: useMemoHome } = React;

// ---------------------------------------------------------------------------
// Synthesize a portfolio series for a given time range.
// Returns { series, points, label, changeLabel }.
// Different ranges produce different lengths + volatility so the chart and
// the 24h / 30d / Yr change labels visibly switch when you tap a range.
// ---------------------------------------------------------------------------
function buildPortfolioSeries(totalUSD, range) {
  const cfgs = {
    '1D': { points: 24, drawdown: 0.985, vol: 0.003, label: '24h',      compareIdx: -2 },
    '1W': { points: 28, drawdown: 0.96,  vol: 0.006, label: 'past week',   compareIdx: 0 },
    '1M': { points: 30, drawdown: 0.88,  vol: 0.010, label: 'past 30 days', compareIdx: 0 },
    '3M': { points: 90, drawdown: 0.78,  vol: 0.012, label: 'past 90 days', compareIdx: 0 },
    '1Y': { points: 52, drawdown: 0.55,  vol: 0.020, label: 'past year',  compareIdx: 0 },
    'ALL':{ points: 80, drawdown: 0.18,  vol: 0.035, label: 'all time',   compareIdx: 0 },
  };
  const cfg = cfgs[range] || cfgs['1M'];
  if (totalUSD <= 0) return { series: new Array(cfg.points).fill(0), cfg };
  const out = [];
  const start = totalUSD * cfg.drawdown;
  // Seed pseudo-random by range so switching tabs gives a stable curve per range.
  const seed = range.split('').reduce((a, c) => a + c.charCodeAt(0), 0);
  let r = seed;
  const rand = () => { r = (r * 9301 + 49297) % 233280; return r / 233280; };
  for (let i = 0; i < cfg.points; i++) {
    const t = i / (cfg.points - 1);
    const noise = Math.sin(i * 0.18 + seed) * (totalUSD * cfg.vol * 0.6)
                + (rand() - 0.45) * (totalUSD * cfg.vol);
    out.push(start + (totalUSD - start) * t + noise);
  }
  out[out.length - 1] = totalUSD;
  return { series: out, cfg };
}

function HomeScreen({ tweaks, navigate, collection, currentUser, refreshPrice, backend }) {
  const cur = tweaks.currency;
  const [valueHidden, setValueHidden] = useStateHome(false);
  const [range, setRange] = useStateHome('1M');
  const [searchOpen, setSearchOpen] = useStateHome(false);
  const [searchQ, setSearchQ] = useStateHome('');
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
  const triedRef = React.useRef(new Set());
  React.useEffect(() => {
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
  const sealedUSD = ownedCards.filter(c => window.api?.isSealedProduct?.(c)).reduce((s, c) => s + (Number(c.usd) || 0), 0);
  const cardsUSD  = totalUSD - sealedUSD;

  const { series: portfolio, cfg } = useMemoHome(
    () => buildPortfolioSeries(totalUSD, range),
    [totalUSD, range]
  );

  // 24h delta is always vs. the previous tick of the *daily* range so the
  // "+$5 24h" stays meaningful regardless of which tab is selected.
  const { series: dailySeries } = useMemoHome(
    () => buildPortfolioSeries(totalUSD, '1D'),
    [totalUSD]
  );
  const change24h = totalUSD - dailySeries[dailySeries.length - 2];

  const changeRange = totalUSD - portfolio[0];
  const changeRangePct = portfolio[0] ? (changeRange / portfolio[0]) * 100 : 0;

  const moversList = [...ownedCards].filter(c => c.change != null).sort((a, b) => (b.change || 0) - (a.change || 0));
  const movers = moversList.slice(0, 3);
  const losers = moversList.slice(-2).reverse();
  const recentScans = ownedCards.slice(0, 4);
  const watchlist = ownedCards.filter(c => (c.usd || 0) > 5).slice(0, 3);
  // Wishlist preview — first 6 by recency, room to grow when there are more.
  const wishlistPreview = wishlistCards.slice(0, 6);

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
            <button className="tap" style={{ position: 'relative' }} aria-label="Notifications">
              <Icon name="bell" size={20}/>
              <span style={{ position: 'absolute', top: 0, right: 0, width: 6, height: 6, borderRadius: 3, background: 'var(--accent)' }}/>
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
          const midP = (minP + maxP) / 2;
          const base = portfolio[0] || 1;
          const pct = (v) => ((v - base) / base) * 100;
          const ticks = [
            { v: maxP, label: 'High' },
            { v: midP, label: 'Mid'  },
            { v: minP, label: 'Low'  },
          ];
          return (
        <div style={{ padding: '12px 0 18px' }}>
          <div style={{ position: 'relative', height: 120, paddingRight: 60 }}>
            <Sparkline data={portfolio} w={298} h={120} stroke={1.5} fill={true} color="var(--accent)" />
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
            {['1D', '1W', '1M', '3M', '1Y', 'ALL'].map((r) => {
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

        {/* Movers */}
        <Section title="Biggest movers · 24h" right={<button className="tap" onClick={() => navigate('browse')} style={{ color: 'var(--ink-3)', fontSize: 13 }}>See all</button>}>
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
                    {c.change >= 0 ? '+' : ''}{(c.change * 100).toFixed(1)}%
                  </div>
                </div>
              </button>
            ))}
          </div>
        </Section>

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
                <Sparkline data={[c.usd*0.92, c.usd*0.95, c.usd*0.91, c.usd*0.97, c.usd*0.99, c.usd*0.96, c.usd*1.02, c.usd*0.98, c.usd]} w={70} h={28} stroke={1.4} color={c.change >= 0 ? 'var(--pos)' : 'var(--neg)'}/>
                <div style={{ textAlign: 'right' }}>
                  <Price usd={c.usd} currency={cur === 'BOTH' ? 'USD' : cur} size="sm"/>
                  <div className={`mono ${c.change >= 0 ? 'delta-pos' : 'delta-neg'}`} style={{ fontSize: 11 }}>
                    {c.change >= 0 ? '+' : ''}{(c.change * 100).toFixed(1)}%
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

function Section({ title, right, children }) {
  return (
    <div style={{ marginTop: 22 }}>
      <div className="row" style={{ padding: '0 16px 10px', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <div style={{ fontSize: 11, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase' }}>{title}</div>
        {right}
      </div>
      {children}
    </div>
  );
}

window.HomeScreen = HomeScreen;
