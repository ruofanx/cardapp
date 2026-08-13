import { useState } from 'react'
import api from '../api.js'
import { NavBar, NavBackButton } from '../components.jsx'
import PaywallSheet from '../PaywallSheet.jsx'

const COLORS = ['#34d399', '#60a5fa', '#f472b6', '#fb923c', '#a78bfa', '#facc15', '#f87171', '#4ade80']

export default function AddProfileScreen({ navigate, goBack, users, onProfileCreated }) {
  const [name, setName] = useState('')
  const [color, setColor] = useState(COLORS[0])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [showPaywall, setShowPaywall] = useState(false)
  const [justUpgraded, setJustUpgraded] = useState(false)

  // `afterUpgrade` is passed explicitly by the post-purchase retry: calling
  // setJustUpgraded(true) then submitCreate() in the same handler would still
  // read the stale `justUpgraded === false` from this render's closure.
  async function submitCreate(afterUpgrade = false) {
    if (!name.trim()) return
    setLoading(true)
    setError('')
    try {
      const profile = await api.createProfile(name.trim(), color)
      if (onProfileCreated) onProfileCreated(profile)
      goBack()
    } catch (err) {
      if (err.status === 402) {
        if (afterUpgrade || justUpgraded) {
          setError('Your upgrade is still processing — please try again in a moment.')
        } else {
          setShowPaywall(true)
        }
      } else {
        setError(err.message || 'Failed to create profile')
      }
    } finally {
      setLoading(false)
    }
  }

  function handleCreate(e) {
    e.preventDefault()
    submitCreate()
  }

  if (showPaywall) {
    return (
      <PaywallSheet
        reason="profiles"
        onClose={() => setShowPaywall(false)}
        onUpgraded={() => {
          setShowPaywall(false)
          setJustUpgraded(true)
          submitCreate(true)
        }}
      />
    )
  }

  return (
    <div style={{ position: 'absolute', inset: 0, background: 'var(--bg)', display: 'flex', flexDirection: 'column' }}>
      <NavBar
        title="Add Profile"
        left={<NavBackButton onClick={goBack} />}
      />

      <div style={{ flex: 1, overflow: 'auto', padding: '24px 20px' }}>
        <p style={{ color: 'var(--ink-3)', fontSize: 14, marginBottom: 28, lineHeight: 1.5 }}>
          Add a profile for each family member. Each person gets their own collection and trade lists.
        </p>

        <form onSubmit={handleCreate} style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
          {/* Avatar color picker */}
          <div>
            <div style={{ fontSize: 12, fontWeight: 600, letterSpacing: '0.05em', textTransform: 'uppercase', color: 'var(--ink-3)', marginBottom: 10 }}>
              Color
            </div>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              {COLORS.map(c => (
                <button
                  key={c}
                  type="button"
                  onClick={() => setColor(c)}
                  style={{
                    width: 36, height: 36, borderRadius: 18,
                    background: c, border: 'none', cursor: 'pointer',
                    boxShadow: color === c ? `0 0 0 3px var(--bg), 0 0 0 5px ${c}` : 'none',
                    transform: color === c ? 'scale(1.1)' : 'scale(1)',
                    transition: 'all 0.15s',
                  }}
                />
              ))}
            </div>
          </div>

          {/* Name */}
          <div>
            <div style={{ fontSize: 12, fontWeight: 600, letterSpacing: '0.05em', textTransform: 'uppercase', color: 'var(--ink-3)', marginBottom: 8 }}>
              Name
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
              <div style={{
                width: 48, height: 48, borderRadius: 24, background: color, flexShrink: 0,
                display: 'grid', placeItems: 'center',
                fontSize: 20, fontWeight: 700, color: '#fff',
              }}>
                {name.trim().charAt(0).toUpperCase() || '?'}
              </div>
              <input
                type="text"
                value={name}
                onChange={e => setName(e.target.value)}
                placeholder="e.g. Reid"
                maxLength={30}
                autoFocus
                style={{
                  flex: 1, padding: '12px 14px', borderRadius: 12,
                  border: '1px solid var(--hairline-soft)',
                  background: 'var(--bg-1)', color: 'var(--ink)',
                  fontSize: 17, fontWeight: 500, outline: 'none',
                }}
              />
            </div>
          </div>

          {error && (
            <div style={{ color: 'var(--neg)', fontSize: 13, padding: '8px 12px', background: 'oklch(0.35 0.10 30 / 0.15)', borderRadius: 8 }}>
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={!name.trim() || loading}
            style={{
              padding: '14px', borderRadius: 14,
              background: name.trim() ? 'var(--accent)' : 'var(--bg-3)',
              color: name.trim() ? 'var(--accent-ink)' : 'var(--ink-3)',
              fontWeight: 700, fontSize: 16, border: 'none',
              cursor: name.trim() && !loading ? 'pointer' : 'not-allowed',
              transition: 'all 0.15s',
            }}>
            {loading ? 'Creating…' : 'Create Profile'}
          </button>
        </form>

        {/* Existing profiles for context */}
        {users && users.length > 0 && (
          <div style={{ marginTop: 32 }}>
            <div style={{ fontSize: 12, fontWeight: 600, letterSpacing: '0.05em', textTransform: 'uppercase', color: 'var(--ink-3)', marginBottom: 12 }}>
              Current Profiles
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 2, borderRadius: 14, overflow: 'hidden', border: '1px solid var(--hairline-soft)' }}>
              {users.map((u, i) => (
                <div key={u.id} className="row gap-3" style={{
                  padding: '12px 14px', background: 'var(--bg-1)',
                  borderTop: i > 0 ? '1px solid var(--hairline-soft)' : 'none',
                }}>
                  <div style={{
                    width: 32, height: 32, borderRadius: 16, flexShrink: 0,
                    display: 'grid', placeItems: 'center',
                    background: u.avatar_color || 'var(--accent)',
                    color: '#fff', fontSize: 13, fontWeight: 700,
                  }}>
                    {(u.name || '?').charAt(0).toUpperCase()}
                  </div>
                  <span style={{ fontSize: 15, fontWeight: 500 }}>{u.name}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
