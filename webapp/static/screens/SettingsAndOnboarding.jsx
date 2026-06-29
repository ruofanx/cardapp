/* Settings + Onboarding screens */

const { useState: useStateSettings } = React;

function SettingsScreen({ tweaks, setTweak, navigate, users = [], currentUser, setCurrentUser, collection = [], backend, reloadCollection, onSignOut }) {
  const setsCount = new Set((collection || []).map(c => c.set).filter(Boolean)).size;
  const [refreshing, setRefreshing] = useStateSettings(false);
  const [refreshMsg, setRefreshMsg] = useStateSettings(null);

  const handleRefreshAll = async () => {
    if (refreshing || !window.api?.refreshAllPrices) return;
    setRefreshing(true);
    setRefreshMsg(null);
    try {
      await window.api.refreshAllPrices();
      setRefreshMsg('Done — prices updated.');
      if (reloadCollection) reloadCollection(currentUser?.id);
    } catch (e) {
      setRefreshMsg(`Error: ${e?.message || 'refresh failed'}`);
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <div className="screen">
      <NavBar large title="Settings"/>

      <div className="screen-scroll" style={{ paddingBottom: 24 }}>
        {/* Profile card */}
        <div style={{ padding: '0 16px 20px' }}>
          <div className="row gap-2" style={{
            padding: 14, background: 'var(--bg-1)', borderRadius: 14, border: '1px solid var(--hairline-soft)', alignItems: 'center',
          }}>
            <div style={{
              width: 48, height: 48, borderRadius: 24, flexShrink: 0,
              background: 'linear-gradient(135deg, var(--accent), oklch(0.55 0.13 200))',
            }}/>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 15, fontWeight: 600 }}>{currentUser?.name ? `@${currentUser.name.toLowerCase()}` : '@you'}</div>
              <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>{collection.length} card{collection.length === 1 ? '' : 's'} · {setsCount} set{setsCount === 1 ? '' : 's'}</div>
            </div>
            <Icon name="chevron-right" size={18} style={{ color: 'var(--ink-3)' }}/>
          </div>
        </div>

        {/* User switcher (multi-user families) */}
        {users.length > 1 && (
          <div style={{ padding: '0 16px 16px' }}>
            <div style={{ fontSize: 11, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 8 }}>Switch user</div>
            <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
              {users.map(u => (
                <button key={u.id} className="tap" onClick={() => {
                  if (setCurrentUser) setCurrentUser(u);
                  if (window.api) window.api.state.currentUserId = u.id;
                  if (reloadCollection) reloadCollection(u.id);
                }} style={{
                  padding: '6px 12px', borderRadius: 999,
                  background: u.id === currentUser?.id ? 'var(--ink)' : 'var(--bg-2)',
                  color: u.id === currentUser?.id ? 'var(--bg)' : 'var(--ink-2)',
                  fontSize: 12, fontWeight: 600,
                }}>{u.name}</button>
              ))}
            </div>
          </div>
        )}

        <SettingsSection label="Display">
          <SettingsRow label="Theme" value={tweaks.theme === 'dark' ? 'Dark' : 'Light'}/>
          <SettingsRow label="Card render" value={
            { svg: 'Glyph', stripe: 'Stripe', placeholder: 'Sleeve', photo: 'Photo' }[tweaks.cardRender]
          }/>
          <SettingsRow label="Currency" value={
            { USD: '$ USD', JPY: '¥ JPY', EUR: '€ EUR', BOTH: 'USD + JPY' }[tweaks.currency]
          }/>
        </SettingsSection>

        <SettingsSection label="Data sources">
          <SettingsRow label="Backend" value={
            backend?.online === true ? 'Online' :
            backend?.online === false ? 'Offline · demo data' :
            'Connecting…'
          } mono/>
          <SettingsRow label="Server" value={(window.api && window.api.state.base) || 'http://localhost:8000'} mono/>
          <SettingsRow label="Price provider" value="TCGplayer · Cardmarket · PriceCharting"/>
          <SettingsRow label="Refresh" value="Daily 7am CT"/>
          <div style={{ padding: '10px 14px', borderTop: '1px solid var(--hairline-soft)' }}>
            <button className="tap" onClick={handleRefreshAll} disabled={refreshing || backend?.online === false}
              style={{
                width: '100%', padding: '9px 0', borderRadius: 10,
                background: 'var(--accent)', color: 'var(--accent-ink)',
                fontWeight: 600, fontSize: 13, opacity: (refreshing || backend?.online === false) ? 0.5 : 1,
              }}>
              {refreshing ? 'Refreshing…' : 'Refresh all prices now'}
            </button>
            {refreshMsg && (
              <div style={{ marginTop: 6, fontSize: 12, color: refreshMsg.startsWith('Error') ? 'var(--neg)' : 'var(--pos)', textAlign: 'center' }}>
                {refreshMsg}
              </div>
            )}
          </div>
        </SettingsSection>

        <SettingsSection label="About">
          <SettingsRow label="Collection" value={`${collection.length} card${collection.length === 1 ? '' : 's'} · ${setsCount} set${setsCount === 1 ? '' : 's'}`}/>
          <SettingsRow label="Pricing engines" value="eBay Browse · PriceCharting · Cardmarket · TCGplayer"/>
        </SettingsSection>

        <div style={{ padding: '8px 16px 24px' }}>
          {onSignOut && (
            <button
              onClick={onSignOut}
              style={{ padding: '12px 16px', width: '100%', background: 'none', border: '1px solid #ef4444', color: '#ef4444', borderRadius: '10px', fontSize: '15px', cursor: 'pointer', marginTop: '24px' }}
            >
              Sign out
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function SettingsSection({ label, children }) {
  return (
    <div style={{ padding: '0 16px 20px' }}>
      <div style={{ fontSize: 11, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 8, paddingLeft: 4 }}>{label}</div>
      <div style={{ background: 'var(--bg-1)', borderRadius: 14, border: '1px solid var(--hairline-soft)', overflow: 'hidden' }}>
        {children}
      </div>
    </div>
  );
}

function SettingsRow({ label, value, mono }) {
  return (
    <button className="tap row" style={{
      width: '100%', padding: '13px 14px',
      borderTop: '1px solid var(--hairline-soft)',
      justifyContent: 'space-between', alignItems: 'center', gap: 12,
      background: 'transparent', textAlign: 'left',
    }}>
      <span style={{ fontSize: 14 }}>{label}</span>
      <span className="row gap-1" style={{ color: 'var(--ink-3)' }}>
        {value && <span className={mono ? 'mono' : ''} style={{ fontSize: 13 }}>{value}</span>}
        <Icon name="chevron-right" size={16}/>
      </span>
    </button>
  );
}

function ToggleRow({ label, value, sub }) {
  const [on, setOn] = React.useState(value);
  return (
    <button className="tap row" onClick={() => setOn(!on)} style={{
      width: '100%', padding: '13px 14px',
      borderTop: '1px solid var(--hairline-soft)',
      justifyContent: 'space-between', alignItems: 'center', gap: 12,
      background: 'transparent', textAlign: 'left',
    }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 14 }}>{label}</div>
        {sub && <div style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 2 }}>{sub}</div>}
      </div>
      <div style={{
        width: 40, height: 24, borderRadius: 12, padding: 2,
        background: on ? 'var(--accent)' : 'var(--bg-3)',
        transition: 'background 0.15s',
      }}>
        <div style={{
          width: 20, height: 20, borderRadius: 10, background: '#fff',
          transform: `translateX(${on ? 16 : 0}px)`,
          transition: 'transform 0.15s',
        }}/>
      </div>
    </button>
  );
}

