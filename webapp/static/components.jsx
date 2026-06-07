/* Shared components and helpers for PokeCollect */

const { useState, useEffect, useRef, useMemo, useCallback } = React;

// ============================================================================
// User photo gallery (per-card, localStorage-backed)
// ----------------------------------------------------------------------------
// The card's primary "index" image stays the stock TCG art. This module lets
// the user attach their own additional shots — graded slab, back, condition
// photos — keyed by cardId. Photos are resized to ~1000px and JPEG-encoded
// before storage so localStorage stays well under quota.
// ============================================================================
window.userPhotos = (() => {
  const KEY = 'pokecollect.userPhotos.v1';
  const subs = new Set();

  function readAll() {
    try { return JSON.parse(localStorage.getItem(KEY) || '{}'); }
    catch { return {}; }
  }
  function writeAll(obj) {
    try { localStorage.setItem(KEY, JSON.stringify(obj)); }
    catch (e) { console.warn('[userPhotos] storage failed', e.message); }
    subs.forEach(fn => { try { fn(); } catch {} });
  }

  function get(cardId) {
    if (!cardId) return [];
    return readAll()[String(cardId)] || [];
  }

  // Read+resize a File/Blob into a JPEG data URL.
  async function resize(file, maxDim = 1000, quality = 0.82) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => {
        const ratio = Math.min(1, maxDim / Math.max(img.width, img.height));
        const w = Math.round(img.width * ratio), h = Math.round(img.height * ratio);
        const canvas = document.createElement('canvas');
        canvas.width = w; canvas.height = h;
        canvas.getContext('2d').drawImage(img, 0, 0, w, h);
        URL.revokeObjectURL(url);
        try { resolve(canvas.toDataURL('image/jpeg', quality)); }
        catch (e) { reject(e); }
      };
      img.onerror = (e) => { URL.revokeObjectURL(url); reject(new Error('image decode failed')); };
      img.src = url;
    });
  }

  async function add(cardId, file) {
    if (!cardId || !file) return null;
    const dataUrl = await resize(file);
    const all = readAll();
    const key = String(cardId);
    const photo = { url: dataUrl, ts: Date.now(), kind: 'user' };
    all[key] = [...(all[key] || []), photo];
    writeAll(all);
    return photo;
  }

  function remove(cardId, index) {
    if (!cardId) return;
    const all = readAll();
    const key = String(cardId);
    if (!all[key]) return;
    all[key] = all[key].filter((_, i) => i !== index);
    if (all[key].length === 0) delete all[key];
    writeAll(all);
  }

  function subscribe(fn) {
    subs.add(fn);
    return () => subs.delete(fn);
  }

  return { get, add, remove, subscribe };
})();

// React hook — re-renders when this card's photo list changes.
function useUserPhotos(cardId) {
  const [, force] = useState(0);
  useEffect(() => window.userPhotos.subscribe(() => force(n => n + 1)), []);
  return window.userPhotos.get(cardId);
}
window.useUserPhotos = useUserPhotos;

// ============================================================================
// Currency / formatting
// ============================================================================
const FX = { USD_PER_JPY: 0.0064, JPY_PER_USD: 156 };

const fmtUSD = (n, opts = {}) => {
  const { compact = false, decimals = 2, sign = false } = opts;
  if (n == null || isNaN(n)) return '—';
  const prefix = sign && n > 0 ? '+' : '';
  if (compact && Math.abs(n) >= 1000) {
    return `${prefix}$${(n / 1000).toFixed(1)}k`;
  }
  return `${prefix}$${n.toFixed(decimals)}`;
};
const fmtJPY = (n, opts = {}) => {
  if (n == null || isNaN(n)) return '—';
  const yen = n * FX.JPY_PER_USD;
  if (opts.compact && Math.abs(yen) >= 10000) return `¥${(yen / 1000).toFixed(0)}k`;
  return `¥${Math.round(yen).toLocaleString()}`;
};
const fmtPrice = (usd, currency = 'USD', opts = {}) => {
  if (currency === 'JPY') return fmtJPY(usd, opts);
  if (currency === 'BOTH') return `${fmtUSD(usd, opts)} · ${fmtJPY(usd, opts)}`;
  return fmtUSD(usd, opts);
};

// ============================================================================
// Mock data
// ============================================================================
// CARDS / PRICE_SERIES / SOLD_LISTINGS now provided by data.jsx

// ============================================================================
// Theme + tweaks defaults
// ============================================================================
const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "theme": "dark",
  "currency": "USD",
  "cardRender": "mix",
  "diagnostics": false
}/*EDITMODE-END*/;

