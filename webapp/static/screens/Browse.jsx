/* Browse / Collection — search + filter + grid */

const { useState: useStateBrowse, useMemo: useMemoBrowse } = React;

// Backend returns tags as objects like {id, name, color, ...}; older mock data
// has plain strings. Normalize to a name list per card.
function tagNamesOf(card) {
  return (card?.tags || [])
    .map(t => (typeof t === 'object' && t) ? (t.name || t.label || '') : String(t))
    .map(t => t.trim())
    .filter(Boolean);
}

function BrowseScreen({ tweaks, navigate, collection, reloadCollection, backend, params }) {
  const cur = tweaks.currency;
  const [view, setView] = useStateBrowse('grid');
  const [sort, setSort] = useStateBrowse('value');
  const [query, setQuery] = useStateBrowse('');
  // Allow navigate('browse', {filter:'wishlist'}) to deep-link into a chip
  // selection — Home's Wishlist section uses this.
  const [filter, setFilter] = useStateBrowse(() => params?.filter || 'all');
  // Selected tag names (case preserved for display; matched case-insensitively).
  // Set semantics — multi-select with AND filter (card must have ALL selected).
  const [selectedTags, setSelectedTags] = useStateBrowse(() => new Set());

  // Collect distinct tag names across the user's collection, sorted by frequency.
  // We hide the "wishlist" tag from this row because it already has its own
  // top-level filter chip — listing it twice would be confusing.
  const allTags = useMemoBrowse(() => {
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

  const items = useMemoBrowse(() => {
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
    if (query) list = list.filter(c =>
      (c.name || '').toLowerCase().includes(query.toLowerCase()) ||
      (c.set  || '').toLowerCase().includes(query.toLowerCase()) ||
      (c.code || '').toLowerCase().includes(query.toLowerCase())
    );
    if (filter === 'cards')  list = list.filter(c => !window.api?.isSealedProduct?.(c));
    if (filter === 'foil')   list = list.filter(c => c.holo);
    if (filter === 'jp')     list = list.filter(c => c.lang === 'JP');
    if (filter === 'graded') list = list.filter(c => c.grade);
    if (filter === 'sealed') list = list.filter(c => window.api?.isSealedProduct?.(c));
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
    if (sort === 'recent')   list = [...list].reverse();
    return list;
  }, [query, sort, filter, selectedTags, collection]);

  // Surface the wishlist count on the chip so the user knows the list isn't
  // empty even when they're looking at the default "All" view.
  const wishlistCount = useMemoBrowse(
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
        <Stat label="Foil" value={items.filter(c => c.holo).length} mono/>
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
        <button className="tap row gap-1" onClick={() => setSort(sort === 'value' ? 'recent' : 'value')} style={{ fontSize: 12, color: 'var(--ink-3)' }}>
          {sort === 'value' ? 'Sort: Value' : 'Sort: Recent'} <Icon name="chevron-down" size={14}/>
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
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10, padding: '8px 16px' }}>
            {items.map(c => (
              <button key={c.id} className="tap" onClick={() => navigate('detail', { card: c })} style={{ textAlign: 'left', display: 'flex', flexDirection: 'column', gap: 4 }}>
                <div style={{ position: 'relative' }}>
                  <CardArt card={c} renderMode={tweaks.cardRender} size="md"/>
                  {c.bulk && <div style={{
                    position: 'absolute', top: 4, right: 4,
                    background: 'oklch(0 0 0 / 0.7)', backdropFilter: 'blur(4px)',
                    color: 'oklch(1 0 0 / 0.85)', fontSize: 8, fontWeight: 700,
                    letterSpacing: '0.05em', padding: '2px 5px', borderRadius: 4,
                  }}>BULK</div>}
                  {window.api?.isSealedProduct?.(c) && (
                    <div style={{ position: 'absolute', bottom: 4, left: 4 }}>
                      <ProductTypeBadge type={c.product_type} />
                    </div>
                  )}
                </div>
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
            ))}
          </div>
        )}

        {view === 'list' && items.length > 0 && (
          <div className="col" style={{ padding: '0 16px' }}>
            {items.map((c, i) => (
              <button key={c.id} className="tap" onClick={() => navigate('detail', { card: c })} style={{
                display: 'grid', gridTemplateColumns: '50px 1fr auto', gap: 12, alignItems: 'center',
                padding: '12px 0', textAlign: 'left',
                borderTop: i === 0 ? 'none' : '1px solid var(--hairline-soft)',
              }}>
                <CardArt card={c} renderMode={tweaks.cardRender} size="sm" flat/>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 14, fontWeight: 500 }}>{c.name}</div>
                  <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>
                    {c.code} · {c.set} · {c.lang} · {c.condition}
                  </div>
                </div>
                <div style={{ textAlign: 'right' }}>
                  <Price usd={c.usd} currency={cur === 'BOTH' ? 'USD' : cur} size="sm"/>
                  <div className={`mono ${c.change >= 0 ? 'delta-pos' : 'delta-neg'}`} style={{ fontSize: 11 }}>
                    {c.change >= 0 ? '+' : ''}{(c.change * 100).toFixed(1)}%
                  </div>
                </div>
              </button>
            ))}
          </div>
        )}

        {view === 'set' && items.length > 0 && (
          <div className="col gap-2" style={{ padding: '4px 16px' }}>
            {(() => {
              const map = new Map();
              items.forEach(c => {
                if (!c.set) return;
                const s = map.get(c.set) || { name: c.set, owned: 0, total: 0, value: 0 };
                s.owned += 1;
                s.value += (c.usd || 0);
                map.set(c.set, s);
              });
              return Array.from(map.values()).sort((a, b) => b.value - a.value);
            })().map(s => (
              <button key={s.name} className="tap row" style={{
                padding: '12px 14px', background: 'var(--bg-1)', borderRadius: 14,
                border: '1px solid var(--hairline-soft)', textAlign: 'left', gap: 12,
              }}>
                <div className="foil-soft" style={{ width: 44, height: 44, borderRadius: 10, flexShrink: 0 }}/>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 14, fontWeight: 500 }}>{s.name}</div>
                  <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>{s.owned} card{s.owned === 1 ? '' : 's'}</div>
                </div>
                <div style={{ textAlign: 'right' }}>
                  <Price usd={s.value} currency={cur === 'BOTH' ? 'USD' : cur} size="sm"/>
                  <Icon name="chevron-right" size={16} style={{ color: 'var(--ink-3)', marginLeft: 'auto', marginTop: 2 }}/>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
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

window.BrowseScreen = BrowseScreen;
