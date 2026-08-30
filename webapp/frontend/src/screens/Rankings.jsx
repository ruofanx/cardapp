import { useEffect, useState } from 'react'
import api from '../api.js'
import { NavBar, NavBackButton, Price } from '../components.jsx'

export default function RankingsScreen({ navigate, currentUser, collection }) {
  const [sort, setSort] = useState('undervalued')
  const [rows, setRows] = useState(null)

  useEffect(() => {
    let cancelled = false
    setRows(null)
    ;(async () => {
      const res = await api.getRankings(currentUser?.id, sort)
      if (!cancelled) setRows(res.rankings || [])
    })()
    return () => { cancelled = true }
  }, [currentUser?.id, sort])

  // Rankings only ever include cards already in the collection (the backend
  // skips cards with no stored prediction features), so we can resolve the
  // full card object locally rather than passing a bare id — Detail.jsx reads
  // params.card, not params.id.
  const openCard = (r) => {
    const card = (collection || []).find(c => String(c.id) === String(r.card_id))
    navigate('detail', { card: card || { id: String(r.card_id), name: r.name, current_market_price: r.current_market_price, usd: r.current_market_price } })
  }

  return (
    <div style={{ position: 'absolute', inset: 0, background: 'var(--bg)', display: 'flex', flexDirection: 'column' }}>
      <NavBar title="Rankings" left={<NavBackButton onClick={() => navigate('__back')} />} />
      <div style={{ display: 'flex', gap: 8, padding: '8px 16px' }}>
        {['undervalued', 'upside', 'grade_ev'].map(s => (
          <button key={s} className={`chip ${sort === s ? 'chip-strong' : ''}`} onClick={() => setSort(s)}>
            {s === 'undervalued' ? 'Undervalued' : s === 'upside' ? 'Upside' : 'Grade EV'}
          </button>
        ))}
      </div>
      {rows === null ? (
        <div style={{ padding: 16, color: 'var(--ink-3)', fontSize: 14 }}>Loading…</div>
      ) : rows.length === 0 ? (
        <div style={{ padding: 16, color: 'var(--ink-3)', fontSize: 14 }}>
          No ranked cards yet — open a few cards' Detail screens first so their fair-value features get computed.
        </div>
      ) : (
        <div style={{ flex: 1, padding: '0 16px 24px', overflowY: 'auto' }}>
          {rows.map(r => (
            <div key={r.card_id} className="col" style={{
              background: 'var(--bg-1)', borderRadius: 14, border: '1px solid var(--hairline-soft)',
              padding: 12, marginTop: 8, cursor: 'pointer',
            }} onClick={() => openCard(r)}>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <div style={{ fontWeight: 600 }}>{r.name}</div>
                <Price usd={r.current_market_price} size="sm"/>
              </div>
              <div className="mono" style={{ fontSize: 13, color: 'var(--ink-3)', marginTop: 2 }}>
                Fair value <Price usd={r.fair_value} size="sm"/> ({r.valuation_gap_pct > 0 ? '+' : ''}{r.valuation_gap_pct}%)
                {r.grade_ev != null && <> · Grade EV <Price usd={r.grade_ev} size="sm" sign/></>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
