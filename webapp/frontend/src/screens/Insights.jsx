import { useMemo } from 'react'
import { NavBar, NavBackButton, fmtPrice } from '../components.jsx'

export default function InsightsScreen({ goBack, collection = [], tweaks }) {
  const cur = tweaks?.currency || 'USD'
  const cards = (collection || []).filter(c => !c.tags?.some?.(t =>
    (typeof t === 'object' ? (t.name || '') : String(t)).toLowerCase() === 'wishlist'
  ))

  const stats = useMemo(() => compute(cards), [cards])

  if (cards.length === 0) {
    return (
      <div style={{ position: 'absolute', inset: 0, background: 'var(--bg)', display: 'flex', flexDirection: 'column' }}>
        <NavBar title="Insights" left={<NavBackButton onClick={goBack} />} />
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--ink-3)', fontSize: 14 }}>
          Add some cards to see insights.
        </div>
      </div>
    )
  }

  return (
    <div style={{ position: 'absolute', inset: 0, background: 'var(--bg)', display: 'flex', flexDirection: 'column' }}>
      <NavBar title="Insights" left={<NavBackButton onClick={goBack} />} />

      <div style={{ flex: 1, overflowY: 'auto', paddingBottom: 40 }}>

        {/* Summary row */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10, padding: '16px 16px 0' }}>
          <StatTile label="Cards" value={cards.length} />
          <StatTile label="Total value" value={fmtPrice(stats.totalValue, cur === 'BOTH' ? 'USD' : cur, { decimals: 0 })} />
          <GainLossTile paid={stats.totalPaid} value={stats.totalValue} hasData={stats.paidCount > 0} cur={cur} />
        </div>

        {/* Value by set */}
        {stats.sets.length > 0 && (
          <Section title="Value by set">
            <div style={{ padding: '0 16px' }}>
              {stats.sets.map(s => (
                <BarRow key={s.name} label={s.name} value={s.value} max={stats.sets[0].value} count={s.count} cur={cur} />
              ))}
            </div>
          </Section>
        )}

        {/* Condition mix */}
        <Section title="Condition">
          <div style={{ padding: '0 16px', display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {stats.conditions.map(c => (
              <CondChip key={c.cond} cond={c.cond} count={c.count} pct={c.pct} />
            ))}
          </div>
        </Section>

        {/* EN vs JP */}
        {stats.hasJP && (
          <Section title="Language">
            <div style={{ padding: '0 16px' }}>
              <LangBar en={stats.enCount} jp={stats.jpCount} enVal={stats.enValue} jpVal={stats.jpValue} cur={cur} />
            </div>
          </Section>
        )}

        {/* Raw vs Graded */}
        {stats.gradedCount > 0 && (
          <Section title="Raw vs graded">
            <div style={{ padding: '0 16px' }}>
              <BarRow label="Raw" value={stats.rawValue} max={Math.max(stats.rawValue, stats.gradedValue)} count={stats.rawCount} cur={cur} />
              <BarRow label="Graded" value={stats.gradedValue} max={Math.max(stats.rawValue, stats.gradedValue)} count={stats.gradedCount} cur={cur} />
            </div>
          </Section>
        )}

        {/* Grader breakdown */}
        {stats.graderBreakdown.length > 1 && (
          <Section title="By grader">
            <div style={{ padding: '0 16px' }}>
              {stats.graderBreakdown.map(g => (
                <BarRow key={g.grader} label={g.grader} value={g.value} max={stats.graderBreakdown[0].value} count={g.count} cur={cur} />
              ))}
            </div>
          </Section>
        )}

        {/* Top 5 */}
        <Section title="Top cards by value">
          <div style={{ padding: '0 16px' }}>
            {stats.top5.map((c, i) => (
              <div key={c.id} style={{
                display: 'flex', alignItems: 'center', gap: 10, padding: '10px 0',
                borderTop: i === 0 ? 'none' : '1px solid var(--hairline-soft)',
              }}>
                <div style={{ width: 22, fontSize: 12, fontWeight: 700, color: 'var(--ink-3)', textAlign: 'center' }}>
                  {i + 1}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 14, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</div>
                  <div style={{ fontSize: 11, color: 'var(--ink-3)' }}>{c.set}</div>
                </div>
                <div style={{ fontSize: 15, fontWeight: 700 }}>
                  {fmtPrice(c.usd, cur === 'BOTH' ? 'USD' : cur, { decimals: 0 })}
                </div>
              </div>
            ))}
          </div>
        </Section>

        {/* Top gainers / losers */}
        {stats.topGainers.length > 0 && (
          <Section title="Best investments">
            <div style={{ padding: '0 16px' }}>
              {stats.topGainers.map((c, i) => (
                <div key={c.id} style={{
                  display: 'flex', alignItems: 'center', gap: 10, padding: '10px 0',
                  borderTop: i === 0 ? 'none' : '1px solid var(--hairline-soft)',
                }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 14, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</div>
                    <div style={{ fontSize: 11, color: 'var(--ink-3)' }}>
                      Paid {fmtPrice(c.purchase_price, cur === 'BOTH' ? 'USD' : cur)} · Now {fmtPrice(c.usd, cur === 'BOTH' ? 'USD' : cur)}
                    </div>
                  </div>
                  <div style={{ textAlign: 'right', flexShrink: 0 }}>
                    <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--pos)' }}>
                      +{fmtPrice(c.gainUSD, cur === 'BOTH' ? 'USD' : cur)}
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--pos)' }}>+{c.gainPct.toFixed(0)}%</div>
                  </div>
                </div>
              ))}
            </div>
          </Section>
        )}

        {stats.topLosers.length > 0 && (
          <Section title="Biggest losses">
            <div style={{ padding: '0 16px' }}>
              {stats.topLosers.map((c, i) => (
                <div key={c.id} style={{
                  display: 'flex', alignItems: 'center', gap: 10, padding: '10px 0',
                  borderTop: i === 0 ? 'none' : '1px solid var(--hairline-soft)',
                }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 14, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</div>
                    <div style={{ fontSize: 11, color: 'var(--ink-3)' }}>
                      Paid {fmtPrice(c.purchase_price, cur === 'BOTH' ? 'USD' : cur)} · Now {fmtPrice(c.usd, cur === 'BOTH' ? 'USD' : cur)}
                    </div>
                  </div>
                  <div style={{ textAlign: 'right', flexShrink: 0 }}>
                    <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--neg)' }}>
                      {fmtPrice(c.gainUSD, cur === 'BOTH' ? 'USD' : cur)}
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--neg)' }}>{c.gainPct.toFixed(0)}%</div>
                  </div>
                </div>
              ))}
            </div>
          </Section>
        )}

        {/* Acquisition timeline */}
        {stats.monthBuckets.length > 1 && (
          <Section title="Cards added by month">
            <div style={{ padding: '0 16px' }}>
              {stats.monthBuckets.map(b => (
                <BarRow key={b.label} label={b.label} value={b.count} max={stats.monthBuckets[0].count} count={null} cur={cur} isCount />
              ))}
            </div>
          </Section>
        )}

      </div>
    </div>
  )
}

