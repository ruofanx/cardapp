import { useEffect, useRef, useState } from 'react'
import QRCode from 'qrcode'
import { Icon, NavBar, NavBackButton, fmtPrice } from '../components.jsx'

const RAILWAY_URL = 'https://cardapp-production-569d.up.railway.app'

export default function TradeShowScreen({ goBack, currentUser, collection, navigate }) {
  const canvasRef = useRef(null)
  const [copied, setCopied] = useState(false)

  const profileId = currentUser?.id
  const publicUrl = `${RAILWAY_URL}/?show=${profileId}`

  const wants = (collection || []).filter(c =>
    (c.tags || []).some(t => (typeof t === 'object' ? (t.name || t.label || '') : String(t)).toLowerCase().trim() === 'wishlist')
  )

  useEffect(() => {
    if (canvasRef.current && profileId) {
      QRCode.toCanvas(canvasRef.current, publicUrl, {
        width: 220,
        margin: 2,
        color: { dark: '#ffffff', light: '#1a1c1f' },
      })
    }
  }, [publicUrl, profileId])

  function share() {
    const name = currentUser?.name || 'My'
    const lines = [`${name}'s Want List — scan to view`, publicUrl]
    if (navigator.share) {
      navigator.share({ title: `${name}'s Want List`, url: publicUrl }).catch(() => {})
    } else {
      navigator.clipboard?.writeText(publicUrl).then(() => {
        setCopied(true)
        setTimeout(() => setCopied(false), 2500)
      })
    }
  }

  return (
    <div style={{ position: 'absolute', inset: 0, background: 'var(--bg)', display: 'flex', flexDirection: 'column' }}>
      <NavBar
        title="Trade Show Mode"
        left={<NavBackButton onClick={goBack} />}
      />

      <div style={{ flex: 1, overflowY: 'auto', padding: '24px 20px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 24 }}>

        {/* Live badge */}
        <div className="row gap-2" style={{ alignItems: 'center', padding: '6px 14px', borderRadius: 999, background: 'oklch(0.35 0.15 145 / 0.2)', border: '1px solid oklch(0.55 0.15 145 / 0.4)' }}>
          <div style={{ width: 7, height: 7, borderRadius: 4, background: '#22c55e', animation: 'pulse 2s infinite' }} />
          <span style={{ fontSize: 12, fontWeight: 700, color: '#22c55e', letterSpacing: '0.04em' }}>ACTIVE</span>
        </div>

        {/* QR code */}
        <div style={{ padding: 16, borderRadius: 20, background: '#1a1c1f', border: '1px solid var(--hairline-soft)', boxShadow: '0 8px 32px oklch(0 0 0 / 0.4)' }}>
          <canvas ref={canvasRef} style={{ display: 'block', borderRadius: 8 }} />
        </div>

        {/* Name + want count */}
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 22, fontWeight: 700, letterSpacing: '-0.02em' }}>{currentUser?.name || 'You'}</div>
          <div style={{ fontSize: 14, color: 'var(--ink-3)', marginTop: 4 }}>
            {wants.length > 0
              ? `Looking for ${wants.length} card${wants.length !== 1 ? 's' : ''}`
              : 'No cards on want list yet'}
          </div>
        </div>

        {/* Share link */}
        <button className="tap row gap-2" onClick={share} style={{
          width: '100%', padding: '13px 16px', borderRadius: 14,
          background: 'var(--accent)', color: 'var(--accent-ink)',
          fontWeight: 700, fontSize: 15, border: 'none', cursor: 'pointer',
          alignItems: 'center', justifyContent: 'center',
        }}>
          <Icon name={copied ? 'check' : 'share'} size={18} />
          {copied ? 'Link copied!' : 'Share link'}
        </button>

        {/* Want list preview */}
        {wants.length > 0 && (
          <div style={{ width: '100%' }}>
            <div style={{ fontSize: 11, fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--ink-3)', marginBottom: 10 }}>
              My Wants
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', borderRadius: 14, overflow: 'hidden', border: '1px solid var(--hairline-soft)' }}>
              {wants.slice(0, 8).map((card, i) => (
                <button key={card.id} className="tap" onClick={() => navigate('detail', { cardId: card.id })} style={{
                  display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px',
                  background: 'var(--bg-1)', borderTop: i > 0 ? '1px solid var(--hairline-soft)' : 'none',
                  textAlign: 'left',
                }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{card.name}</div>
                    <div style={{ fontSize: 11, color: 'var(--ink-3)' }}>{card.set}{card.lang && card.lang !== 'EN' ? ` · ${card.lang}` : ''}</div>
                  </div>
                  {card.usd > 0 && <span style={{ fontSize: 12, color: 'var(--ink-2)', flexShrink: 0 }}>${card.usd.toFixed(0)}</span>}
                </button>
              ))}
              {wants.length > 8 && (
                <button className="tap" onClick={() => navigate('want-list')} style={{
                  padding: '10px 14px', background: 'var(--bg-1)', borderTop: '1px solid var(--hairline-soft)',
                  fontSize: 13, color: 'var(--ink-3)', textAlign: 'center',
                }}>
                  +{wants.length - 8} more · View all
                </button>
              )}
            </div>
          </div>
        )}

        {wants.length === 0 && (
          <button className="tap" onClick={() => navigate('want-list')} style={{
            width: '100%', padding: '13px', borderRadius: 14,
            background: 'var(--bg-2)', color: 'var(--ink-2)',
            fontWeight: 600, fontSize: 14, border: 'none', cursor: 'pointer',
          }}>
            Add cards to your Want List →
          </button>
        )}
      </div>
    </div>
  )
}
