/* Browse / Collection — search + filter + grid */

import React, { useState, useMemo, useEffect } from 'react'
import api from '../api.js'
import { CardArt, Icon, Price, fmtPrice, NavBar, ProductTypeBadge, tagNamesOf } from '../components.jsx'

const GRADER_LOGOS = { PSA: 1, BGS: 1, CGC: 1, SGC: 1, HGA: 1 };

function gradeLabel(grader, grade) {
  if (grade === 10.5) {
    if (grader === 'BGS') return 'Black Label';
    return '10 Pristine';
  }
  return grade === Math.floor(grade) ? String(Math.floor(grade)) : String(grade);
}

function GradingBadge({ grader, grade }) {
  if (!grader || grade == null) return null;
  const key = grader?.toUpperCase();
  const hasLogo = GRADER_LOGOS[key];
  return (
    <div style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      background: 'var(--bg-1)', border: '1px solid var(--hairline-soft)',
      borderRadius: 6, padding: '3px 6px 3px 4px',
      boxShadow: '0 1px 3px oklch(0 0 0 / 0.18)',
    }}>
      {hasLogo
        ? <img src={`/grading-logos/${key.toLowerCase()}.svg`} alt={key}
            style={{ height: 14, borderRadius: 2, display: 'block' }}/>
        : <span style={{ fontSize: 8, fontWeight: 800, letterSpacing: '0.05em', color: 'var(--ink-2)' }}>{key}</span>
      }
      <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--ink)', letterSpacing: '-0.01em' }}>
        {gradeLabel(grader, grade)}
      </span>
    </div>
  );
}

