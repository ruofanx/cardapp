/* App shell — navigation stack, tab bar, edit mode wiring, backend wiring */

const { useState: useStateApp, useEffect: useEffectApp, useCallback: useCallbackApp } = React;

let _authToken = null;
function getAuthToken() { return _authToken; }

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "theme": "dark",
  "cardRender": "svg",
  "currency": "USD",
  "showDiagnostics": true,
  "accentHue": 165,
  "density": "regular",
  "scanFlash": true
}/*EDITMODE-END*/;

function App() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const [stack, setStack] = useStateApp([{ screen: 'home', params: {} }]);
  const [tab, setTab] = useStateApp('home');
  const [scanQueue, setScanQueue] = useStateApp([]);
  const [collection, setCollection] = useStateApp([]);
  const [users, setUsers] = useStateApp([]);
  const [currentUser, setCurrentUser] = useStateApp(null);
  const [backend, setBackend] = useStateApp({ online: null, error: null, busy: false });
  const [authed, setAuthed] = useStateApp(false);

  const top = stack[stack.length - 1];

  // -- Backend bootstrap ---------------------------------------------------
  const reloadCollection = useCallbackApp(async (userId) => {
    if (!window.api) return;
    setBackend(b => ({ ...b, busy: true }));
    try {
      const cards = await window.api.listCards(userId);
      setCollection(cards);
      setBackend({ online: true, error: null, busy: false });
    } catch (e) {
      // Backend offline: leave the collection empty rather than substituting
      // mock data that looks real. The offline banner makes the state explicit.
      setCollection([]);
      setBackend({ online: false, error: String(e.message || e), busy: false });
    }
  }, []);

  useEffectApp(() => {
    let cancelled = false;
    (async () => {
      if (!window.api) return;
      try {
        // Discover real route paths via FastAPI's OpenAPI schema before
        // the first call — avoids guessing /users vs /api/users etc.
        await window.api.bootstrap();
        const us = await window.api.listUsers();
        if (cancelled) return;
        setUsers(us);
        const me = us[0] || { id: 1, name: 'Demo' };
        setCurrentUser(me);
        window.api.state.currentUserId = me.id;
        await reloadCollection(me.id);
      } catch (e) {
        if (cancelled) return;
        setBackend({ online: false, error: String(e.message || e), busy: false });
        setCollection([]);
      }
    })();
    return () => { cancelled = true; };
  }, [reloadCollection]);

  // -- Navigation ----------------------------------------------------------
  const navigate = useCallbackApp((screen, params = {}) => {
    const tabScreens = ['home', 'browse', 'scan', 'trade', 'settings'];
    if (tabScreens.includes(screen)) {
      setTab(screen);
      setStack([{ screen, params }]);
    } else {
      setStack(s => [...s, { screen, params }]);
    }
  }, []);

  const goBack = useCallbackApp(() => {
    setStack(s => s.length > 1 ? s.slice(0, -1) : s);
  }, []);

  // -- Backend-aware actions ----------------------------------------------
  const addToCollection = useCallbackApp(async (card) => {
    if (!card) return;
    // Optimistic insert with a temp id so UI updates immediately.
    const tempId = 'tmp-' + Date.now();
    const optimistic = { ...card, id: tempId, _pending: true };
    setCollection(c => [optimistic, ...c]);

    if (backend.online === false || !window.api) {
      // Demo mode — keep the optimistic record (and its tempId, so we don't
      // collide with the seed mock card's id like "c1").
      setCollection(c => c.map(x => x.id === tempId ? { ...x, _pending: false } : x));
      return optimistic;
    }
    try {
      const saved = await window.api.addCard({
        user_id:   currentUser?.id,
        name:      card.name,
        set:       card.set,
        code:      card.code,
        lang:      card.lang,
        condition: card.condition,
        grade:     card.grade ?? null,
        grader:    card.grader ?? null,
        is_graded: card.is_graded ?? Boolean(card.grade && card.grader),
        holo:      Boolean(card.holo),
        variant:   card.variant ?? null,
        image_url: card.image_url ?? null,
        current_market_price: card.usd ?? null,
        purchase_price: card.purchase_price ?? null,
        tags: Array.isArray(card.tags) ? card.tags : [],
      });
      // If a captured photo came with the card, save it to the per-card
      // user gallery now that we have a real backend id.
      if (saved?.id && card._capturedPhotoFile && window.userPhotos?.add) {
        try { await window.userPhotos.add(saved.id, card._capturedPhotoFile); }
        catch (e) { console.warn('userPhotos add failed:', e.message); }
      }
      setCollection(c => c.map(x => x.id === tempId ? saved : x));
      setBackend(b => ({ ...b, online: true, error: null }));
      return saved;
    } catch (e) {
      // Roll back the optimistic insert and surface the error.
      setCollection(c => c.filter(x => x.id !== tempId));
      if (e.networkError) {
        setBackend(b => ({ ...b, online: false, error: String(e.message || e) }));
      }
      throw e;
    }
  }, [backend.online, currentUser]);

  const removeCard = useCallbackApp(async (cardOrId) => {
    const cardId = typeof cardOrId === 'object' && cardOrId ? cardOrId.id : cardOrId;
    if (!cardId) return false;
    // Snapshot for rollback and optimistic remove.
    const prev = collection;
    setCollection(c => c.filter(x => x.id !== cardId));
    // Optimistic-only path for demo mode / unsaved temp rows.
    if (backend.online === false || !window.api || String(cardId).startsWith('tmp-')) {
      return true;
    }
    try {
      await window.api.deleteCard(cardId);
      setBackend(b => ({ ...b, online: true, error: null }));
      return true;
    } catch (e) {
      // Roll back if the backend rejected the delete.
      setCollection(prev);
      if (e.networkError) {
        setBackend(b => ({ ...b, online: false, error: String(e.message || e) }));
      }
      throw e;
    }
  }, [backend.online, collection]);

  // Partial update of a card row (purchase_price, tags, notes, condition, …).
  // Patches the backend then merges the normalized result into collection.
  const updateCard = useCallbackApp(async (cardId, fields) => {
    if (!window.api || !cardId) return null;
    if (backend.online === false) return null;
    try {
      const updated = await window.api.patchCard(cardId, fields);
      setCollection(c => c.map(x => x.id === cardId ? updated : x));
      setBackend(b => ({ ...b, online: true, error: null }));
      return updated;
    } catch (e) {
      if (e.networkError) {
        setBackend(b => ({ ...b, online: false, error: String(e.message || e) }));
      }
      throw e;
    }
  }, [backend.online]);

  const refreshPrice = useCallbackApp(async (cardOrId) => {
    if (!window.api || backend.online === false) return null;
    // Accept either a full card object or just an id (look up the card).
    const card = (typeof cardOrId === 'object' && cardOrId)
      ? cardOrId
      : (collection.find(x => x.id === cardOrId));
    if (!card) return null;
    setCollection(c => c.map(x => x.id === card.id ? { ...x, _refreshing: true } : x));
    try {
      const updated = await window.api.refreshPrice(card);
      setCollection(c => c.map(x => x.id === card.id ? { ...updated, _refreshing: false } : x));
      setBackend(b => ({ ...b, online: true, error: null }));
      return updated;
    } catch (e) {
      // Pricing failed — fall back to image-only lookup against Pokemon TCG
      // API so the card art still loads even when the backend can't price it.
      let withImage = null;
      try { withImage = await window.api.lookupCardImage(card); } catch (_) {}
      setCollection(c => c.map(x => x.id === card.id ? {
        ...(withImage || x),
        _refreshing: false,
        _priceUnavailable: true,
      } : x));
      if (e.networkError) {
        setBackend(b => ({ ...b, online: false, error: String(e.message || e) }));
      }
      return null;
    }
  }, [backend.online, collection]);

  const identifyCard = useCallbackApp(async ({ query, image, productTypeHint }) => {
    if (!window.api) return [];
    try {
      const candidates = await window.api.identify({ query, image, productTypeHint });
      setBackend(b => ({ ...b, online: true, error: null }));
      return candidates;
    } catch (e) {
      if (e.networkError) {
        setBackend(b => ({ ...b, online: false, error: String(e.message || e) }));
      }
      return [];
    }
  }, []);

  // Apply theme + accent
  useEffectApp(() => {
    const root = document.documentElement;
    root.setAttribute('data-theme', t.theme);
    root.style.setProperty('--accent-hue', t.accentHue);
    root.style.setProperty('--density', t.density);
  }, [t.theme, t.accentHue, t.density]);

  // Auth: restore existing session + keep token refreshed
  useEffectApp(() => {
    if (!window._supabase) return;
    window._supabase.auth.getSession().then(({ data }) => {
      if (data.session) {
        _authToken = data.session.access_token;
        setAuthed(true);
      }
    });
    const { data: { subscription } } = window._supabase.auth.onAuthStateChange((_event, session) => {
      _authToken = session?.access_token || null;
      setAuthed(!!session);
    });
    return () => subscription.unsubscribe();
  }, []);

  function handleLogin(token, session) {
    _authToken = token;
    setAuthed(true);
  }

  async function handleSignOut() {
    if (window._supabase) await window._supabase.auth.signOut();
    _authToken = null;
    setAuthed(false);
  }

  const screenProps = {
    tweaks: t, setTweak,
    navigate: (s, p) => s === '__back' ? goBack() : navigate(s, p),
    goBack,
    scanQueue, setScanQueue,
    collection,
    addToCollection,
    removeCard,
    updateCard,
    refreshPrice,
    identifyCard,
    reloadCollection: (uid) => reloadCollection(uid ?? currentUser?.id),
    users, currentUser, setCurrentUser,
    backend,
    params: top.params,
    onSignOut: handleSignOut,
  };

  let Screen;
  switch (top.screen) {
    case 'home':       Screen = HomeScreen; break;
    case 'browse':     Screen = BrowseScreen; break;
    case 'scan':       Screen = ScanScreen; break;
    case 'detail':     Screen = DetailScreen; break;
    case 'bulk':       Screen = BulkScreen; break;
    case 'trade':      Screen = TradeScreen; break;
    case 'settings':   Screen = SettingsScreen; break;
    case 'onboarding': Screen = OnboardingScreen; break;
    default:           Screen = HomeScreen;
  }

  const hideTabBar = top.screen === 'onboarding' || top.screen === 'detail' || top.screen === 'bulk' || top.screen === 'trade' || top.screen === 'scan';

  if (!authed) {
    return <LoginScreen onLogin={handleLogin} />;
  }

  return (
    <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column' }}>
      {backend.online === false && <BackendBanner backend={backend} onRetry={() => reloadCollection(currentUser?.id)}/>}
      <Screen {...screenProps} key={top.screen}/>
      {!hideTabBar && (
        <BottomTabBar
          tab={tab} navigate={navigate}
          users={users} currentUser={currentUser} setCurrentUser={setCurrentUser}
          reloadCollection={reloadCollection}
          scanQueueCount={scanQueue.length}
        />
      )}
      <TweaksHook t={t} setTweak={setTweak}/>
    </div>
  );
}

