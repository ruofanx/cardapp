import { useState, useMemo, useCallback } from 'react'
import api from '../api.js'
import { CardArt, Icon, NavBar, NavBackButton, Price, fmtPrice, tagNamesOf } from '../components.jsx'

export default function WantListScreen({ navigate, goBack, collection = [], updateCard, currentUser, identifyCard }) {
  const [search, setSearch] = useState('')
  const [adding, setAdding] = useState(false)
  const [candidates, setCandidates] = useState([])
  const [searching, setSearching] = useState(false)
  const [searchQ, setSearchQ] = useState('')

  const wants = useMemo(() =>
    collection.filter(c => tagNamesOf(c).some(t => t.toLowerCase() === 'wishlist')),
    [collection]
  )

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return wants
    return wants.filter(c =>
      (c.name || '').toLowerCase().includes(q) ||
      (c.set || '').toLowerCase().includes(q)
    )
  }, [wants, search])

  const removeWant = useCallback(async (card) => {
    const newTags = tagNamesOf(card).filter(t => t.toLowerCase() !== 'wishlist')
    await updateCard(card.id, { tags: newTags })
  }, [updateCard])

  async function runSearch(q) {
    if (!q.trim()) { setCandidates([]); return }
    setSearching(true)
    try {
      const tasks = []
      if (api.searchPokemonTCG) tasks.push(api.searchPokemonTCG({ name: q }, { pageSize: 12 }))
      if (api.searchTCGdex)     tasks.push(api.searchTCGdex({ name: q }, { pageSize: 12, lang: 'en' }))
      const results = await Promise.allSettled(tasks)
      const seen = new Set()
      const merged = []
      results.forEach(r => {
        if (r.status === 'fulfilled' && Array.isArray(r.value)) {
          r.value.forEach(c => { if (!seen.has(c.id)) { merged.push(c); seen.add(c.id) } })
        }
      })
      setCandidates(merged.slice(0, 20))
    } finally {
      setSearching(false)
    }
  }

  async function addWant(card) {
    try {
      await api.addCard({
        name: card.name,
        set: card.set,
        code: card.code || card.number,
        lang: card.lang || 'EN',
        image_url: card.image_url || card.imageUrl,
        tags: ['wishlist'],
        condition: 'NM',
      })
      // Optimistic: reload will pick it up via reloadCollection in parent
      setAdding(false)
      setSearchQ('')
      setCandidates([])
    } catch (e) {
      console.error('addWant', e)
    }
  }

  const totalEstimate = useMemo(() =>
    wants.reduce((sum, c) => sum + (c.usd || 0), 0),
    [wants]
  )

  return (
    <div style={{ position: 'absolute', inset: 0, background: 'var(--bg)', display: 'flex', flexDirection: 'column' }}>
      <NavBar
        title="Want List"
        left={<NavBackButton onClick={goBack} />}
        right={
          <button className="tap" onClick={() => setAdding(a => !a)} style={{
            padding: '6px 10px', borderRadius: 10,
            background: adding ? 'var(--accent)' : 'var(--bg-2)',
            color: adding ? 'var(--accent-ink)' : 'var(--ink)',
            fontSize: 13, fontWeight: 600,
          }}>
            {adding ? 'Done' : '+ Add'}
          </button>
        }
      />

      {/* Add panel */}
      {adding && (
        <div style={{ borderBottom: '1px solid var(--hairline-soft)', padding: '12px 16px', background: 'var(--bg-1)' }}>
          <div style={{ position: 'relative', marginBottom: candidates.length ? 10 : 0 }}>
            <Icon name="search" size={16} style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--ink-3)', pointerEvents: 'none' }} />
            <input
              autoFocus
              value={searchQ}
              onChange={e => {
                setSearchQ(e.target.value)
                runSearch(e.target.value)
              }}
              placeholder="Search for a card…"
              style={{
                width: '100%', padding: '9px 12px 9px 32px', borderRadius: 10, boxSizing: 'border-box',
                border: '1px solid var(--hairline-soft)', background: 'var(--bg)',
                color: 'var(--ink)', fontSize: 14, outline: 'none',
              }}
            />
            {searching && <div style={{ position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)', fontSize: 11, color: 'var(--ink-3)' }}>…</div>}
          </div>
          {candidates.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 1, borderRadius: 10, overflow: 'hidden', border: '1px solid var(--hairline-soft)', maxHeight: 240, overflowY: 'auto' }}>
              {candidates.map((c, i) => (
                <button key={c.id || i} className="tap" onClick={() => addWant(c)} style={{
                  display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px',
                  background: 'var(--bg-1)', borderTop: i > 0 ? '1px solid var(--hairline-soft)' : 'none',
                  textAlign: 'left',
                }}>
                  <CardArt card={c} size="xs" flat />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</div>
                    <div style={{ fontSize: 11, color: 'var(--ink-3)' }}>{c.set} · {c.lang || 'EN'}</div>
                  </div>
                  <Icon name="plus" size={16} style={{ color: 'var(--accent)', flexShrink: 0 }} />
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Summary bar */}
      {wants.length > 0 && (
        <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--hairline-soft)', display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 13, color: 'var(--ink-3)' }}>{wants.length} card{wants.length !== 1 ? 's' : ''} wanted</span>
          {totalEstimate > 0 && (
            <>
              <span style={{ color: 'var(--hairline)', fontSize: 13 }}>·</span>
              <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>~{fmtPrice(totalEstimate, 'USD')} est. total</span>
            </>
          )}
          <div style={{ flex: 1 }} />
          <ShareButton wants={wants} currentUser={currentUser} />
        </div>
      )}

      {/* Search filter */}
      {wants.length > 4 && (
        <div style={{ padding: '8px 16px', borderBottom: '1px solid var(--hairline-soft)' }}>
          <div style={{ position: 'relative' }}>
            <Icon name="search" size={15} style={{ position: 'absolute', left: 9, top: '50%', transform: 'translateY(-50%)', color: 'var(--ink-3)', pointerEvents: 'none' }} />
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Filter wants…"
              style={{
                width: '100%', padding: '7px 10px 7px 28px', borderRadius: 8, boxSizing: 'border-box',
                border: '1px solid var(--hairline-soft)', background: 'var(--bg-1)',
                color: 'var(--ink)', fontSize: 13, outline: 'none',
              }}
            />
          </div>
        </div>
      )}

      {/* List */}
      <div style={{ flex: 1, overflowY: 'auto' }}>
        {filtered.length === 0 && (
          <EmptyState hasWants={wants.length > 0} onAdd={() => setAdding(true)} />
        )}
        {filtered.map((card, i) => (
          <WantRow
            key={card.id}
            card={card}
            first={i === 0}
            onRemove={() => removeWant(card)}
            onDetail={() => navigate('detail', { cardId: card.id })}
          />
        ))}
      </div>
    </div>
  )
}

function WantRow({ card, first, onRemove, onDetail }) {
  const [confirming, setConfirming] = useState(false)

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12, padding: '10px 16px',
      borderTop: first ? 'none' : '1px solid var(--hairline-soft)',
    }}>
      <button className="tap" onClick={onDetail} style={{ display: 'contents' }}>
        <CardArt card={card} size="xs" flat />
        <div style={{ flex: 1, minWidth: 0, textAlign: 'left' }}>
          <div style={{ fontSize: 14, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{card.name}</div>
          <div style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: 1 }}>
            {card.set}{card.code ? ` · ${card.code}` : ''}{card.lang && card.lang !== 'EN' ? ` · ${card.lang}` : ''}
          </div>
        </div>
        {card.usd > 0 && (
          <Price card={card} style={{ fontSize: 13, fontWeight: 600, flexShrink: 0 }} />
        )}
      </button>
      <button
        className="tap"
        onClick={() => {
          if (confirming) { onRemove(); setConfirming(false) }
          else { setConfirming(true); setTimeout(() => setConfirming(false), 2000) }
        }}
        style={{
          padding: '6px 8px', borderRadius: 8, flexShrink: 0,
          background: confirming ? 'oklch(0.35 0.12 30 / 0.2)' : 'transparent',
          color: confirming ? 'var(--neg)' : 'var(--ink-3)',
          fontSize: 11, fontWeight: 600,
        }}>
        {confirming ? 'Remove?' : <Icon name="x" size={15} />}
      </button>
    </div>
  )
}