// ============================================================================
// Mini sparkline
// ============================================================================
function Sparkline({ data, w = 80, h = 24, color = 'currentColor', fill = false, stroke = 1.5 }) {
  if (!data || data.length < 2) return null;
  const min = Math.min(...data), max = Math.max(...data);
  const span = max - min || 1;
  const pts = data.map((v, i) => {
    const x = (i / (data.length - 1)) * w;
    const y = h - ((v - min) / span) * h;
    return [x, y];
  });
  const d = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');
  const areaD = `${d} L${w},${h} L0,${h} Z`;
  return (
    <svg className="spark" width={w} height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      {fill && <path d={areaD} fill={color} opacity="0.12" />}
      <path d={d} fill="none" stroke={color} strokeWidth={stroke} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

// ============================================================================
// Card visual placeholder — three render modes
// ============================================================================
// renderMode: "placeholder" (striped + label), "stylized" (foil-sheen tile), "photo" (user-photo placeholder), "mix" (decides per card)
function CardArt({ card, renderMode = 'mix', size = 'md', flat = false }) {
  // Decide effective mode for "mix"
  const effective = renderMode === 'mix'
    ? (card.id?.startsWith('scan-') ? 'photo' : (card.holo ? 'stylized' : 'placeholder'))
    : renderMode;

  const aspect = '5 / 7';
  const sizes = {
    xs: { w: 36, label: 9 },
    sm: { w: 64, label: 9 },
    md: { w: 110, label: 10 },
    lg: { w: 180, label: 11 },
    xl: { w: 240, label: 12 },
  };
  const s = sizes[size] || sizes.md;

  const radius = size === 'xs' ? 4 : size === 'sm' ? 6 : size === 'md' ? 9 : 12;

  const baseStyle = {
    width: s.w,
    aspectRatio: aspect,
    borderRadius: radius,
    flexShrink: 0,
    position: 'relative',
    overflow: 'hidden',
    boxShadow: flat ? 'none' : 'var(--shadow-card)',
  };

  // Real card image if backend provided one (Pokemon TCG API / TCGdex).
  if (card.image_url) {
    return (
      <div className="card-art card-art-image" style={baseStyle}>
        <img
          src={card.image_url}
          alt={card.name || 'card'}
          loading="lazy"
          style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover' }}
          onError={(e) => { e.currentTarget.style.display = 'none'; }}
        />
      </div>
    );
  }

  if (effective === 'photo') {
    // Photo placeholder — soft gradient mimicking a card photo
    return (
      <div className="card-art card-art-photo" style={baseStyle}>
        <div style={{
          position: 'absolute', inset: 0,
          background: `linear-gradient(135deg, oklch(0.32 0.04 ${260 + (parseInt(card.id?.slice(-2) || '0', 36) * 17) % 100}) 0%, oklch(0.20 0.02 250) 100%)`,
        }} />
        <div style={{
          position: 'absolute', inset: '14% 14% 30%',
          borderRadius: '4px',
          background: `linear-gradient(160deg, oklch(0.55 0.10 ${200 + (parseInt(card.id?.slice(-2) || '0', 36) * 17) % 140}) 0%, oklch(0.30 0.05 ${200 + (parseInt(card.id?.slice(-2) || '0', 36) * 17) % 140}) 100%)`,
          opacity: 0.7,
        }} />
        {/* Glare */}
        <div style={{
          position: 'absolute', inset: 0,
          background: 'linear-gradient(115deg, transparent 40%, oklch(1 0 0 / 0.08) 50%, transparent 60%)',
        }} />
        {size !== 'xs' && size !== 'sm' && (
          <div className="mono" style={{
            position: 'absolute', left: 8, bottom: 6, right: 8,
            color: 'oklch(1 0 0 / 0.65)',
            fontSize: s.label - 1, fontWeight: 500,
            display: 'flex', justifyContent: 'space-between',
            textShadow: '0 1px 2px oklch(0 0 0 / 0.5)',
          }}>
            <span>{card.code}</span>
            <span>{card.lang}</span>
          </div>
        )}
      </div>
    );
  }

  if (effective === 'stylized') {
    // Foil-sheen abstract tile
    return (
      <div className="card-art card-art-stylized" style={baseStyle}>
        <div className={card.holo ? 'foil' : 'card-stripe'} style={{
          position: 'absolute', inset: 0,
          opacity: card.holo ? 0.85 : 1,
        }} />
        {card.holo && (
          <div style={{
            position: 'absolute', inset: 0,
            background: 'linear-gradient(170deg, oklch(0 0 0 / 0.45) 0%, oklch(0 0 0 / 0.05) 40%, oklch(0 0 0 / 0.55) 100%)',
          }} />
        )}
        <div style={{
          position: 'absolute', inset: 0,
          background: 'linear-gradient(115deg, transparent 35%, oklch(1 0 0 / 0.16) 48%, transparent 62%)',
        }} />
        {size !== 'xs' && (
          <>
            <div className="mono" style={{
              position: 'absolute', top: 6, left: 8, right: 8,
              color: 'oklch(1 0 0 / 0.85)',
              fontSize: s.label - 1, fontWeight: 600,
              letterSpacing: '0.04em', textTransform: 'uppercase',
              display: 'flex', justifyContent: 'space-between',
              textShadow: '0 1px 4px oklch(0 0 0 / 0.6)',
            }}>
              <span>{card.rarity}</span>
              <span>{card.lang}</span>
            </div>
            {size !== 'sm' && (
              <div style={{
                position: 'absolute', left: 8, right: 8, bottom: '38%',
                color: 'oklch(1 0 0 / 0.95)',
                fontSize: size === 'lg' ? 14 : size === 'xl' ? 16 : 11,
                fontWeight: 600,
                lineHeight: 1.1,
                textShadow: '0 1px 6px oklch(0 0 0 / 0.7)',
              }}>{card.name}</div>
            )}
            <div className="mono" style={{
              position: 'absolute', left: 8, right: 8, bottom: 6,
              color: 'oklch(1 0 0 / 0.7)',
              fontSize: s.label - 1, fontWeight: 500,
              display: 'flex', justifyContent: 'space-between',
              textShadow: '0 1px 2px oklch(0 0 0 / 0.5)',
            }}>
              <span>{card.code}</span>
              {card.hp && <span>HP{card.hp}</span>}
            </div>
          </>
        )}
      </div>
    );
  }

  // placeholder (default) — card-back gradient. Looks like a sleeved card
  // rather than a "CARD" label so the home grid doesn't look broken while
  // images backfill.
  const isLoading = card._refreshing;
  return (
    <div className="card-art card-art-placeholder" style={{
      ...baseStyle,
      background: 'linear-gradient(155deg, oklch(0.28 0.05 260) 0%, oklch(0.18 0.03 250) 50%, oklch(0.22 0.04 280) 100%)',
    }}>
      {/* inner crest */}
      <div style={{
        position: 'absolute', inset: '12% 14%',
        borderRadius: 6,
        border: '1px solid oklch(1 0 0 / 0.08)',
        background: 'radial-gradient(circle at 50% 40%, oklch(1 0 0 / 0.06), transparent 60%)',
      }} />
      {/* sheen */}
      <div style={{
        position: 'absolute', inset: 0,
        background: 'linear-gradient(115deg, transparent 40%, oklch(1 0 0 / 0.06) 50%, transparent 60%)',
        animation: isLoading ? 'shimmerSweep 1.4s ease-in-out infinite' : 'none',
      }} />
      {size !== 'xs' && (
        <div className="mono" style={{
          position: 'absolute', left: 0, right: 0, bottom: 6, textAlign: 'center',
          color: 'oklch(1 0 0 / 0.45)', fontSize: Math.max(9, s.label - 2), fontWeight: 500,
          letterSpacing: '0.04em',
        }}>{card.code || ''}</div>
      )}
    </div>
  );
}

// ============================================================================
// Price tag (multi-currency aware)
// ============================================================================
function Price({ usd, currency = 'USD', size = 'md', mono = true, sign = false, decimals = 2, compact = false }) {
  const fontSize = { xs: 11, sm: 13, md: 16, lg: 22, xl: 32, xxl: 44 }[size] || 16;
  const weight = size === 'xl' || size === 'xxl' ? 600 : 500;
  const cls = mono ? 'mono' : '';
  if (currency === 'BOTH') {
    return (
      <span style={{ display: 'inline-flex', alignItems: 'baseline', gap: 6 }}>
        <span className={cls} style={{ fontSize, fontWeight: weight }}>{fmtUSD(usd, { sign, decimals, compact })}</span>
        <span className={cls} style={{ fontSize: Math.max(11, fontSize - 6), color: 'var(--ink-3)', fontWeight: 500 }}>
          {fmtJPY(usd, { compact: true })}
        </span>
      </span>
    );
  }
  return (
    <span className={cls} style={{ fontSize, fontWeight: weight }}>
      {currency === 'JPY' ? fmtJPY(usd, { compact }) : fmtUSD(usd, { sign, decimals, compact })}
    </span>
  );
}

// ============================================================================
// Icons (minimal stroke set, no character art)
// ============================================================================
const Icon = ({ name, size = 20, stroke = 1.6, ...rest }) => {
  const common = {
    width: size, height: size, viewBox: '0 0 24 24', fill: 'none',
    stroke: 'currentColor', strokeWidth: stroke, strokeLinecap: 'round', strokeLinejoin: 'round',
    ...rest,
  };
  switch (name) {
    case 'home':       return <svg {...common}><path d="M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-7h-6v7H4a1 1 0 0 1-1-1z"/></svg>;
    case 'browse':     return <svg {...common}><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>;
    case 'scan':       return <svg {...common}><path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3"/><path d="M4 12h16"/></svg>;
    case 'trade':      return <svg {...common}><path d="M7 4l-4 4 4 4"/><path d="M3 8h14"/><path d="M17 20l4-4-4-4"/><path d="M21 16H7"/></svg>;
    case 'profile':    return <svg {...common}><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-7 8-7s8 3 8 7"/></svg>;
    case 'search':     return <svg {...common}><circle cx="11" cy="11" r="7"/><path d="m20 20-3-3"/></svg>;
    case 'filter':     return <svg {...common}><path d="M3 5h18"/><path d="M6 12h12"/><path d="M10 19h4"/></svg>;
    case 'plus':       return <svg {...common}><path d="M12 5v14M5 12h14"/></svg>;
    case 'check':      return <svg {...common}><path d="M5 12l5 5 9-11"/></svg>;
    case 'x':          return <svg {...common}><path d="M6 6l12 12M18 6 6 18"/></svg>;
    case 'chevron-right': return <svg {...common}><path d="m9 5 7 7-7 7"/></svg>;
    case 'chevron-left':  return <svg {...common}><path d="m15 5-7 7 7 7"/></svg>;
    case 'chevron-down':  return <svg {...common}><path d="m5 9 7 7 7-7"/></svg>;
    case 'arrow-up':   return <svg {...common}><path d="M12 19V5M5 12l7-7 7 7"/></svg>;
    case 'arrow-down': return <svg {...common}><path d="M12 5v14M5 12l7 7 7-7"/></svg>;
    case 'sparkle':    return <svg {...common}><path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2 2M16 16l2 2M6 18l2-2M16 8l2-2"/></svg>;
    case 'flash':      return <svg {...common}><path d="M13 3 4 14h7l-1 7 9-11h-7z"/></svg>;
    case 'bolt':       return <svg {...common}><path d="M13 3 4 14h7l-1 7 9-11h-7z"/></svg>;
    case 'image':      return <svg {...common}><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 16-5-5L5 21"/></svg>;
    case 'flash-on':   return <svg {...common}><path d="M5 3h14l-3 7h3l-9 11 2-9H7z"/></svg>;
    case 'cog':        return <svg {...common}><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>;
    case 'bell':       return <svg {...common}><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10 21a2 2 0 0 0 4 0"/></svg>;
    case 'eye':        return <svg {...common}><path d="M2 12s4-8 10-8 10 8 10 8-4 8-10 8S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>;
    case 'eye-off':    return <svg {...common}><path d="M2 12s4-8 10-8a10 10 0 0 1 5 1.4M22 12s-4 8-10 8a10 10 0 0 1-5-1.4"/><path d="M3 3l18 18"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/></svg>;
    case 'menu':       return <svg {...common}><path d="M4 6h16M4 12h16M4 18h16"/></svg>;
    case 'lang':       return <svg {...common}><path d="M5 8h13"/><path d="M11 4v4"/><path d="M5 20l4-10 4 10"/><path d="M6 18h6"/><path d="M14 14h6c0 4-3 6-3 6"/><path d="M17 16c0 4 3 6 3 6"/></svg>;
    case 'database':   return <svg {...common}><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v6c0 1.7 4 3 9 3s9-1.3 9-3V5"/><path d="M3 11v6c0 1.7 4 3 9 3s9-1.3 9-3v-6"/></svg>;
    case 'card':       return <svg {...common}><rect x="3" y="6" width="18" height="14" rx="2"/><path d="M3 11h18"/></svg>;
    case 'binders':    return <svg {...common}><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M9 3v18"/></svg>;
    case 'star':       return <svg {...common}><path d="m12 3 2.5 6 6.5.5-5 4.5 1.5 6.5L12 17l-5.5 3.5L8 14 3 9.5l6.5-.5z"/></svg>;
    case 'tag':        return <svg {...common}><path d="M20 13 13 20l-9-9V4h7z"/><circle cx="8" cy="8" r="1.5"/></svg>;
    case 'photo':      return <svg {...common}><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 16-5-5L5 21"/></svg>;
    case 'gallery':    return <svg {...common}><rect x="3" y="6" width="14" height="14" rx="2"/><path d="M21 4v12a2 2 0 0 1-2 2"/><path d="M17 4V2"/></svg>;
    case 'refresh':    return <svg {...common}><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/><path d="M3 21v-5h5"/></svg>;
    case 'trash':      return <svg {...common}><path d="M3 6h18"/><path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/><path d="M19 6 18 20a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1L5 6"/></svg>;
    case 'key':        return <svg {...common}><circle cx="8" cy="15" r="4"/><path d="m11 12 9-9"/><path d="m17 6 3 3"/><path d="m14 9 3 3"/></svg>;
    case 'info':       return <svg {...common}><circle cx="12" cy="12" r="9"/><path d="M12 8h.01"/><path d="M11 12h1v4h1"/></svg>;
    case 'circle':     return <svg {...common}><circle cx="12" cy="12" r="9"/></svg>;
    case 'dot':        return <svg {...common}><circle cx="12" cy="12" r="3" fill="currentColor"/></svg>;
    default: return null;
  }
};

// ============================================================================
// Tab bar
// ============================================================================
function TabBar({ tab, onTab }) {
  const tabs = [
    { id: 'home',    label: 'Home',    icon: 'home' },
    { id: 'browse',  label: 'Browse',  icon: 'browse' },
    { id: 'scan',    label: 'Scan',    icon: 'scan' },
    { id: 'trade',   label: 'Trades',  icon: 'trade' },
    { id: 'profile', label: 'Me',      icon: 'profile' },
  ];
  return (
    <div className="tabbar" style={{ display: 'flex', gap: 4 }}>
      {tabs.map(t => {
        const active = tab === t.id;
        const isScan = t.id === 'scan';
        return (
          <button
            key={t.id}
            className="tap"
            onClick={() => onTab(t.id)}
            style={{
              flex: 1,
              display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3,
              padding: '8px 4px',
              color: active ? 'var(--ink)' : 'var(--ink-3)',
              position: 'relative',
            }}
          >
            {isScan ? (
              <div className="foil" style={{
                width: 38, height: 38, borderRadius: 12,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                color: 'oklch(0.18 0.05 200)',
                animation: 'foilRot 14s linear infinite',
                boxShadow: '0 4px 12px oklch(0.78 0.14 200 / 0.3)',
              }}>
                <Icon name="scan" size={20} stroke={2.2} />
              </div>
            ) : (
              <Icon name={t.icon} size={22} stroke={active ? 2 : 1.6} />
            )}
            <span style={{ fontSize: 10, fontWeight: active ? 600 : 500, letterSpacing: '0.01em' }}>{t.label}</span>
          </button>
        );
      })}
    </div>
  );
}

// ============================================================================
// Headers
// ============================================================================
function NavBar({ title, left, right, large = false, transparent = false }) {
  return (
    <div style={{
      flexShrink: 0,
      padding: large ? '8px 16px 0' : '12px 16px',
      background: transparent ? 'transparent' : 'var(--bg)',
      borderBottom: transparent ? 'none' : '1px solid var(--hairline-soft)',
    }}>
      <div className="row" style={{ minHeight: 36, justifyContent: 'space-between', gap: 8 }}>
        <div className="row gap-2" style={{ flex: 1, minWidth: 0 }}>{left}</div>
        {!large && <div style={{ fontWeight: 600, fontSize: 17 }}>{title}</div>}
        <div className="row gap-2" style={{ flex: 1, minWidth: 0, justifyContent: 'flex-end' }}>{right}</div>
      </div>
      {large && (
        <div style={{ fontSize: 32, fontWeight: 600, padding: '4px 0 12px', letterSpacing: '-0.025em' }}>
          {title}
        </div>
      )}
    </div>
  );
}

function NavBackButton({ onClick, label = 'Back' }) {
  return (
    <button className="tap row gap-1" onClick={onClick} style={{
      color: 'var(--accent)', fontSize: 15, fontWeight: 500,
      padding: '4px 0',
    }}>
      <Icon name="chevron-left" size={20} stroke={2.2} />
      <span style={{ marginLeft: -2 }}>{label}</span>
    </button>
  );
}

// Export to window
Object.assign(window, {
  FX,
  fmtUSD, fmtJPY, fmtPrice,
  TWEAK_DEFAULTS,
  Sparkline, CardArt, Price, Icon, TabBar, NavBar, NavBackButton,
});
