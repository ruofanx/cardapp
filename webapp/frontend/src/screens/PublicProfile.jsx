import { useEffect, useState } from 'react'
import api from '../api.js'
import { CardArt, fmtPrice } from '../components.jsx'

export default function PublicProfileView({ profileId }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    api.getPublicProfile(profileId)
      .then(setData)
      .catch(() => setError('This profile is not currently sharing their want list.'))
  }, [profileId])

  if (error) return (
    <div style={{ minHeight: '100dvh', display: 'grid', placeItems: 'center', background: 'var(--bg)', padding: 24 }}>
      <div style={{ textAlign: 'center', color: 'var(--ink-3)', fontSize: 15 }}>{error}</div>
    </div>
  )

  if (!data) return (
    <div style={{ minHeight: '100dvh', display: 'grid', placeItems: 'center', background: 'var(--bg)' }}>
      <div style={{ color: 'var(--ink-3)', fontSize: 13 }}>Loading…</div>
    </div>
  )

  const { profile, wants } = data
  const total = wants.reduce((s, c) => s + (c.usd || 0), 0)

  return (
    <div style={{ minHeight: '100dvh', background: 'var(--bg)', maxWidth: 480, margin: '0 auto' }}>
      {/* Header */}
      <div style={{ padding: '32px 20px 20px', borderBottom: '1px solid var(--hairline-soft)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <div style={{
            width: 52, height: 52, borderRadius: 26, flexShrink: 0,
            background: profile.avatar_color || '#34d399',
            display: 'grid', placeItems: 'center',
            fontSize: 22, fontWeight: 700, color: '#fff',
          }}>
            {(profile.name || '?').charAt(0).toUpperCase()}
          </div>
          <div>
            <div style={{ fontSize: 20, fontWeight: 700, letterSpacing: '-0.02em' }}>{profile.name}</div>
            <div className="row gap-2" style={{ alignItems: 'center', marginTop: 4 }}>
              <div style={{ width: 6, height: 6, borderRadius: 3, background: '#22c55e' }} />
              <span style={{ fontSize: 12, color: '#22c55e', fontWeight: 600 }}>At a card show</span>
            </div>
          </div>
        </div>
      </div>

      {/* Want list */}
      <div style={{ padding: '16px 20px' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 14 }}>
          <span style={{ fontSize: 13, fontWeight: 700, letterSpacing: '0.04em', textTransform: 'uppercase', color: 'var(--ink-3)' }}>
            Want List
          </span>
          <span style={{ fontSize: 12, color: 'var(--ink-3)' }}>· {wants.length} card{wants.length !== 1 ? 's' : ''}</span>
          {total > 0 && <span style={{ fontSize: 12, color: 'var(--ink-3)' }}>· ~{fmtPrice(total, 'USD')} est.</span>}
        </div>

        {wants.length === 0 && (
          <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--ink-3)', fontSize: 14 }}>
            Nothing on the list yet.
          </div>
        )}

        <div style={{ display: 'flex', flexDirection: 'column', gap: 1, borderRadius: 14, overflow: 'hidden', border: wants.length ? '1px solid var(--hairline-soft)' : 'none' }}>
          {wants.map((card, i) => (
            <div key={card.id} style={{
              display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px',
              background: 'var(--bg-1)', borderTop: i > 0 ? '1px solid var(--hairline-soft)' : 'none',
            }}>
              <CardArt card={card} size={40} style={{ borderRadius: 5, flexShrink: 0 }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 14, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{card.name}</div>
                <div style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: 1 }}>
                  {card.set}{card.code ? ` · ${card.code}` : ''}{card.lang && card.lang !== 'EN' ? ` · ${card.lang}` : ''}
                </div>
              </div>
              {card.usd > 0 && (
                <span style={{ fontSize: 13, fontWeight: 600, flexShrink: 0, color: 'var(--ink-2)' }}>
                  ${card.usd.toFixed(2)}
                </span>
              )}
            </div>
          ))}
        </div>
      </div>

      <div style={{ padding: '8px 20px 32px', textAlign: 'center' }}>
        <a href="/" style={{ fontSize: 12, color: 'var(--ink-3)', textDecoration: 'none' }}>
          Powered by PokeCollect
        </a>
      </div>
    </div>
  )
}
