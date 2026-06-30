import { useState, useEffect } from 'react'

export function IOSStatusBar({ dark = false, time }) {
  const c = dark ? '#fff' : '#000'
  const [now, setNow] = useState(() => new Date())
  useEffect(() => { const id = setInterval(() => setNow(new Date()), 20_000); return () => clearInterval(id) }, [])
  const liveTime = now.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  const displayTime = time || liveTime
  return (
    <div style={{ display: 'flex', gap: 154, alignItems: 'center', justifyContent: 'center', padding: '21px 24px 19px', boxSizing: 'border-box', position: 'relative', zIndex: 20, width: '100%' }}>
      <div style={{ flex: 1, height: 22, display: 'flex', alignItems: 'center', justifyContent: 'center', paddingTop: 1.5 }}>
        <span style={{ fontFamily: '-apple-system, "SF Pro", system-ui', fontWeight: 590, fontSize: 17, lineHeight: '22px', color: c }}>{displayTime}</span>
      </div>
      <div style={{ flex: 1, height: 22, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 7, paddingTop: 1, paddingRight: 1 }}>
        <svg width="19" height="12" viewBox="0 0 19 12"><rect x="0" y="7.5" width="3.2" height="4.5" rx="0.7" fill={c}/><rect x="4.8" y="5" width="3.2" height="7" rx="0.7" fill={c}/><rect x="9.6" y="2.5" width="3.2" height="9.5" rx="0.7" fill={c}/><rect x="14.4" y="0" width="3.2" height="12" rx="0.7" fill={c}/></svg>
        <svg width="17" height="12" viewBox="0 0 17 12"><path d="M8.5 3.2C10.8 3.2 12.9 4.1 14.4 5.6L15.5 4.5C13.7 2.7 11.2 1.5 8.5 1.5C5.8 1.5 3.3 2.7 1.5 4.5L2.6 5.6C4.1 4.1 6.2 3.2 8.5 3.2Z" fill={c}/><path d="M8.5 6.8C9.9 6.8 11.1 7.3 12 8.2L13.1 7.1C11.8 5.9 10.2 5.1 8.5 5.1C6.8 5.1 5.2 5.9 3.9 7.1L5 8.2C5.9 7.3 7.1 6.8 8.5 6.8Z" fill={c}/><circle cx="8.5" cy="10.5" r="1.5" fill={c}/></svg>
        <svg width="27" height="13" viewBox="0 0 27 13"><rect x="0.5" y="0.5" width="23" height="12" rx="3.5" stroke={c} strokeOpacity="0.35" fill="none"/><rect x="2" y="2" width="20" height="9" rx="2" fill={c}/><path d="M25 4.5V8.5C25.8 8.2 26.5 7.2 26.5 6.5C26.5 5.8 25.8 4.8 25 4.5Z" fill={c} fillOpacity="0.4"/></svg>
      </div>
    </div>
  )
}

export function IOSGlassPill({ children, dark = false, style = {} }) {
  return (
    <div style={{ height: 44, minWidth: 44, borderRadius: 9999, position: 'relative', overflow: 'hidden', display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: dark ? '0 2px 6px rgba(0,0,0,0.35), 0 6px 16px rgba(0,0,0,0.2)' : '0 1px 3px rgba(0,0,0,0.07), 0 3px 10px rgba(0,0,0,0.06)', ...style }}>
      <div style={{ position: 'absolute', inset: 0, borderRadius: 9999, backdropFilter: 'blur(12px) saturate(180%)', WebkitBackdropFilter: 'blur(12px) saturate(180%)', background: dark ? 'rgba(120,120,128,0.28)' : 'rgba(255,255,255,0.5)' }} />
      <div style={{ position: 'absolute', inset: 0, borderRadius: 9999, boxShadow: dark ? 'inset 1.5px 1.5px 1px rgba(255,255,255,0.15), inset -1px -1px 1px rgba(255,255,255,0.08)' : 'inset 1.5px 1.5px 1px rgba(255,255,255,0.7), inset -1px -1px 1px rgba(255,255,255,0.4)', border: dark ? '0.5px solid rgba(255,255,255,0.15)' : '0.5px solid rgba(0,0,0,0.06)' }} />
      <div style={{ position: 'relative', zIndex: 1, display: 'flex', alignItems: 'center', padding: '0 4px' }}>{children}</div>
    </div>
  )
}

export function IOSDevice({ children, width = 402, height = 874, dark = false, title, keyboard = false }) {
  return (
    <div style={{ width, height, borderRadius: 48, overflow: 'hidden', position: 'relative', background: dark ? '#000' : '#F2F2F7', boxShadow: '0 40px 80px rgba(0,0,0,0.18), 0 0 0 1px rgba(0,0,0,0.12)', fontFamily: '-apple-system, system-ui, sans-serif', WebkitFontSmoothing: 'antialiased' }}>
      <div style={{ position: 'absolute', top: 11, left: '50%', transform: 'translateX(-50%)', width: 126, height: 37, borderRadius: 24, background: '#000', zIndex: 50 }} />
      <div style={{ position: 'absolute', top: 0, left: 0, right: 0, zIndex: 10 }}><IOSStatusBar dark={dark} /></div>
      <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
        <div style={{ flex: 1, overflow: 'auto' }}>{children}</div>
      </div>
      <div style={{ position: 'absolute', bottom: 0, left: 0, right: 0, zIndex: 60, height: 34, display: 'flex', justifyContent: 'center', alignItems: 'flex-end', paddingBottom: 8, pointerEvents: 'none' }}>
        <div style={{ width: 139, height: 5, borderRadius: 100, background: dark ? 'rgba(255,255,255,0.7)' : 'rgba(0,0,0,0.25)' }} />
      </div>
    </div>
  )
}

function readFramePreference() {
  if (typeof window === 'undefined') return false
  try {
    const url = new URL(window.location.href)
    const qp = url.searchParams.get('frame')
    if (qp === '1' || qp === 'true') { try { localStorage.setItem('pokecollect.frame', '1') } catch (_) {}; return true }
    if (qp === '0' || qp === 'false') { try { localStorage.removeItem('pokecollect.frame') } catch (_) {}; return false }
    return localStorage.getItem('pokecollect.frame') === '1'
  } catch (_) { return false }
}

export function ResponsiveShell({ children, forceMode }) {
  const [framed, setFramed] = useState(() => {
    if (forceMode === 'framed') return true
    if (forceMode === 'fluid') return false
    return readFramePreference()
  })
  useEffect(() => {
    if (forceMode) return
    const onPop = () => setFramed(readFramePreference())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [forceMode])

  if (framed) {
    return (
      <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', background: 'var(--page-bg)', padding: '24px 0' }}>
        <IOSDevice dark={true}>{children}</IOSDevice>
      </div>
    )
  }
  return (
    <div data-shell="fluid" style={{ position: 'fixed', inset: 0, background: 'var(--bg)', '--app-top-inset': 'env(safe-area-inset-top, 0px)' }}>
      {children}
    </div>
  )
}
