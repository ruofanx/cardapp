import { useEffect, useState } from 'react'
import { Icon, NavBar, NavBackButton } from './components.jsx'
import { getProPackage, isEligibleForTrial, purchasePackage, purchaseWasCancelled, purchaseIsPending, customerInfoIsPro } from './purchases.js'

const REASON_COPY = {
  profiles: 'The free plan includes 1 profile. Upgrade to add unlimited profiles for everyone in your family.',
  scans: "You've hit this month's free scan limit. Upgrade for unlimited card scans.",
  general: 'Upgrade for unlimited profiles, unlimited scans, and full price history.',
}

const FEATURES = [
  ['Unlimited profiles', 'Add all your kids with their own collections'],
  ['Unlimited scans', 'No monthly limit on card identification'],
  ['Full price history', 'Charts and trend data for every card'],
  ['Price alerts', 'Get notified when a card hits your target'],
]

export default function PaywallSheet({ reason = 'general', onClose, onUpgraded }) {
  const [pkg, setPkg] = useState(null)
  const [eligibleForTrial, setEligibleForTrial] = useState(false)
  const [status, setStatus] = useState('loading') // loading | ready | purchasing | pending | error
  const [errorMsg, setErrorMsg] = useState('')

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const p = await getProPackage()
        if (cancelled) return
        setPkg(p)
        if (p) setEligibleForTrial(await isEligibleForTrial(p))
        setStatus('ready')
      } catch (e) {
        if (cancelled) return
        setErrorMsg(String(e.message || e))
        setStatus('error')
      }
    })()
    return () => { cancelled = true }
  }, [])

  async function handleUpgrade() {
    if (!pkg || status === 'purchasing') return
    setStatus('purchasing')
    setErrorMsg('')
    try {
      const customerInfo = await purchasePackage(pkg)
      if (customerInfoIsPro(customerInfo)) {
        onUpgraded?.(customerInfo)
      } else {
        setStatus('ready')
      }
    } catch (e) {
      if (purchaseWasCancelled(e)) {
        setStatus('ready')
        return
      }
      if (purchaseIsPending(e)) {
        setStatus('pending')
        return
      }
      setErrorMsg(String(e.message || e))
      setStatus('error')
    }
  }

  const priceLine = pkg
    ? (eligibleForTrial ? `${pkg.product.priceString}/mo · 14-day free trial` : `${pkg.product.priceString}/mo`)
    : '$3.99/mo'

  return (
    <div style={{ position: 'absolute', inset: 0, background: 'var(--bg)', display: 'flex', flexDirection: 'column' }}>
      <NavBar title="" left={<NavBackButton onClick={onClose} label="Back" />} />
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '24px 28px', gap: 0 }}>
        <div className="foil" style={{ width: 72, height: 72, borderRadius: 20, animation: 'foilRot 18s linear infinite', marginBottom: 24 }} />
        <div style={{ fontSize: 26, fontWeight: 700, letterSpacing: '-0.02em', marginBottom: 8, textAlign: 'center' }}>
          Upgrade to Family/Pro
        </div>
        <div style={{ fontSize: 15, color: 'var(--ink-3)', textAlign: 'center', lineHeight: 1.55, marginBottom: 32 }}>
          {REASON_COPY[reason] || REASON_COPY.general}
        </div>

        <div style={{ width: '100%', display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 24 }}>
          {FEATURES.map(([title, desc]) => (
            <div key={title} className="row gap-3" style={{ padding: '12px 14px', background: 'var(--bg-1)', borderRadius: 12 }}>
              <Icon name="check" size={18} style={{ color: 'var(--accent)', flexShrink: 0, marginTop: 1 }} />
              <div>
                <div style={{ fontSize: 14, fontWeight: 600 }}>{title}</div>
                <div style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: 1 }}>{desc}</div>
              </div>
            </div>
          ))}
        </div>

        {status === 'error' && (
          <div style={{ width: '100%', color: 'var(--neg)', fontSize: 13, padding: '8px 12px', background: 'oklch(0.35 0.10 30 / 0.15)', borderRadius: 8, marginBottom: 16 }}>
            {errorMsg || 'Something went wrong. Please try again.'}
          </div>
        )}
        {status === 'pending' && (
          <div style={{ width: '100%', color: 'var(--ink-2)', fontSize: 13, padding: '8px 12px', background: 'var(--bg-1)', borderRadius: 8, marginBottom: 16, textAlign: 'center' }}>
            Waiting for approval (Ask to Buy) — this screen will update once it's approved.
          </div>
        )}
        {status === 'ready' && !pkg && (
          <div style={{ width: '100%', color: 'var(--ink-2)', fontSize: 13, padding: '8px 12px', background: 'var(--bg-1)', borderRadius: 8, marginBottom: 16, textAlign: 'center' }}>
            Subscriptions are temporarily unavailable — please try again later.
          </div>
        )}

        <button
          onClick={handleUpgrade}
          disabled={status === 'loading' || status === 'purchasing' || status === 'pending' || !pkg}
          style={{
            width: '100%', padding: '15px', borderRadius: 14,
            background: 'var(--accent)', color: 'var(--accent-ink)',
            fontWeight: 700, fontSize: 17, border: 'none',
            cursor: (status === 'ready' && pkg) ? 'pointer' : 'not-allowed',
            opacity: (status === 'loading' || status === 'purchasing' || !pkg) ? 0.7 : 1,
          }}>
          {status === 'purchasing' ? 'Processing…' : `Upgrade · ${priceLine}`}
        </button>
        <div style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: 10, textAlign: 'center' }}>
          Cancel anytime
        </div>
      </div>
    </div>
  )
}
