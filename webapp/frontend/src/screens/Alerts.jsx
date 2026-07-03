import { useEffect, useState } from 'react'
import api from '../api.js'
import { CardArt, Icon, NavBar, NavBackButton, fmtPrice } from '../components.jsx'

export default function AlertsScreen({ navigate, goBack, collection = [] }) {
  const [alerts, setAlerts] = useState(null) // null = loading
  const [error, setError] = useState('')

  useEffect(() => {
    api.getAlerts()
      .then(data => setAlerts(Array.isArray(data) ? data : []))
      .catch(() => setError('Could not load alerts'))
  }, [])

  // Also surface big movers from collection (>10% change)
  const movers = [...(collection || [])]
    .filter(c => c.usd > 0 && Math.abs(c.change || 0) >= 10)
    .sort((a, b) => Math.abs(b.change || 0) - Math.abs(a.change || 0))
    .slice(0, 10)

  const loading = alerts === null

  return (
    <div style={{ position: 'absolute', inset: 0, background: 'var(--bg)', display: 'flex', flexDirection: 'column' }}>
      <NavBar title="Alerts" left={<NavBackButton onClick={goBack} />} />

      <div style={{ flex: 1, overflowY: 'auto', paddingBottom: 32 }}>

        {/* Price alerts */}
        <SectionHeader label="Price alerts" count={alerts?.length} />
        {loading && <Placeholder />}
        {error && <EmptyMsg text={error} />}
        {!loading && !error && alerts.length === 0 && (
          <EmptyMsg text="No alerts triggered. Set a target price on any card's detail screen." action={{ label: 'Browse collection', onTap: () => navigate('browse') }} />
        )}
        {!loading && alerts.map((a, i) => (
          <AlertRow key={a.id} alert={a} first={i === 0} onTap={() => navigate('detail', { cardId: a.id })} />
        ))}

        {/* Big movers */}
        {movers.length > 0 && (
          <>
            <SectionHeader label="Big movers" subtitle="≥10% change" count={movers.length} style={{ marginTop: 24 }} />
            {movers.map((c, i) => (
              <MoverRow key={c.id} card={c} first={i === 0} onTap={() => navigate('detail', { card: c })} />
            ))}
          </>
        )}
      </div>
    </div>
  )
}

function SectionHeader({ label, count, subtitle, style }) {
  return (
    <div style={{ padding: '16px 16px 8px', display: 'flex', alignItems: 'baseline', gap: 6, ...style }}>
      <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--ink-3)' }}>
        {label}
      </span>
      {subtitle && <span style={{ fontSize: 11, color: 'var(--ink-3)' }}>· {subtitle}</span>}
      {count != null && count > 0 && (
        <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--accent)', marginLeft: 'auto' }}>{count}</span>
      )}
    </div>
  )
}

function AlertRow({ alert, first, onTap }) {
  const drop = alert.alert_price - alert.usd
  const dropPct = (drop / alert.alert_price * 100).toFixed(1)
  return (
    <button className="tap" onClick={onTap} style={{
      width: '100%', display: 'flex', alignItems: 'center', gap: 12, padding: '10px 16px',
      borderTop: first ? 'none' : '1px solid var(--hairline-soft)', textAlign: 'left', background: 'transparent',
    }}>
      <div style={{
        width: 8, height: 8, borderRadius: 4, background: 'var(--pos)', flexShrink: 0,
      }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 14, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {alert.name}
        </div>
        <div style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: 1 }}>
          {alert.set}{alert.code ? ` · ${alert.code}` : ''}
        </div>
      </div>
      <div style={{ textAlign: 'right', flexShrink: 0 }}>
        <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--pos)' }}>
          ${alert.usd.toFixed(2)}
        </div>
        <div style={{ fontSize: 11, color: 'var(--ink-3)' }}>
          target ${alert.alert_price.toFixed(0)} · −{dropPct}%
        </div>
      </div>
    </button>
  )
}

function MoverRow({ card, first, onTap }) {
  const pos = (card.change || 0) >= 0
  return (
    <button className="tap" onClick={onTap} style={{
      width: '100%', display: 'flex', alignItems: 'center', gap: 12, padding: '10px 16px',
      borderTop: first ? 'none' : '1px solid var(--hairline-soft)', textAlign: 'left', background: 'transparent',
    }}>
      <CardArt card={card} size={40} style={{ borderRadius: 5, flexShrink: 0 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 14, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {card.name}
        </div>
        <div style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: 1 }}>
          {card.set}
        </div>
      </div>
      <div style={{ textAlign: 'right', flexShrink: 0 }}>
        <div style={{ fontSize: 14, fontWeight: 600 }}>${(card.usd || 0).toFixed(2)}</div>
        <div className={pos ? 'delta-pos' : 'delta-neg'} style={{ fontSize: 12, fontWeight: 600 }}>
          {pos ? '+' : ''}{(card.change || 0).toFixed(1)}%
        </div>
      </div>
    </button>
  )
}

function Placeholder() {
  return (
    <div style={{ padding: '24px 16px', color: 'var(--ink-3)', fontSize: 13 }}>Loading…</div>
  )
}

function EmptyMsg({ text, action }) {
  return (
    <div style={{ padding: '16px 16px 0', display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.5 }}>{text}</div>
      {action && (
        <button className="tap" onClick={action.onTap} style={{
          alignSelf: 'flex-start', fontSize: 13, fontWeight: 600, color: 'var(--accent)', background: 'transparent', padding: 0,
        }}>{action.label} →</button>
      )}
    </div>
  )
}