function BackendBanner({ backend, onRetry }) {
  return (
    <div style={{
      flexShrink: 0,
      padding: '6px 12px',
      background: 'oklch(0.30 0.10 30 / 0.85)',
      color: 'oklch(0.98 0.02 80)',
      fontSize: 11, fontWeight: 600, letterSpacing: '0.02em',
      display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8,
    }}>
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        Backend offline · demo data {backend.error ? '· ' + backend.error.slice(0, 60) : ''}
      </span>
      <button className="tap" onClick={onRetry} style={{
        padding: '2px 8px', borderRadius: 6,
        background: 'oklch(1 0 0 / 0.15)', color: 'inherit',
        fontSize: 11, fontWeight: 600,
      }}>Retry</button>
    </div>
  );
}

function BottomTabBar({ tab, navigate, users = [], currentUser, setCurrentUser, reloadCollection, scanQueueCount = 0 }) {
  const [showSwitcher, setShowSwitcher] = useStateApp(false);
  const items = [
    { id: 'home',     label: 'Home',    icon: 'home' },
    { id: 'browse',   label: 'Binder',  icon: 'browse' },
    { id: 'scan',     label: 'Scan',    icon: 'scan', primary: true },
    { id: 'trade',    label: 'Trade',   icon: 'trade' },
    { id: 'settings', label: currentUser?.name || 'You', icon: 'profile' },
  ];

  const switchTo = (u) => {
    setShowSwitcher(false);
    if (!u || u.id === currentUser?.id) return;
    if (setCurrentUser) setCurrentUser(u);
    if (window.api) window.api.state.currentUserId = u.id;
    if (reloadCollection) reloadCollection(u.id);
  };

  return (
    <div style={{ position: 'relative', flexShrink: 0 }}>
      {showSwitcher && users.length > 1 && (
        <AccountSwitcherSheet users={users} currentUser={currentUser} onSelect={switchTo} onClose={() => setShowSwitcher(false)}/>
      )}
      <div style={{
        padding: '6px 6px calc(env(safe-area-inset-bottom, 0px) + 6px)',
        background: 'oklch(0.16 0.01 250 / 0.78)',
        backdropFilter: 'blur(20px) saturate(140%)',
        borderTop: '1px solid var(--hairline-soft)',
        display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)',
      }}>
        {items.map(it => {
          const active = tab === it.id;
          if (it.primary) {
            return (
              <button key={it.id} className="tap" onClick={() => navigate(it.id)} style={{
                display: 'grid', placeItems: 'center', padding: '4px 0', position: 'relative',
              }}>
                <div style={{
                  width: 44, height: 44, borderRadius: 22,
                  background: 'var(--accent)', color: 'var(--accent-ink)',
                  display: 'grid', placeItems: 'center',
                  boxShadow: '0 4px 14px var(--accent-glow)',
                }}>
                  <Icon name={it.icon} size={20}/>
                </div>
                {scanQueueCount > 0 && (
                  <div style={{
                    position: 'absolute', top: 2, right: '50%', transform: 'translateX(14px)',
                    minWidth: 16, height: 16, borderRadius: 8,
                    background: '#ef4444', color: '#fff',
                    fontSize: 10, fontWeight: 700, lineHeight: '16px',
                    textAlign: 'center', padding: '0 4px',
                    border: '1.5px solid var(--bg)',
                  }}>{scanQueueCount}</div>
                )}
              </button>
            );
          }
          const isProfile = it.id === 'settings';
          return (
            <button
              key={it.id}
              className="tap"
              onClick={() => navigate(it.id)}
              onDoubleClick={isProfile ? () => { if (users.length > 1) setShowSwitcher(s => !s); } : undefined}
              style={{
                display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2,
                padding: '8px 0 4px',
                color: active ? 'var(--ink)' : 'var(--ink-3)',
                background: 'transparent',
              }}>
              <Icon name={it.icon} size={20} stroke={active ? 2 : 1.6}/>
              <span style={{ fontSize: 10, fontWeight: active ? 600 : 500, maxWidth: 60, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{it.label}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// Quick account switcher — pops up above the profile tab on double-click,
// letting you jump straight to another family member without opening Settings.
function AccountSwitcherSheet({ users, currentUser, onSelect, onClose }) {
  return (
    <>
      <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 60 }}/>
      <div className="col" style={{
        position: 'absolute', right: 8, bottom: '100%', marginBottom: 8, zIndex: 61,
        minWidth: 168,
        background: 'var(--bg-2)', border: '1px solid var(--hairline-soft)',
        borderRadius: 14, boxShadow: 'var(--shadow-pop)', overflow: 'hidden',
      }}>
        <div style={{ padding: '10px 14px 6px', fontSize: 11, fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--ink-3)' }}>
          Switch account
        </div>
        {users.map(u => {
          const active = u.id === currentUser?.id;
          return (
            <button key={u.id} className="tap row gap-2" onClick={() => onSelect(u)} style={{
              padding: '10px 14px', width: '100%',
              background: active ? 'var(--accent-soft)' : 'transparent',
              borderTop: '1px solid var(--hairline-soft)',
            }}>
              <span style={{
                width: 26, height: 26, borderRadius: 13, flexShrink: 0,
                display: 'grid', placeItems: 'center',
                background: active ? 'var(--accent)' : 'var(--bg-3)',
                color: active ? 'var(--accent-ink)' : 'var(--ink-2)',
                fontSize: 12, fontWeight: 700,
              }}>{(u.name || '?').trim().charAt(0).toUpperCase()}</span>
              <span style={{ flex: 1, textAlign: 'left', fontSize: 14, fontWeight: active ? 600 : 500, color: active ? 'var(--ink)' : 'var(--ink-2)' }}>{u.name}</span>
              {active && <Icon name="check" size={16} style={{ color: 'var(--accent)' }}/>}
            </button>
          );
        })}
      </div>
    </>
  );
}

/* Tweaks panel — only visible when host activates edit mode */
function TweaksHook({ t, setTweak }) {
  return (
    <TweaksPanel title="Tweaks">
      <TweakSection label="Theme"/>
      <TweakRadio label="Mode" value={t.theme}
        options={[{ value: 'dark', label: 'Dark' }, { value: 'light', label: 'Light' }]}
        onChange={v => setTweak('theme', v)}/>
      <TweakSlider label="Accent hue" value={t.accentHue} min={0} max={360} step={5}
        onChange={v => setTweak('accentHue', v)}/>
      <TweakRadio label="Density" value={t.density}
        options={['compact', 'regular', 'comfy']}
        onChange={v => setTweak('density', v)}/>

      <TweakSection label="Card art"/>
      <TweakSelect label="Render" value={t.cardRender}
        options={[
          { value: 'svg', label: 'Glyph' },
          { value: 'stripe', label: 'Stripe placeholder' },
          { value: 'placeholder', label: 'Sleeve' },
          { value: 'photo', label: 'Photo slot' },
        ]}
        onChange={v => setTweak('cardRender', v)}/>

      <TweakSection label="Pricing"/>
      <TweakSelect label="Currency" value={t.currency}
        options={[
          { value: 'USD', label: '$ USD' },
          { value: 'JPY', label: '¥ JPY' },
          { value: 'EUR', label: '€ EUR' },
          { value: 'BOTH', label: 'Both' },
        ]}
        onChange={v => setTweak('currency', v)}/>

      <TweakSection label="Diagnostics"/>
      <TweakToggle label="Pipeline overlay" value={t.showDiagnostics}
        onChange={v => setTweak('showDiagnostics', v)}/>
      <TweakToggle label="Scan flash" value={t.scanFlash}
        onChange={v => setTweak('scanFlash', v)}/>
    </TweaksPanel>
  );
}

window.App = App;