function ShareButton({ wants, currentUser }) {
  const [copied, setCopied] = useState(false)

  function share() {
    const name = currentUser?.name || 'My'
    const lines = [`${name}'s Want List (${wants.length} cards)`, '']
    wants.forEach(c => {
      const price = c.usd > 0 ? ` — ~$${c.usd.toFixed(0)}` : ''
      lines.push(`• ${c.name}${c.set ? ` (${c.set})` : ''}${price}`)
    })
    const text = lines.join('\n')
    if (navigator.share) {
      navigator.share({ title: `${name}'s Want List`, text }).catch(() => {})
    } else {
      navigator.clipboard?.writeText(text).then(() => {
        setCopied(true)
        setTimeout(() => setCopied(false), 2000)
      })
    }
  }

  return (
    <button className="tap row gap-1" onClick={share} style={{
      padding: '5px 10px', borderRadius: 8,
      background: 'var(--bg-2)', color: 'var(--ink-2)',
      fontSize: 12, fontWeight: 600, alignItems: 'center',
    }}>
      <Icon name={copied ? 'check' : 'share'} size={13} />
      {copied ? 'Copied!' : 'Share'}
    </button>
  )
}

function EmptyState({ hasWants, onAdd }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '60px 32px', gap: 12, textAlign: 'center' }}>
      <Icon name="star" size={36} style={{ color: 'var(--ink-3)', opacity: 0.4 }} />
      <div style={{ fontSize: 16, fontWeight: 600 }}>
        {hasWants ? 'No matches' : 'Nothing on your list yet'}
      </div>
      <div style={{ fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.5 }}>
        {hasWants
          ? 'Try a different search'
          : 'Add cards you\'re looking for. Share the list at trade shows so other collectors know what you need.'}
      </div>
      {!hasWants && (
        <button className="tap" onClick={onAdd} style={{
          marginTop: 8, padding: '10px 20px', borderRadius: 12,
          background: 'var(--accent)', color: 'var(--accent-ink)',
          fontWeight: 700, fontSize: 14, border: 'none', cursor: 'pointer',
        }}>
          + Add a Card
        </button>
      )}
    </div>
  )
}