// ─── computation ─────────────────────────────────────────────────────────────

function tagNames(card) {
  return (card.tags || []).map(t => typeof t === 'object' && t ? (t.name || t.label || '') : String(t)).map(t => t.trim().toLowerCase())
}

function compute(cards) {
  const totalValue = cards.reduce((s, c) => s + (c.usd || 0), 0)

  const withPaid = cards.filter(c => c.purchase_price != null)
  const totalPaid = withPaid.reduce((s, c) => s + c.purchase_price, 0)
  const paidCount = withPaid.length

  // Sets
  const setMap = {}
  for (const c of cards) {
    const key = c.set || 'Unknown'
    if (!setMap[key]) setMap[key] = { name: key, value: 0, count: 0 }
    setMap[key].value += c.usd || 0
    setMap[key].count++
  }
  const sets = Object.values(setMap).sort((a, b) => b.value - a.value).slice(0, 10)

  // Conditions
  const condMap = {}
  for (const c of cards) {
    const key = c.condition || 'NM'
    condMap[key] = (condMap[key] || 0) + 1
  }
  const ORDER = ['NM', 'LP', 'MP', 'HP', 'DMG']
  const conditions = Object.entries(condMap)
    .sort((a, b) => (ORDER.indexOf(a[0]) + 99) % 99 - (ORDER.indexOf(b[0]) + 99) % 99)
    .map(([cond, count]) => ({ cond, count, pct: Math.round(count / cards.length * 100) }))

  // Language
  const enCards = cards.filter(c => (c.lang || 'EN').toUpperCase() === 'EN')
  const jpCards = cards.filter(c => (c.lang || '').toUpperCase() === 'JP')
  const hasJP = jpCards.length > 0
  const enValue = enCards.reduce((s, c) => s + (c.usd || 0), 0)
  const jpValue = jpCards.reduce((s, c) => s + (c.usd || 0), 0)

  // Raw vs Graded
  const rawCards = cards.filter(c => !c.is_graded)
  const gradedCards = cards.filter(c => c.is_graded)
  const rawValue = rawCards.reduce((s, c) => s + (c.usd || 0), 0)
  const gradedValue = gradedCards.reduce((s, c) => s + (c.usd || 0), 0)

  // Grader breakdown
  const graderMap = {}
  for (const c of gradedCards) {
    const key = (c.grader || 'Unknown').toUpperCase()
    if (!graderMap[key]) graderMap[key] = { grader: key, count: 0, value: 0 }
    graderMap[key].count++
    graderMap[key].value += c.usd || 0
  }
  const graderBreakdown = Object.values(graderMap).sort((a, b) => b.value - a.value)

  // Top 5 by value
  const top5 = [...cards].filter(c => c.usd > 0).sort((a, b) => (b.usd || 0) - (a.usd || 0)).slice(0, 5)

  // Top gainers (cards with purchase_price set and highest % gain)
  const gainers = [...withPaid]
    .filter(c => c.usd != null && c.purchase_price > 0)
    .map(c => ({ ...c, gainPct: (c.usd - c.purchase_price) / c.purchase_price * 100, gainUSD: c.usd - c.purchase_price }))
    .sort((a, b) => b.gainPct - a.gainPct)
  const topGainers = gainers.filter(c => c.gainPct > 0).slice(0, 5)
  const topLosers  = [...gainers].filter(c => c.gainPct < 0).sort((a, b) => a.gainPct - b.gainPct).slice(0, 3)

  // Acquisition by month
  const monthMap = {}
  for (const c of cards) {
    const d = c.created_at ? new Date(c.created_at) : null
    if (!d || isNaN(d)) continue
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
    const label = d.toLocaleDateString('en-US', { month: 'short', year: '2-digit' })
    if (!monthMap[key]) monthMap[key] = { label, count: 0 }
    monthMap[key].count++
  }
  const monthBuckets = Object.entries(monthMap).sort((a, b) => b[0].localeCompare(a[0])).map(([, v]) => v).slice(0, 12)

  return {
    totalValue, totalPaid, paidCount,
    sets,
    conditions,
    hasJP, enCount: enCards.length, jpCount: jpCards.length, enValue, jpValue,
    graderBreakdown,
    rawCount: rawCards.length, gradedCount: gradedCards.length, rawValue, gradedValue,
    top5, topGainers, topLosers,
    monthBuckets,
  }
}