// Backend returns tags as objects like {id, name, color, ...}; older mock data
// has plain strings. Normalize to a name list per card.
function BrowseScreen({ tweaks, navigate, collection, reloadCollection, removeCard, backend, params }) {
  const cur = tweaks.currency;
  const [view, setView] = useState('grid');
  const [sort, setSort] = useState('value');
  const [query, setQuery] = useState('');
  // Allow navigate('browse', {filter:'wishlist'}) to deep-link into a chip
  // selection — Home's Wishlist section uses this.
  const [filter, setFilter] = useState(() => params?.filter || 'all');
  // Selected tag names (case preserved for display; matched case-insensitively).
  // Set semantics — multi-select with AND filter (card must have ALL selected).
  const [selectedTags, setSelectedTags] = useState(() => new Set());
  const [setLogos, setSetLogos] = useState(null); // null = not yet fetched
  const [setTotals, setSetTotals] = useState({});
  const [setIds, setSetIds] = useState({});       // normName -> TCGdex set id
  const [confirmDeleteId, setConfirmDeleteId] = useState(null);
  const [completionSheet, setCompletionSheet] = useState(null); // { setName, setId, owned }

  useEffect(() => {
    if (view !== 'set' || setLogos !== null) return;
    Promise.all([
      fetch('https://api.tcgdex.net/v2/en/sets').then(r => r.json()).catch(() => []),
      fetch('https://api.tcgdex.net/v2/ja/sets').then(r => r.json()).catch(() => []),
    ]).then(([en, ja]) => {
      const logos = {};
      const totals = {};
      const ids = {};
      const norm = n => n.toLowerCase().replace(/[^a-z0-9]/g, '');
      [...(en || []), ...(ja || [])].forEach(s => {
        if (!s.name) return;
        const key = norm(s.name);
        if (s.logo) logos[key] = s.logo + '.png';
        if (s.id) ids[key] = s.id;
        if (s.cardCount?.total) totals[key] = s.cardCount.total;
        else if (s.cardCount?.official) totals[key] = s.cardCount.official;
      });
      setSetLogos(logos);
      setSetTotals(totals);
      setSetIds(ids);
    });
  }, [view, setLogos]);

  // Collect distinct tag names across the user's collection, sorted by frequency.
  // We hide the "wishlist" tag from this row because it already has its own
  // top-level filter chip — listing it twice would be confusing.
  const allTags = useMemo(() => {
    const counts = new Map();          // lowercased name → { display, count }
    for (const c of (collection || [])) {
      for (const t of tagNamesOf(c)) {
        if (t.toLowerCase() === 'wishlist') continue;
        const key = t.toLowerCase();
        const entry = counts.get(key) || { display: t, count: 0 };
        entry.count += 1;
        counts.set(key, entry);
      }
    }
    return Array.from(counts.values())
      .sort((a, b) => b.count - a.count || a.display.localeCompare(b.display));
  }, [collection]);

  const toggleTag = (name) => {
    setSelectedTags(prev => {
      const next = new Set(prev);
      const key = name.toLowerCase();
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };
  const clearTags = () => setSelectedTags(new Set());

  // Count how many cards in the WHOLE collection share the same name+set+code.
  // Used to show a "x2" badge when the user owns multiples.
  const copyCount = useMemo(() => {
    const counts = {};
    (collection || []).forEach(c => {
      const key = `${(c.name || '').toLowerCase()}|${c.set || ''}|${c.code || ''}`;
      counts[key] = (counts[key] || 0) + 1;
    });
    return counts;
  }, [collection]);

  const items = useMemo(() => {
    let list = (collection || []).filter(c => c && c.name);
    // Wishlist cards (tagged "wishlist") are tracked separately from the
    // owned collection. When the Wishlist chip is active, show ONLY them.
    // For every other filter, hide them so they don't pollute "owned"
    // counts, value totals, or graded/foil lists.
    const isWishlistCard = (c) =>
      tagNamesOf(c).some(t => t.toLowerCase() === 'wishlist');
    if (filter === 'wishlist') {
      list = list.filter(isWishlistCard);
    } else {
      list = list.filter(c => !isWishlistCard(c));
    }
    if (query) {
      const q = query.toLowerCase();
      list = list.filter(c =>
        (c.name || '').toLowerCase().includes(q) ||
        (c.set  || '').toLowerCase().includes(q) ||
        (c.code || '').toLowerCase().includes(q) ||
        tagNamesOf(c).some(t => t.toLowerCase().includes(q))
      );
    }
    if (filter === 'cards')  list = list.filter(c => !api.isSealedProduct?.(c));
    if (filter === 'foil')   list = list.filter(c => c.holo);
    if (filter === 'jp')     list = list.filter(c => c.lang === 'JP');
    if (filter === 'graded') list = list.filter(c => c.grade);
    if (filter === 'sealed') list = list.filter(c => api.isSealedProduct?.(c));
    // Tag filter — card must have ALL selected tags (case-insensitive).
    if (selectedTags.size > 0) {
      list = list.filter(c => {
        const names = tagNamesOf(c).map(t => t.toLowerCase());
        for (const want of selectedTags) {
          if (!names.includes(want)) return false;
        }
        return true;
      });
    }
    if (sort === 'value')    list = [...list].sort((a, b) => (b.usd || 0) - (a.usd || 0));
    // 'recent' = newest-added first. Backend already returns created_at DESC,
    // so no re-sort needed — just use list as-is (undoes any prior sorts).
    if (sort === 'az')       list = [...list].sort((a, b) => (a.name || '').localeCompare(b.name || ''));
    if (sort === 'change')   list = [...list].sort((a, b) => Math.abs(b.change || 0) - Math.abs(a.change || 0));
    return list;
  }, [query, sort, filter, selectedTags, collection]);

  // Surface the wishlist count on the chip so the user knows the list isn't
  // empty even when they're looking at the default "All" view.
  const wishlistCount = useMemo(
    () => (collection || []).filter(c =>
      tagNamesOf(c).some(t => t.toLowerCase() === 'wishlist')
    ).length,
    [collection]
  );

  const total = items.reduce((s, c) => s + c.usd, 0);

  return (
    <div className="screen">
      <NavBar
        large
        title="Collection"
        right={<>
          <button className="tap" onClick={() => reloadCollection && reloadCollection()} style={{ color: 'var(--ink-2)' }}><Icon name="refresh" size={20}/></button>
          <button className="tap" onClick={() => navigate('scan')} style={{ color: 'var(--ink-2)' }}><Icon name="plus" size={22}/></button>
        </>}
      />

      {/* Stats strip */}
      <div className="row" style={{ padding: '0 16px 14px', gap: 8 }}>
        <Stat label="Cards" value={items.length} mono/>
        <Stat label="Value" value={fmtPrice(total, cur === 'BOTH' ? 'USD' : cur, { decimals: 0 })} mono accent/>
        <Stat label="Sets" value={new Set(items.map(c => c.set).filter(Boolean)).size} mono/>
        {items.some(c => c.is_graded) ? (
          <Stat label="Graded" value={items.filter(c => c.is_graded).length} mono/>
        ) : (
          <Stat label="Foil" value={items.filter(c => c.holo).length} mono/>
        )}
      </div>

      {/* Search */}
      <div style={{ padding: '0 16px 12px' }}>
        <div className="row gap-2" style={{
          background: 'var(--bg-2)', borderRadius: 12, padding: '10px 12px',
        }}>
          <Icon name="search" size={18} style={{ color: 'var(--ink-3)' }}/>
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder={`Search ${collection?.length || 0} cards · sets · numbers`}
            style={{
              flex: 1, background: 'transparent', border: 0, outline: 'none',
              fontSize: 14, color: 'var(--ink)',
            }}
          />
          {query && <button className="tap" onClick={() => setQuery('')} style={{ color: 'var(--ink-3)' }}><Icon name="x" size={16}/></button>}
        </div>
      </div>

      {/* Filter chips */}
      <div className="row" style={{ padding: '0 16px 12px', gap: 6, overflowX: 'auto', scrollbarWidth: 'none' }}>
        {[
          { id: 'all', label: 'All' },
          { id: 'cards', label: 'Cards' },
          { id: 'sealed', label: 'Sealed' },
          { id: 'foil', label: 'Foil only' },
          { id: 'jp', label: 'JP' },
          { id: 'graded', label: 'Graded' },
          { id: 'wishlist', label: 'Wishlist', count: wishlistCount },
        ].map(f => (
          <button key={f.id} className="tap" onClick={() => setFilter(f.id)} style={{
            padding: '6px 12px', borderRadius: 999, flexShrink: 0,
            background: filter === f.id ? 'var(--ink)' : 'var(--bg-2)',
            color: filter === f.id ? 'var(--bg)' : 'var(--ink-2)',
            fontSize: 12, fontWeight: 600,
            border: '1px solid var(--hairline-soft)',
            borderColor: filter === f.id ? 'var(--ink)' : 'var(--hairline-soft)',
            display: 'inline-flex', alignItems: 'center', gap: 5,
          }}>
            {f.label}
            {f.count != null && f.count > 0 && (
              <span className="mono" style={{
                fontSize: 10, opacity: 0.7, fontWeight: 700,
              }}>{f.count}</span>
            )}
          </button>
        ))}
      </div>

      {/* Tag filter — multi-select. Only rendered when the user has tags
          somewhere in the collection. Selecting two chips narrows to cards
          with BOTH tags. */}
      {allTags.length > 0 && (
        <div className="row" style={{ padding: '0 16px 12px', gap: 6, alignItems: 'center', overflowX: 'auto', scrollbarWidth: 'none' }}>
          <span style={{ fontSize: 10, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', flexShrink: 0, marginRight: 2 }}>
            Tags
          </span>
          {selectedTags.size > 0 && (
            <button className="tap" onClick={clearTags} style={{
              padding: '6px 10px', borderRadius: 999, flexShrink: 0,
              background: 'transparent', color: 'var(--ink-3)',
              fontSize: 11, fontWeight: 600,
              border: '1px dashed var(--hairline-soft)',
            }}>Clear</button>
          )}
          {allTags.map(({ display, count }) => {
            const active = selectedTags.has(display.toLowerCase());
            return (
              <button key={display} className="tap" onClick={() => toggleTag(display)} style={{
                padding: '6px 10px', borderRadius: 999, flexShrink: 0,
                background: active ? 'var(--accent-soft)' : 'var(--bg-2)',
                color: active ? 'var(--accent)' : 'var(--ink-2)',
                fontSize: 12, fontWeight: 600,
                border: '1px solid',
                borderColor: active ? 'transparent' : 'var(--hairline-soft)',
                display: 'inline-flex', alignItems: 'center', gap: 4,
              }}>
                <span>#{display}</span>
                <span className="mono" style={{ fontSize: 10, opacity: 0.7 }}>{count}</span>
              </button>
            );
          })}
        </div>
      )}

      {/* View + sort */}
      <div className="row" style={{ padding: '0 16px 8px', justifyContent: 'space-between' }}>
        <div className="row gap-1" style={{ background: 'var(--bg-2)', borderRadius: 8, padding: 2 }}>
          {['grid', 'list', 'set'].map(v => (
            <button key={v} className="tap" onClick={() => setView(v)} style={{
              padding: '5px 10px', borderRadius: 6,
              background: view === v ? 'var(--bg-3)' : 'transparent',
              color: view === v ? 'var(--ink)' : 'var(--ink-3)',
              fontSize: 11, fontWeight: 600, textTransform: 'capitalize',
            }}>{v}</button>
          ))}
        </div>
        <button className="tap row gap-1" onClick={() => {
          const cycle = { value: 'recent', recent: 'az', az: 'change', change: 'value' };
          setSort(s => cycle[s] || 'value');
        }} style={{ fontSize: 12, color: 'var(--ink-3)' }}>
          {sort === 'value' ? 'Sort: Value' : sort === 'recent' ? 'Sort: Recent' : sort === 'az' ? 'Sort: A–Z' : 'Sort: Change'} <Icon name="chevron-down" size={14}/>
        </button>
      </div>

      <div className="screen-scroll" style={{ paddingBottom: 24 }}>
        {items.length === 0 && (
          <div className="col" style={{ alignItems: 'center', padding: '40px 24px', gap: 12 }}>
            <div className="foil-soft" style={{ width: 64, height: 64, borderRadius: 14 }}/>
            <div style={{ fontSize: 16, fontWeight: 600 }}>{collection?.length ? 'No cards match' : 'Your collection is empty'}</div>
            <div style={{ fontSize: 13, color: 'var(--ink-3)', textAlign: 'center', maxWidth: 240 }}>
              {collection?.length ? 'Try clearing the filter or search.' : 'Add your first card from the Scan tab.'}
            </div>
            {!collection?.length && (
              <button className="tap" onClick={() => navigate('scan')} style={{
                marginTop: 4, padding: '10px 18px', borderRadius: 12,
                background: 'var(--accent)', color: 'var(--accent-ink)',
                fontWeight: 600, fontSize: 14,
              }}>Add a card</button>
            )}
          </div>
        )}
        {view === 'grid' && items.length > 0 && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 8, padding: '8px 12px' }}>
            {items.map(c => {
              const dupeKey = `${(c.name || '').toLowerCase()}|${c.set || ''}|${c.code || ''}`;
              const dupeCount = copyCount[dupeKey] || 1;
              return (
              <button key={c.id} className="tap" onClick={() => navigate('detail', { card: c })} style={{ textAlign: 'left', display: 'flex', flexDirection: 'column', gap: 4, width: '100%', minWidth: 0 }}>
                <div style={{ position: 'relative', width: '100%' }}>
                  <CardArt card={c} renderMode={tweaks.cardRender} size="md" fill/>
                  {c.bulk && <div style={{
                    position: 'absolute', top: 4, right: 4,
                    background: 'oklch(0 0 0 / 0.7)', backdropFilter: 'blur(4px)',
                    color: 'oklch(1 0 0 / 0.85)', fontSize: 8, fontWeight: 700,
                    letterSpacing: '0.05em', padding: '2px 5px', borderRadius: 4,
                  }}>BULK</div>}
                  {dupeCount > 1 && !c.bulk && (
                    <div style={{
                      position: 'absolute', top: 4, right: 4,
                      background: 'oklch(0 0 0 / 0.72)', backdropFilter: 'blur(4px)',
                      color: '#fff', fontSize: 9, fontWeight: 700,
                      padding: '2px 5px', borderRadius: 4,
                    }}>×{dupeCount}</div>
                  )}
                  {(c.lang === 'JP' || c.lang === 'CH') && (
                    <div style={{
                      position: 'absolute', top: 4, left: 4,
                      background: c.lang === 'JP' ? 'oklch(0.40 0.16 25 / 0.88)' : 'oklch(0.38 0.14 80 / 0.88)',
                      backdropFilter: 'blur(4px)',
                      color: '#fff', fontSize: 7, fontWeight: 800,
                      letterSpacing: '0.05em', padding: '2px 4px', borderRadius: 3,
                    }}>{c.lang}</div>
                  )}
                  {api.isSealedProduct?.(c) && (
                    <div style={{ position: 'absolute', bottom: 4, left: 4 }}>
                      <ProductTypeBadge type={c.product_type} />
                    </div>
                  )}
                </div>
                {c.is_graded && c.grader && c.grade != null && (
                  <GradingBadge grader={c.grader} grade={c.grade} />
                )}
                <div style={{ fontSize: 11, fontWeight: 500, lineHeight: 1.2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</div>
                <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {[c.code, c.set].filter(Boolean).join(' · ')}
                </div>
                {c.variant && (
                  <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {c.variant}
                  </div>
                )}
                <Price usd={c.usd} currency={cur === 'BOTH' ? 'USD' : cur} size="sm"/>
              </button>
              );
            })}
          </div>
        )}

        {view === 'list' && items.length > 0 && (
          <div className="col" style={{ padding: '0 16px' }}>
            {items.map((c, i) => (
              <div key={c.id} style={{
                display: 'grid', gridTemplateColumns: '50px 1fr auto auto', gap: 12, alignItems: 'center',
                padding: '12px 0', textAlign: 'left',
                borderTop: i === 0 ? 'none' : '1px solid var(--hairline-soft)',
              }}>
                <button className="tap" onClick={() => navigate('detail', { card: c })} style={{ display: 'contents' }}>
                  <CardArt card={c} renderMode={tweaks.cardRender} size="sm" flat/>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 14, fontWeight: 500 }}>{c.name}</div>
                    <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>
                      {c.code} · {c.set} · {c.lang} · {c.condition}
                    </div>
                    {c.is_graded && c.grader && c.grade != null && (
                      <div style={{ marginTop: 3 }}>
                        <GradingBadge grader={c.grader} grade={c.grade} />
                      </div>
                    )}
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <Price usd={c.usd} currency={cur === 'BOTH' ? 'USD' : cur} size="sm"/>
                    {c.change != null && (
                      <div className={`mono ${c.change >= 0 ? 'delta-pos' : 'delta-neg'}`} style={{ fontSize: 11 }}>
                        {c.change >= 0 ? '+' : ''}{(c.change).toFixed(1)}%
                      </div>
                    )}
                  </div>
                </button>
                {removeCard && (
                  <button
                    className="tap"
                    onClick={() => {
                      if (confirmDeleteId === c.id) {
                        removeCard(c.id);
                        setConfirmDeleteId(null);
                      } else {
                        setConfirmDeleteId(c.id);
                        setTimeout(() => setConfirmDeleteId(id => id === c.id ? null : id), 2500);
                      }
                    }}
                    style={{
                      padding: '6px 8px', borderRadius: 8, flexShrink: 0,
                      background: confirmDeleteId === c.id ? 'oklch(0.35 0.12 30 / 0.15)' : 'transparent',
                      color: confirmDeleteId === c.id ? 'var(--neg)' : 'var(--ink-3)',
                      fontSize: 11, fontWeight: 700,
                    }}>
                    {confirmDeleteId === c.id ? 'Del?' : <Icon name="trash" size={14}/>}
                  </button>
                )}
              </div>
            ))}
          </div>
        )}

        {view === 'set' && items.length > 0 && (
          <div className="col gap-2" style={{ padding: '4px 16px' }}>
            {(() => {
              const normName = n => n.toLowerCase().replace(/[^a-z0-9]/g, '');
              const map = new Map();
              items.forEach(c => {
                if (!c.set) return;
                const s = map.get(c.set) || { name: c.set, owned: 0, value: 0, preview: null };
                s.owned += 1;
                s.value += (c.usd || 0);
                if (!s.preview) s.preview = c;
                map.set(c.set, s);
              });
              return Array.from(map.values()).sort((a, b) => b.value - a.value).map(s => {
                const logo = setLogos ? setLogos[normName(s.name)] : null;
                const total = setTotals[normName(s.name)] || null;
                const pct = total ? Math.round((s.owned / total) * 100) : null;
                const setId = setIds[normName(s.name)] || null;
                const ownedCards = items.filter(c => c.set === s.name);
                return (
                  <button key={s.name} className="tap row" onClick={() => {
                    if (setId) {
                      setCompletionSheet({ setName: s.name, setId, ownedCards, logo, pct, total });
                    } else {
                      setQuery(s.name); setView('grid');
                    }
                  }} style={{
                    padding: '12px 14px', background: 'var(--bg-1)', borderRadius: 14,
                    border: '1px solid var(--hairline-soft)', textAlign: 'left', gap: 12,
                    alignItems: 'center',
                  }}>
                    <div style={{ width: 72, height: 44, flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                      {logo
                        ? <img src={logo} alt={s.name} style={{ maxWidth: 72, maxHeight: 44, objectFit: 'contain', display: 'block' }}/>
                        : <div style={{ width: 44 }}><CardArt card={s.preview} renderMode={tweaks.cardRender} size="xs" fill flat/></div>
                      }
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 14, fontWeight: 500 }}>{s.name}</div>
                      <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>
                        {total ? `${s.owned}/${total}` : `${s.owned} card${s.owned === 1 ? '' : 's'}`}
                        {pct != null && ` · ${pct}%`}
                      </div>
                      {total != null && (
                        <div style={{ marginTop: 5, height: 3, borderRadius: 2, background: 'var(--hairline)', overflow: 'hidden' }}>
                          <div style={{ height: '100%', width: `${Math.min(pct, 100)}%`, borderRadius: 2, background: pct >= 100 ? 'var(--pos)' : 'var(--accent)' }}/>
                        </div>
                      )}
                    </div>
                    <div style={{ textAlign: 'right', flexShrink: 0 }}>
                      <Price usd={s.value} currency={cur === 'BOTH' ? 'USD' : cur} size="sm"/>
                      <Icon name="chevron-right" size={16} style={{ color: 'var(--ink-3)', marginLeft: 'auto', marginTop: 2 }}/>
                    </div>
                  </button>
                );
              });
            })()}
          </div>
        )}
      </div>

      {completionSheet && (
        <SetCompletionSheet
          {...completionSheet}
          onClose={() => setCompletionSheet(null)}
          onBrowse={() => { setQuery(completionSheet.setName); setView('grid'); setCompletionSheet(null); }}
          navigate={navigate}
          tweaks={tweaks}
          addToWantList={async (card) => {
            try {
              await api.addCard({
                name: card.name,
                set: completionSheet.setName,
                code: card.localId,
                lang: 'EN',
                image_url: card.image ? card.image + '/high.png' : null,
                tags: ['wishlist'],
                condition: 'NM',
              });
            } catch (e) {
              console.error('addToWantList', e);
            }
          }}
        />
      )}
    </div>
  );
}

function Stat({ label, value, mono, accent }) {
  return (
    <div style={{
      flex: 1, padding: '10px 12px',
      background: 'var(--bg-1)', borderRadius: 12, border: '1px solid var(--hairline-soft)',
    }}>
      <div style={{ fontSize: 10, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase' }}>{label}</div>
      <div className={mono ? 'mono' : ''} style={{ fontSize: 16, fontWeight: 600, marginTop: 2, color: accent ? 'var(--accent)' : 'var(--ink)' }}>{value}</div>
    </div>
  );
}

function SetCompletionSheet({ setName, setId, ownedCards, logo, pct, total, onClose, onBrowse, navigate, tweaks, addToWantList }) {
  const [cards, setCards] = useState(null);
  const [loading, setLoading] = useState(true);
  const [addedIds, setAddedIds] = useState(new Set());

  useEffect(() => {
    setLoading(true);
    fetch(`https://api.tcgdex.net/v2/en/sets/${setId}`)
      .then(r => r.json())
      .then(data => { setCards(data.cards || []); setLoading(false); })
      .catch(() => { setCards(null); setLoading(false); });
  }, [setId]);

  // Normalize code: "4/102" -> "4", "4" -> "4"
  const normCode = s => s ? String(s).split('/')[0].trim() : '';
  const ownedLocalIds = new Set(ownedCards.map(c => normCode(c.code)));

  async function handleAddWant(card) {
    setAddedIds(prev => new Set([...prev, card.localId]));
    await addToWantList(card);
  }

  return (
    <div style={{
      position: 'absolute', inset: 0, zIndex: 10,
      background: 'oklch(0 0 0 / 0.55)', backdropFilter: 'blur(2px)',
      display: 'flex', flexDirection: 'column', justifyContent: 'flex-end',
    }} onClick={onClose}>
      <div onClick={e => e.stopPropagation()} style={{
        background: 'var(--bg)', borderRadius: '20px 20px 0 0',
        maxHeight: '88dvh', display: 'flex', flexDirection: 'column',
        boxShadow: '0 -4px 32px oklch(0 0 0 / 0.3)',
      }}>
        {/* Handle */}
        <div style={{ display: 'flex', justifyContent: 'center', padding: '10px 0 4px' }}>
          <div style={{ width: 36, height: 4, borderRadius: 2, background: 'var(--hairline)' }}/>
        </div>

        {/* Header */}
        <div style={{ padding: '8px 16px 12px', display: 'flex', alignItems: 'center', gap: 12 }}>
          {logo && <img src={logo} alt={setName} style={{ height: 32, objectFit: 'contain' }}/>}
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 16, fontWeight: 700 }}>{setName}</div>
            {total != null && (
              <div style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: 2 }}>
                {ownedCards.length}/{total} owned · {pct}%
              </div>
            )}
          </div>
          <button className="tap" onClick={onBrowse} style={{
            fontSize: 12, fontWeight: 600, color: 'var(--accent)',
            padding: '6px 10px', borderRadius: 8, background: 'var(--accent-soft)',
          }}>View owned</button>
        </div>

        {/* Progress bar */}
        {total != null && (
          <div style={{ height: 4, background: 'var(--hairline)', margin: '0 16px 12px' }}>
            <div style={{ height: '100%', width: `${Math.min(pct, 100)}%`, background: pct >= 100 ? 'var(--pos)' : 'var(--accent)', borderRadius: 2, transition: 'width 0.4s' }}/>
          </div>
        )}

        {/* Card list */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '0 16px 24px' }}>
          {loading && (
            <div style={{ textAlign: 'center', color: 'var(--ink-3)', padding: 32, fontSize: 13 }}>Loading set…</div>
          )}
          {!loading && !cards && (
            <div style={{ textAlign: 'center', color: 'var(--ink-3)', padding: 32, fontSize: 13 }}>
              Set data unavailable.{' '}
              <button className="tap" onClick={onBrowse} style={{ color: 'var(--accent)', fontWeight: 600 }}>Browse owned</button>
            </div>
          )}
          {!loading && cards && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 6 }}>
              {cards.map(card => {
                const owned = ownedLocalIds.has(normCode(card.localId));
                const added = addedIds.has(card.localId);
                return (
                  <div key={card.localId} style={{ position: 'relative' }}>
                    <div style={{ opacity: owned ? 1 : 0.38, transition: 'opacity 0.2s' }}>
                      {card.image
                        ? <img src={card.image + '/high.png'} alt={card.name}
                            style={{ width: '100%', borderRadius: 6, display: 'block' }}
                            onError={e => { e.target.style.display='none'; }}/>
                        : <div style={{ width: '100%', paddingBottom: '140%', background: 'var(--bg-2)', borderRadius: 6 }}/>
                      }
                    </div>
                    <div style={{ fontSize: 9, color: 'var(--ink-3)', textAlign: 'center', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {card.localId} {card.name}
                    </div>
                    {owned && (
                      <div style={{
                        position: 'absolute', top: 3, right: 3, width: 16, height: 16, borderRadius: 8,
                        background: 'var(--pos)', color: '#fff', fontSize: 10, fontWeight: 700,
                        display: 'grid', placeItems: 'center', boxShadow: '0 1px 4px oklch(0 0 0 / 0.3)',
                      }}>✓</div>
                    )}
                    {!owned && !added && (
                      <button className="tap" onClick={() => handleAddWant(card)} style={{
                        position: 'absolute', inset: 0, borderRadius: 6, background: 'transparent',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                      }}>
                        <div style={{
                          background: 'oklch(0 0 0 / 0.6)', backdropFilter: 'blur(4px)',
                          color: '#fff', fontSize: 9, fontWeight: 700, borderRadius: 4,
                          padding: '3px 6px', display: 'flex', alignItems: 'center', gap: 3,
                        }}>
                          <Icon name="star" size={9}/> Want
                        </div>
                      </button>
                    )}
                    {added && !owned && (
                      <div style={{
                        position: 'absolute', top: 3, right: 3, width: 16, height: 16, borderRadius: 8,
                        background: 'var(--accent)', color: 'var(--accent-ink)', fontSize: 10, fontWeight: 700,
                        display: 'grid', placeItems: 'center', boxShadow: '0 1px 4px oklch(0 0 0 / 0.3)',
                      }}>★</div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default BrowseScreen
