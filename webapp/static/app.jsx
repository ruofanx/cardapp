/* App shell — navigation stack, tab bar, edit mode wiring, backend wiring */

const { useState: useStateApp, useEffect: useEffectApp, useCallback: useCallbackApp } = React;

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

  const identifyCard = useCallbackApp(async ({ query, image }) => {
    if (!window.api) return [];
    try {
      const candidates = await window.api.identify({ query, image });
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
    reloadCollection: () => reloadCollection(currentUser?.id),
    users, currentUser, setCurrentUser,
    backend,
    params: top.params,
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

  return (
    <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column' }}>
      {backend.online === false && <BackendBanner backend={backend} onRetry={() => reloadCollection(currentUser?.id)}/>}
      <Screen {...screenProps} key={top.screen}/>
      {!hideTabBar && <BottomTabBar tab={tab} navigate={navigate}/>}
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

function BottomTabBar({ tab, navigate }) {
  const items = [
    { id: 'home',     label: 'Home',    icon: 'home' },
    { id: 'browse',   label: 'Binder',  icon: 'browse' },
    { id: 'scan',     label: 'Scan',    icon: 'scan', primary: true },
    { id: 'trade',    label: 'Trade',   icon: 'trade' },
    { id: 'settings', label: 'You',     icon: 'profile' },
  ];
  return (
    <div style={{
      flexShrink: 0,
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
              display: 'grid', placeItems: 'center', padding: '4px 0',
            }}>
              <div style={{
                width: 44, height: 44, borderRadius: 22,
                background: 'var(--accent)', color: 'var(--accent-ink)',
                display: 'grid', placeItems: 'center',
                boxShadow: '0 4px 14px var(--accent-glow)',
              }}>
                <Icon name={it.icon} size={20}/>
              </div>
            </button>
          );
        }
        return (
          <button key={it.id} className="tap" onClick={() => navigate(it.id)} style={{
            display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2,
            padding: '8px 0 4px',
            color: active ? 'var(--ink)' : 'var(--ink-3)',
            background: 'transparent',
          }}>
            <Icon name={it.icon} size={20} stroke={active ? 2 : 1.6}/>
            <span style={{ fontSize: 10, fontWeight: active ? 600 : 500 }}>{it.label}</span>
          </button>
        );
      })}
    </div>
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