// ─── sub-components ───────────────────────────────────────────────────────────

function Section({ title, children }) {
  return (
    <div style={{ marginTop: 28 }}>
      <div style={{ padding: '0 16px 10px', fontSize: 11, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--ink-3)' }}>
        {title}
      </div>
      {children}
    </div>
  )
}

function StatTile({ label, value }) {
  return (
    <div style={{ background: 'var(--bg-1)', border: '1px solid var(--hairline-soft)', borderRadius: 12, padding: '12px 14px' }}>
      <div style={{ fontSize: 10, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.05em', textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 700, marginTop: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{value}</div>
    </div>
  )
}

function GainLossTile({ paid, value, hasData, cur }) {
  if (!hasData) return <StatTile label="Cost basis" value="—" />
  const gl = value - paid
  const pct = paid > 0 ? (gl / paid * 100) : 0
  const pos = gl >= 0
  return (
    <div style={{ background: 'var(--bg-1)', border: '1px solid var(--hairline-soft)', borderRadius: 12, padding: '12px 14px' }}>
      <div style={{ fontSize: 10, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.05em', textTransform: 'uppercase' }}>Gain / loss</div>
      <div style={{ fontSize: 18, fontWeight: 700, marginTop: 4, color: pos ? 'var(--pos)' : 'var(--neg)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {pos ? '+' : ''}{fmtPrice(gl, cur === 'BOTH' ? 'USD' : cur, { decimals: 0 })}
      </div>
      <div style={{ fontSize: 10, color: pos ? 'var(--pos)' : 'var(--neg)', fontWeight: 600 }}>
        {pos ? '+' : ''}{pct.toFixed(1)}%
      </div>
    </div>
  )
}

function BarRow({ label, value, max, count, cur, isCount }) {
  const pct = max > 0 ? (value / max) : 0
  return (
    <div style={{ padding: '7px 0', borderTop: '1px solid var(--hairline-soft)', firstOfType: 'none' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 5, gap: 8 }}>
        <span style={{ fontSize: 13, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}>{label}</span>
        <span style={{ fontSize: 13, fontWeight: 600, flexShrink: 0, color: 'var(--ink-2)' }}>
          {isCount ? `${value} card${value === 1 ? '' : 's'}` : fmtPrice(value, cur === 'BOTH' ? 'USD' : cur, { decimals: 0 })}
          {count != null && !isCount && <span style={{ fontSize: 11, color: 'var(--ink-3)', fontWeight: 400, marginLeft: 4 }}>· {count}</span>}
        </span>
      </div>
      <div style={{ height: 5, borderRadius: 3, background: 'var(--bg-3)' }}>
        <div style={{ height: 5, borderRadius: 3, background: 'var(--accent)', width: `${pct * 100}%`, transition: 'width 0.4s ease' }} />
      </div>
    </div>
  )
}

function CondChip({ cond, count, pct }) {
  const colors = { NM: 'var(--pos)', LP: '#4ea4f5', MP: '#f5a623', HP: '#e06050', DMG: '#a060d0' }
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 6, padding: '6px 12px',
      background: 'var(--bg-1)', border: '1px solid var(--hairline-soft)', borderRadius: 999,
    }}>
      <div style={{ width: 8, height: 8, borderRadius: 4, background: colors[cond] || 'var(--ink-3)' }} />
      <span style={{ fontSize: 13, fontWeight: 600 }}>{cond}</span>
      <span style={{ fontSize: 12, color: 'var(--ink-3)' }}>{count} · {pct}%</span>
    </div>
  )
}

function LangBar({ en, jp, enVal, jpVal, cur }) {
  const total = en + jp || 1
  const enPct = en / total * 100
  const jpPct = jp / total * 100
  return (
    <div>
      <div style={{ display: 'flex', height: 10, borderRadius: 5, overflow: 'hidden', marginBottom: 8 }}>
        <div style={{ width: `${enPct}%`, background: 'var(--accent)', transition: 'width 0.4s ease' }} />
        <div style={{ flex: 1, background: '#e54444' }} />
      </div>
      <div style={{ display: 'flex', gap: 16 }}>
        <LangStat color="var(--accent)" label="English" count={en} value={enVal} cur={cur} />
        {jp > 0 && <LangStat color="#e54444" label="Japanese" count={jp} value={jpVal} cur={cur} />}
      </div>
    </div>
  )
}

function LangStat({ color, label, count, value, cur }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <div style={{ width: 10, height: 10, borderRadius: 2, background: color }} />
      <span style={{ fontSize: 12, color: 'var(--ink-3)' }}>{label}</span>
      <span style={{ fontSize: 12, fontWeight: 600 }}>{count}</span>
      <span style={{ fontSize: 11, color: 'var(--ink-3)' }}>{fmtPrice(value, cur === 'BOTH' ? 'USD' : cur, { decimals: 0 })}</span>
    </div>
  )
}