/* ---- Onboarding ---- */

function OnboardingScreen({ navigate, setTweak, tweaks }) {
  const [step, setStep] = React.useState(0);
  const steps = [
    {
      eyebrow: '01 / 03',
      title: 'Your binder, indexed.',
      body: 'Snap a stack. We identify each card, grade roughly, and price it against live market comps.',
      visual: <OnboVisualScan/>,
    },
    {
      eyebrow: '02 / 03',
      title: 'Trade with verdicts, not vibes.',
      body: 'Every offer shows a fairness band. ±8% is fair. Beyond that we tell you who it favors and by how much.',
      visual: <OnboVisualTrade/>,
    },
    {
      eyebrow: '03 / 03',
      title: 'Pick a render.',
      body: 'PokeCollect ships without licensed card art. Choose a placeholder style — you can change it any time.',
      visual: <OnboVisualRender tweaks={tweaks} setTweak={setTweak}/>,
    },
  ];
  const s = steps[step];

  return (
    <div className="screen" style={{ background: 'var(--bg)' }}>
      <div style={{ flexShrink: 0, padding: 'calc(env(safe-area-inset-top) + 12px) 16px 0', display: 'flex', justifyContent: 'flex-end' }}>
        <button className="tap" onClick={() => navigate('home')} style={{ fontSize: 13, color: 'var(--ink-3)' }}>Skip</button>
      </div>

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center', padding: '0 24px', gap: 24 }}>
        <div style={{ height: 240, display: 'grid', placeItems: 'center' }}>
          {s.visual}
        </div>
        <div>
          <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.06em', marginBottom: 10 }}>{s.eyebrow}</div>
          <div style={{ fontSize: 28, fontWeight: 600, letterSpacing: '-0.02em', lineHeight: 1.1, marginBottom: 12 }}>{s.title}</div>
          <div style={{ fontSize: 14, color: 'var(--ink-2)', lineHeight: 1.5, textWrap: 'pretty' }}>{s.body}</div>
        </div>
      </div>

      <div style={{ flexShrink: 0, padding: '0 24px calc(env(safe-area-inset-bottom) + 24px)', display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div className="row gap-1" style={{ justifyContent: 'center' }}>
          {steps.map((_, i) => (
            <div key={i} style={{
              width: i === step ? 22 : 6, height: 6, borderRadius: 3,
              background: i === step ? 'var(--accent)' : 'var(--bg-3)',
              transition: 'all 0.2s',
            }}/>
          ))}
        </div>
        <button className="tap" onClick={() => step === steps.length - 1 ? navigate('home') : setStep(step + 1)} style={{
          padding: '16px', borderRadius: 14, background: 'var(--accent)', color: 'var(--accent-ink)', fontWeight: 600, fontSize: 15,
        }}>{step === steps.length - 1 ? 'Open my binder' : 'Continue'}</button>
      </div>
    </div>
  );
}

function OnboVisualScan() {
  return (
    <div style={{ position: 'relative', width: 180, height: 240 }}>
      {[0, 1, 2].map(i => (
        <div key={i} className="foil-soft" style={{
          position: 'absolute', left: 30 + i * 8, top: 30 - i * 8,
          width: 120, height: 168, borderRadius: 8,
          border: '1px solid var(--hairline)',
          transform: `rotate(${(i - 1) * 4}deg)`,
        }}/>
      ))}
      <div style={{
        position: 'absolute', left: 0, right: 0, top: '50%',
        height: 1.5, background: 'var(--accent)',
        boxShadow: '0 0 18px var(--accent-glow)',
        animation: 'scanLine 2.4s ease-in-out infinite',
      }}/>
    </div>
  );
}

function OnboVisualTrade() {
  return (
    <div className="row gap-2" style={{ alignItems: 'center' }}>
      <div className="foil-soft" style={{ width: 80, height: 112, borderRadius: 8, border: '1px solid var(--hairline)' }}/>
      <div style={{
        width: 36, height: 36, borderRadius: 18,
        background: 'var(--bg-2)', border: '1px solid var(--hairline)',
        display: 'grid', placeItems: 'center', color: 'var(--accent)',
      }}>
        <Icon name="swap" size={16}/>
      </div>
      <div className="foil-soft" style={{ width: 80, height: 112, borderRadius: 8, border: '1px solid var(--hairline)' }}/>
    </div>
  );
}

function OnboVisualRender({ tweaks, setTweak }) {
  const opts = [
    { id: 'svg', label: 'Glyph' },
    { id: 'stripe', label: 'Stripe' },
    { id: 'placeholder', label: 'Sleeve' },
  ];
  return (
    <div className="row gap-2">
      {opts.map(o => (
        <button key={o.id} className="tap" onClick={() => setTweak('cardRender', o.id)} style={{
          display: 'flex', flexDirection: 'column', gap: 6, alignItems: 'center',
          padding: 8, borderRadius: 10,
          background: tweaks.cardRender === o.id ? 'var(--bg-1)' : 'transparent',
          border: '1px solid', borderColor: tweaks.cardRender === o.id ? 'var(--accent)' : 'var(--hairline-soft)',
        }}>
          <CardArt card={window.CARDS[0]} renderMode={o.id} size="sm"/>
          <span style={{ fontSize: 11, color: 'var(--ink-2)', fontWeight: 500 }}>{o.label}</span>
        </button>
      ))}
    </div>
  );
}

window.SettingsScreen = SettingsScreen;
window.OnboardingScreen = OnboardingScreen;
