/* api.jsx — backend client for the CardApp FastAPI server.
 *
 * Real routes (from `grep '@app.\(get\|post\|patch\|delete\)' app.py`):
 *   GET    /api/users
 *   GET    /api/users/{user_id}/portfolio
 *   GET    /api/users/{user_id}/cards
 *   POST   /api/users/{user_id}/cards
 *   PATCH  /api/cards/{card_id}
 *   DELETE /api/cards/{card_id}
 *   POST   /api/cards/{card_id}/photo
 *   GET    /api/cards/search?q=...
 *   POST   /api/identify             (json {query} or multipart {image})
 *   POST   /api/refresh-price        (body: {card_id})
 *   POST   /api/refresh-prices/run-now
 *   POST   /api/trade/propose
 *
 * If the backend is unreachable, screens fall back to window.CARDS mock
 * data and the app shows a "Backend offline — demo data" banner.
 */

(function () {
  function _authHeader() {
    const token = typeof getAuthToken === 'function' ? getAuthToken() : null;
    return token ? { 'Authorization': `Bearer ${token}` } : {};
  }

  // Default to same-host:8000 so the page works from any LAN IP — phone hits
  // http://192.168.x.x:5173 and the API resolves to http://192.168.x.x:8000.
  // Override with window.POKECOLLECT_API or api.setBase() if backend is elsewhere.
  function defaultBase() {
    if (typeof window !== 'undefined' && window.POKECOLLECT_API) return window.POKECOLLECT_API;
    if (typeof window !== 'undefined' && window.location && window.location.hostname) {
      const port = window.location.port;
      // On Railway (port 443/80), use origin as-is; on local dev use :8000
      if (!port || port === '80' || port === '443') {
        return `${window.location.protocol}//${window.location.hostname}`;
      }
      return `${window.location.protocol}//${window.location.hostname}:8000`;
    }
    return 'http://localhost:8000';
  }
  const DEFAULT_BASE = defaultBase();

  const state = {
    base: DEFAULT_BASE,
    online: null,
    lastError: null,
    currentUserId: null,
  };

  const P = {
    users:           () => `/api/users`,
    userPortfolio:   (uid) => `/api/users/${uid}/portfolio`,
    userCards:       (uid) => `/api/users/${uid}/cards`,
    cardsSearch:     (q) => `/api/cards/search?q=${encodeURIComponent(q)}`,
    pricechartingSearch: (q, limit) => `/api/pricecharting/search?q=${encodeURIComponent(q)}&limit=${limit}`,
    card:            (cid) => `/api/cards/${cid}`,
    cardPhoto:       (cid) => `/api/cards/${cid}/photo`,
    cardPriceHistory:(cid) => `/api/cards/${cid}/price-history`,
    identify:        () => `/api/identify`,
    refreshPrice:    () => `/api/refresh-price`,
    refreshAll:      () => `/api/refresh-prices/run-now`,
    soldListings:    () => `/api/sold-listings`,
    tradePropose:    () => `/api/trade/propose`,
  };

  async function request(path, opts = {}) {
    const url = `${state.base}${path}`;
    const init = {
      method: opts.method || 'GET',
      headers: { 'Accept': 'application/json', ..._authHeader(), ...(opts.headers || {}) },
      ...opts,
    };
    if (opts.body && !(opts.body instanceof FormData) && typeof opts.body === 'object') {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(opts.body);
    } else if (opts.body) {
      init.body = opts.body;
    }
    try {
      const res = await fetch(url, init);
      if (!res.ok) {
        const text = await res.text().catch(() => '');
        const err = new Error(`${res.status} ${res.statusText}${text ? ' — ' + text.slice(0, 200) : ''}`);
        // HTTP error responses mean the backend is reachable — don't flip
        // global online state to false. Callers can decide what to do per-call.
        err.status = res.status;
        err.networkError = false;
        state.online = true;
        state.lastError = null;
        throw err;
      }
      state.online = true;
      state.lastError = null;
      const ct = res.headers.get('content-type') || '';
      return ct.includes('application/json') ? res.json() : res.text();
    } catch (e) {
      // Only true network failures (fetch rejected without ever getting a
      // response) should flip the global "offline" indicator.
      if (e.networkError === false || e.status) {
        throw e;
      }
      e.networkError = true;
      state.online = false;
      state.lastError = String(e.message || e);
      throw e;
    }
  }

  // -- Normalizers ---------------------------------------------------------
  function normalizeLang(v) {
    const s = String(v ?? 'EN').toLowerCase();
    if (s.startsWith('jp') || s.startsWith('ja')) return 'JP';
    if (s.startsWith('ch') || s.startsWith('zh')) return 'CH';
    return 'EN';
  }
  function denormalizeLang(v) {
    const u = String(v).toUpperCase();
    if (u === 'JP') return 'japanese';
    if (u === 'CH') return 'chinese';
    return 'english';
  }
  function normalizeCard(c) {
    if (!c || typeof c !== 'object') return null;
    // Keep usd as null when truly unknown so the UI can render "—" instead
    // of misleading "$0.00" for cards Cardmarket / TCGplayer doesn't price
    // (most JA-only sets, brand-new promos, etc.).
    const usd = num(c.current_market_price) ?? num(c.market_value_usd)
             ?? num(c.estimated_price) ?? num(c.market_price)
             ?? num(c.usd) ?? num(c.price)
             ?? num(c.value) ?? num(c.market_value) ?? num(c.fair_value)
             ?? null;
    const isGraded = Boolean(c.is_graded ?? (c.grade_company && c.grade));
    return {
      id:        String(c.id ?? c.card_id ?? c.uuid ?? cryptoId()),
      name:      c.name ?? c.card_name ?? 'Unknown',
      code:      c.card_number ?? c.code ?? c.number ?? '',
      set:       c.set_name ?? c.set ?? '',
      lang:      normalizeLang(c.language ?? c.lang),
      condition: c.condition ?? 'NM',
      grade:     isGraded ? (num(c.grade) ?? null) : null,
      grader:    isGraded ? (c.grade_company ?? c.grader ?? null) : null,
      is_graded: isGraded,
      usd,
      change:    num(c.gain_loss_pct) ?? num(c.change_24h) ?? num(c.change) ?? 0,
      gain_loss: num(c.gain_loss),
      purchase_price: num(c.purchase_price),
      holo:      Boolean(c.holo ?? c.is_holo ?? c.foil),
      bulk:      Boolean(c.bulk) || (usd > 0 && usd < 5),
      hue:       num(c.hue) ?? hashHue(c.name || ''),
      glyph:     c.glyph || 'spark',
      variant:   c.variant ?? c.rarity ?? null,
      hp:        c.hp ?? null,
      // Strip EN-only image sources for JP/CH cards — EN card art on a JP
      // card in the collection is misleading (different language text on card).
      // pokemontcg.io = EN API CDN; assets.tcgdex.net/en/ = TCGdex EN path.
      // TCGdex JA images live at assets.tcgdex.net/ja/ and are kept.
      // Exception: SV-era EN tcgdex images are allowed because JP and EN SV
      // cards share identical illustration art (JP is the source).
      image_url: (() => {
        const raw = c.image_url ?? c.image ?? null;
        const lang = normalizeLang(c.language ?? c.lang);
        if (raw && (lang === 'JP' || lang === 'CH')) {
          if (raw.includes('images.pokemontcg.io')) return null;
          if (raw.includes('assets.tcgdex.net/en/') && !raw.includes('/en/sv/')) return null;
        }
        return raw;
      })(),
      product_type: c.product_type || "card",
      photo_path:c.photo_path ?? null,
      last_priced_at: c.last_priced_at ?? c.last_refreshed ?? null,
      notes:     c.notes ?? null,
      tags:      c.tags ?? [],
      raw:       c,
    };
  }
  function isSealedProduct(card) {
    return card.product_type != null && card.product_type !== "card";
  }
  const num = (v) => (v == null || v === '' || isNaN(Number(v))) ? null : Number(v);
  const cryptoId = () => 'c' + Math.random().toString(36).slice(2, 9);
  const hashHue = (s) => {
    let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
    return Math.abs(h) % 360;
  };

  // Distinct-print variant key → display label. Mirrors card_lookup.py's
  // _VARIANT_KEY_LABELS / _explode_variants — old WotC holos (Base/Jungle/
  // Fossil/Team Rocket) carry BOTH `1stEditionHolofoil` and
  // `unlimitedHolofoil` price keys with very different market values; those
  // are genuinely different physical cards (1st Edition vs Unlimited), not
  // one card with two prices. searchPokemonTCG used to pick a single price
  // via fallback chain, producing duplicate-looking candidates with no way
  // to tell which print was which. Splitting into one row per print (each
  // labelled and priced correctly) is what lets the trade picker show
  // "1st Edition Holo · $312" vs "Unlimited Holo · $172" instead of two
  // identical "Team Rocket" thumbnails.
  const VARIANT_KEY_LABELS = {
    '1stEditionHolofoil': '1st Edition Holo',
    'unlimitedHolofoil':  'Unlimited Holo',
    '1stEditionNormal':   '1st Edition',
    '1stEdition':         '1st Edition',
    'unlimited':          'Unlimited',
    'reverseHolofoil':    'Reverse Holo',
    'holofoil':           'Holo',
    'normal':             'Normal',
  };
  const VARIANT_DISPLAY_ORDER = [
    '1st Edition Holo', 'Unlimited Holo',
    '1st Edition', 'Unlimited',
    'Holo', 'Normal',
    'Reverse Holo',
  ];
  // Returns [[label, price], ...] — one entry per distinct print when the
  // catalogue has 2+, else a single [null, price] entry (no label noise for
  // cards with only one print).
  function explodeVariants(hit, cmFallback) {
    const prices = hit.tcgplayer?.prices || {};
    const seen = new Set();
    const unique = [];
    for (const [key, label] of Object.entries(VARIANT_KEY_LABELS)) {
      const market = num(prices[key]?.market);
      if (market == null || seen.has(label)) continue;
      seen.add(label);
      unique.push([label, market]);
    }
    if (unique.length >= 2) {
      const order = Object.fromEntries(VARIANT_DISPLAY_ORDER.map((l, i) => [l, i]));
      unique.sort((a, b) => (order[a[0]] ?? 99) - (order[b[0]] ?? 99));
      return unique;
    }
    if (unique.length === 1) return [[null, unique[0][1]]];
    return [[null, cmFallback ?? null]];
  }

  function unwrapList(data, key) {
    if (Array.isArray(data)) return data;
    if (data && Array.isArray(data[key])) return data[key];
    if (data && Array.isArray(data.items)) return data.items;
    if (data && Array.isArray(data.results)) return data.results;
    return [];
  }

  // -- Public API ----------------------------------------------------------
  const api = {
    state,
    setBase(url) { state.base = url; },

    async listUsers() {
      try {
        const data = await request(P.users());
        const users = unwrapList(data, 'users');
        return users.map(u => ({ id: u.id ?? u.user_id, name: u.name ?? u.display_name ?? `User ${u.id}` }));
      } catch {
        return [{ id: 1, name: 'Demo' }];
      }
    },

    async portfolio(userId) {
      try { return await request(P.userPortfolio(userId)); }
      catch { return null; }
    },

    async listCards(userId) {
      const id = userId ?? state.currentUserId;
      if (id == null) return [];
      const data = await request(P.userCards(id));
      const list = unwrapList(data, 'cards');
      return list.map(normalizeCard).filter(Boolean);
    },

    async addCard(payload) {
      const uid = payload.user_id ?? state.currentUserId;
      if (uid == null) throw new Error('No current user');
      const body = {
        name:        payload.name,
        set_name:    payload.set ?? null,
        card_number: payload.code ?? null,
        language:    denormalizeLang(payload.lang ?? 'EN'),
        condition:   payload.condition ?? 'NM',
        variant:     payload.variant ?? null,
        is_graded:   Boolean(payload.is_graded ?? (payload.grade && payload.grader)),
        grade_company: payload.grader ?? null,
        grade:       payload.grade ?? null,
        image_url:   payload.image_url ?? null,
        current_market_price: payload.current_market_price ?? payload.usd ?? null,
        purchase_price:       payload.purchase_price ?? null,
        tags:                 Array.isArray(payload.tags) ? payload.tags : (payload.tags ? [payload.tags] : []),
        product_type: payload.product_type ?? 'card',
      };
      let data;
      try {
        data = await request(P.userCards(uid), { method: 'POST', body });
      } catch (e) {
        // Some backends reject unknown fields. Retry with the legacy schema.
        const legacy = { ...body };
        delete legacy.image_url;
        delete legacy.current_market_price;
        delete legacy.purchase_price;
        delete legacy.tags;
        data = await request(P.userCards(uid), { method: 'POST', body: legacy });
      }
      let saved = normalizeCard(data);
      // Belt-and-braces: if the create dropped image/price/grading, PATCH them.
      const patch = {};
      if (!saved.image_url && body.image_url) patch.image_url = body.image_url;
      if (!saved.usd       && body.current_market_price != null) patch.current_market_price = body.current_market_price;
      if (body.is_graded && !saved.is_graded) {
        patch.is_graded = true;
        if (body.grade != null)    patch.grade = body.grade;
        if (body.grade_company)    patch.grade_company = body.grade_company;
      }
      if (body.purchase_price != null && saved.purchase_price == null) {
        patch.purchase_price = body.purchase_price;
      }
      if (body.tags && body.tags.length && (!saved.tags || saved.tags.length === 0)) {
        patch.tags = body.tags;
      }
      if (Object.keys(patch).length > 0 && saved.id) {
        try { saved = await api.patchCard(saved.id, patch); } catch (_) { /* keep what we have */ }
      }
      return saved;
    },

    async patchCard(cardId, fields) {
      const data = await request(P.card(cardId), { method: 'PATCH', body: fields });
      return normalizeCard(data);
    },

    async deleteCard(cardId) {
      await request(P.card(cardId), { method: 'DELETE' });
      return true;
    },

    async searchCards(query) {
      if (!query) return [];
      try {
        const data = await request(P.cardsSearch(query));
        const list = unwrapList(data, 'cards');
        return list.map(normalizeCard).filter(Boolean);
      } catch { return []; }
    },

    // Photo identify only — backend route is `photo: UploadFile = File(...)`.
    // Response shape: { identity: { name, set_name, card_number }, market_price, image_url }
    // `productTypeHint` ("card" | "sealed") comes from the Scan screen's
    // pre-scan TYPE toggle — passed through so the backend can bias the OCR
    // prompt and (for "card") force the response's product_type.
    async identifyPhoto(file, productTypeHint) {
      const fd = new FormData();
      fd.append('photo', file);
      if (productTypeHint && productTypeHint !== 'auto') fd.append('product_type_hint', productTypeHint);
      const res = await request(P.identify(), { method: 'POST', body: fd });
      // If the backend found catalogue matches via broad search, use those so
      // the widening phase in Scan.jsx has a real seed (name, set, image_url).
      // Merge the OCR identity fields so grading/language info is preserved.
      if (res?.candidates?.length > 0) {
        const identity = res.identity || {};
        return res.candidates
          .map(c => normalizeCard({ ...identity, ...c }))
          .filter(Boolean);
      }
      // No catalogue hit — normalise the raw OCR identity so the widening
      // phase can still search by name and surface related printings.
      const flat = { ...(res?.identity || {}), ...res };
      const one = normalizeCard(flat);
      return one ? [one] : [];
    },

    // Text search for a sealed product (booster box, ETB, tin, bundle) by
    // name — e.g. "chaos rising etb". Sealed products aren't in the
    // single-card catalogues `searchCards` queries, so this posts JSON to
    // /api/identify with product_type_hint: 'sealed', which parses the
    // product type out of the query and looks up price + image via eBay
    // active listings (see app.py _identify_text_sealed).
    async identifyTextSealed(query) {
      const res = await request(P.identify(), {
        method: 'POST',
        body: { query, product_type_hint: 'sealed' },
      });
      const flat = { ...(res?.identity || {}), ...res };
      const one = normalizeCard(flat);
      return one ? [one] : [];
    },

    // Unified entry point used by Scan: photo path → /api/identify, text path → /api/cards/search
    // (or /api/identify with product_type_hint: 'sealed' when SEALED is selected).
    async identify({ query, image, productTypeHint }) {
      if (image) return this.identifyPhoto(image, productTypeHint);
      if (query) {
        if (productTypeHint === 'sealed') return this.identifyTextSealed(query);
        return this.searchCards(query);
      }
      return [];
    },

    // Quote-only variant of refreshPrice. Same backend call, but the result
    // is NOT persisted onto the card row. Used by the Detail screen to
    // preview a new price when the user toggles condition/grading without
    // committing — the user explicitly Saves to apply.
    async quotePrice(card) {
      if (!card?.name) return null;
      const body = {
        name:          card.name,
        set_name:      card.set ?? null,
        card_number:   card.code ?? null,
        language:      denormalizeLang(card.lang),
        condition:     card.condition ?? 'NM',
        variant:       card.variant ?? null,
        is_graded:     Boolean(card.is_graded ?? card.grade),
        grade_company: card.grader ?? null,
        grade:         card.grade ?? null,
        product_type:  card.product_type || "card",
      };
      const res = await request(P.refreshPrice(), { method: 'POST', body });
      return {
        estimated_price: num(res?.estimated_price),
        source:          res?.source ?? null,
        image_url:       res?.image_url ?? null,
        note:            res?.note ?? null,
        raw:             res,
      };
    },

    // Backend's /api/refresh-price is metadata-in, price-out — it does NOT
    // know which card row to update. We POST the metadata to get a quote, then
    // PATCH the card row with the new current_market_price.
    async refreshPrice(card) {
      const body = {
        name:          card.name,
        set_name:      card.set ?? null,
        card_number:   card.code ?? null,
        language:      denormalizeLang(card.lang),
        condition:     card.condition ?? 'NM',
        variant:       card.variant ?? null,
        is_graded:     Boolean(card.is_graded ?? card.grade),
        grade_company: card.grader ?? null,
        grade:         card.grade ?? null,
        product_type:  card.product_type || "card",
      };
      const res = await request(P.refreshPrice(), { method: 'POST', body });
      const newPrice = num(res?.estimated_price);
      const newImage = res?.image_url || null;
      if (newPrice != null && card.id && !/^tmp-/.test(String(card.id))) {
        try {
          const patch = { current_market_price: newPrice };
          if (newImage && !card.image_url) patch.image_url = newImage;
          const patched = await api.patchCard(card.id, patch);
          return { ...patched, _quote: res };
        } catch (e) { /* fall through — return synthesized */ }
      }
      return normalizeCard({
        ...card.raw, ...card,
        current_market_price: newPrice,
        image_url: newImage || card.image_url,
        _quote: res,
      });
    },

    async refreshAllPrices() {
      return request(P.refreshAll(), { method: 'POST' });
    },

    // Fetch the recorded price history for a card. The backend logs a row
    // each time current_market_price changes (creates, patches, refreshes),
    // and backfills existing cards from last_priced_at on startup. We hand
    // back a clean { points:[{at,price}], current, currency } shape; the UI
    // slices to the chosen range and feeds the numbers into <Sparkline>.
    async getPriceHistory(cardId, opts = {}) {
      if (!cardId) return null;
      const qs = new URLSearchParams();
      if (opts.since) qs.set('since', opts.since);
      if (opts.limit) qs.set('limit', String(opts.limit));
      const suffix = qs.toString() ? `?${qs}` : '';
      try {
        const res = await request(P.cardPriceHistory(cardId) + suffix);
        const points = Array.isArray(res?.points)
          ? res.points.map(p => ({ at: p.at, price: Number(p.price), source: p.source || null, source_url: p.source_url || null }))
                      .filter(p => Number.isFinite(p.price))
          : [];
        return {
          points,
          current:  num(res?.current),
          currency: res?.currency || 'USD',
        };
      } catch (e) {
        // 404 = card not found on backend (e.g. demo / temp id). Surface as
        // empty rather than throwing so the UI can fall back gracefully.
        if (e.status === 404) return { points: [], current: null, currency: 'USD' };
        throw e;
      }
    },

    // Fetch recent eBay sold listings for a card. Returns the full payload:
    //   { sales: [{price_usd, sold_date, title, url, source}], median_usd,
    //     sample_size, raw_sample_size, period_days, sold_url, cached }
    // Filters by language + (when graded) grader+grade. Backend caches per
    // search URL for 24h, so re-opens of the same card stay snappy.
    async getSoldListings(card, opts = {}) {
      if (!card?.name) return null;
      const body = {
        name:          card.name,
        set_name:      card.set ?? null,
        card_number:   card.code ?? null,
        language:      denormalizeLang(opts.lang ?? card.lang),
        variant:       card.variant ?? null,
        condition:     opts.condition || card.condition || 'NM',
        is_graded:     Boolean(opts.is_graded ?? card.is_graded),
        grade_company: opts.grader ?? card.grader ?? null,
        grade:         opts.grade  ?? card.grade  ?? null,
        period_days:   opts.period_days ?? 60,
        max_listings:  opts.max_listings ?? 25,
      };
      try {
        return await request(P.soldListings(), { method: 'POST', body });
      } catch (e) {
        // Surface 502s as null so the UI shows the empty state + deep link
        // fallback instead of an error toast — eBay anti-bot rejections are
        // expected and shouldn't break the screen.
        if (e.status === 502 || e.networkError) return null;
        throw e;
      }
    },

    // Multi-result search against the public Pokemon TCG API. Used by Scan to
    // give the user a pickable candidate list (with set + rarity filters).
    // Returns up to `pageSize` normalized cards. `filters` keys: name, set,
    // rarity, number, supertype.
    async searchPokemonTCG(filters = {}, { pageSize = 20, page = 1 } = {}) {
      const qs = (v) => String(v).replace(/"/g, '');
      // Build a sequence of progressively looser query bodies. We try strict
      // first, fall back to looser ones if a query returns 0 results — that
      // way special promos (UPCs, regional pre-releases) that aren't in the
      // Pokemon TCG API still surface visually-similar printings.
      const buildParts = (nameTerm) => {
        const p = [];
        if (nameTerm) p.push(nameTerm);
        if (filters.set) p.push(`set.name:"${qs(filters.set)}"`);
        if (filters.rarity) p.push(`rarity:"${qs(filters.rarity)}"`);
        if (filters.supertype) p.push(`supertype:"${qs(filters.supertype)}"`);
        if (filters.number) p.push(`number:"${String(filters.number).replace(/[^\w/-]/g, '')}"`);
        return p;
      };
      const queryStrings = [];
      if (filters.name) {
        const clean = qs(filters.name).trim();
        // 1) Exact name
        queryStrings.push(buildParts(`name:"${clean}"`).join(' '));
        // 2) Strip "ex / V / VMAX / GX / VSTAR" suffix and exact match
        const base = clean.replace(/\s+(ex|EX|V|VMAX|GX|VSTAR|VUNION)$/i, '').trim();
        if (base && base.toLowerCase() !== clean.toLowerCase()) {
          queryStrings.push(buildParts(`name:"${base}"`).join(' '));
        }
        // 3) First-word wildcard (last resort — catches typos / unusual names)
        const firstWord = (base || clean).split(/\s+/)[0];
        if (firstWord && firstWord.length >= 3) {
          queryStrings.push(buildParts(`name:${firstWord}*`).join(' '));
        }
      } else {
        const merged = buildParts(null).join(' ');
        if (merged) queryStrings.push(merged);
      }
      if (queryStrings.length === 0) return [];

      for (const qstr of queryStrings) {
        const params = new URLSearchParams({
          q: qstr,
          pageSize: String(pageSize),
          page: String(page),
          orderBy: '-set.releaseDate,number',
        });
        const url = `https://api.pokemontcg.io/v2/cards?${params}`;
        let hits = [];
        try {
          const res = await fetch(url, { headers: { Accept: 'application/json' } });
          if (!res.ok) continue;
          const data = await res.json();
          hits = data?.data || [];
        } catch (_) { continue; }
        if (hits.length === 0) continue;
        console.info(`[searchPokemonTCG] ${hits.length} hits for: ${qstr}`);
        return hits.flatMap(hit => {
          const cmPrice = num(hit.cardmarket?.prices?.averageSellPrice) ?? num(hit.cardmarket?.prices?.trendPrice);
          const variants = explodeVariants(hit, cmPrice);
          return variants.map(([variantLabel, price]) => normalizeCard({
            // e.g. "me2-18" — UI-only temp id; backend assigns one on add.
            // Distinct prints of the same card get a suffixed id so they
            // don't collide/dedupe against each other in candidate lists.
            id: variantLabel ? `${hit.id}::${variantLabel}` : hit.id,
            name: hit.name,
            card_number: hit.number,
            set_name: hit.set?.name,
            language: 'english',
            condition: 'NM',
            is_graded: false,
            variant: variantLabel || hit.rarity,
            hp: hit.hp,
            image_url: hit.images?.large || hit.images?.small || null,
            current_market_price: price ?? cmPrice ?? null,
            last_priced_at: hit.tcgplayer?.updatedAt || hit.cardmarket?.updatedAt || null,
            // Stash a few extras for UI filter chips
            _set_release: hit.set?.releaseDate,
            _set_id:      hit.set?.id,
            _rarity:      hit.rarity,
          }));
        });
      }
      // Every query returned 0 hits.
      console.info('[searchPokemonTCG] no hits for any fallback', queryStrings);
      return [];
    },

    // Build an eBay sold-listings search URL for a given card. eBay's
    // search-results page renders price + photo for every sold listing — by
    // far the most reliable signal for JP/CH cards whose Cardmarket /
    // TCGplayer coverage is poor. We open it in a new tab so the user can
    // verify the actual market price and copy it back via the manual override.
    buildEbayUrl(card, opts = {}) {
      if (!card) return null;
      const tokens = [];
      // Pokemon TCG API stores English names; TCGdex JA stores カナ. We just
      // include whichever name the candidate carries — eBay handles unicode.
      if (card.name) tokens.push(card.name);
      if (card.code) tokens.push(String(card.code).replace(/^0+/, '')); // 024 → 24
      if (card.set)  tokens.push(card.set);
      // Language qualifier hugely improves precision on eBay.
      if (card.lang === 'JP') tokens.push('Japanese');
      else if (card.lang === 'CH') tokens.push('Chinese');
      // Grading hint
      if (opts.grader && opts.grader !== 'Raw') {
        tokens.push(opts.grader);
        if (opts.grade) tokens.push(String(opts.grade));
      }
      const q = tokens.filter(Boolean).join(' ');
      const params = new URLSearchParams({
        _nkw: q,
        LH_Sold: '1',
        LH_Complete: '1',
        _sop: '13', // recently ended first
      });
      return `https://www.ebay.com/sch/i.html?${params}`;
    },

    // PokeAPI translation cache. Given an English Pokemon name (e.g.
    // "Dragonite V"), returns the Pokemon's name in JA + ZH so we can query
    // TCGdex JA / zh-tw databases (which use localized names, not English).
    // Cached in-memory for the session.
    async lookupPokemonNames(rawName) {
      if (!rawName) return null;
      // Strip trade suffixes — PokeAPI's species endpoint uses the base name.
      const base = String(rawName)
        .replace(/\s+(ex|EX|V|VMAX|GX|VSTAR|VUNION)$/i, '')
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9 -]/g, '')
        .replace(/\s+/g, '-');
      if (!base) return null;
      const cache = (state._nameCache = state._nameCache || {});
      if (cache[base]) return cache[base];
      try {
        const res = await fetch(`https://pokeapi.co/api/v2/pokemon-species/${base}`, {
          headers: { Accept: 'application/json' },
        });
        if (!res.ok) { cache[base] = null; return null; }
        const data = await res.json();
        const pick = (codes) => {
          for (const code of codes) {
            const hit = (data.names || []).find(n => n.language?.name === code);
            if (hit?.name) return hit.name;
          }
          return null;
        };
        const out = {
          en: pick(['en']) || rawName,
          ja: pick(['ja-hrkt', 'ja']),
          zh: pick(['zh-hant', 'zh-hans']),
        };
        cache[base] = out;
        return out;
      } catch (_) {
        cache[base] = null;
        return null;
      }
    },

    // Secondary data source — TCGdex covers many printings missing from
    // Pokemon TCG API (Black Star Promos like mep-024, regional sets, etc.)
    // and has both EN and JA card databases. Returns up to `pageSize`
    // normalized cards. `lang` selects database: 'en' (default) or 'ja'.
    async searchTCGdex(filters = {}, { pageSize = 20, lang = 'en', dbLang } = {}) {
      if (!filters.name) return [];
      const cleanName = String(filters.name).replace(/"/g, '').trim();
      if (!cleanName) return [];
      // dbLang controls which TCGdex language database to hit (defaults to
      // `lang`). Useful when querying JA/CH dbs with localized names while
      // still tagging the result as JP/CH for UI purposes.
      const db = dbLang || lang;
      const base = `https://api.tcgdex.net/v2/${db}/cards`;
      const tries = [cleanName];
      const stripped = cleanName.replace(/\s+(ex|EX|V|VMAX|GX|VSTAR|VUNION)$/i, '').trim();
      if (stripped && stripped.toLowerCase() !== cleanName.toLowerCase()) tries.push(stripped);

      let list = [];
      for (const t of tries) {
        try {
          const res = await fetch(`${base}?name=${encodeURIComponent(t)}`, { headers: { Accept: 'application/json' } });
          if (!res.ok) continue;
          const data = await res.json();
          if (Array.isArray(data) && data.length > 0) { list = data; break; }
        } catch (_) { continue; }
      }
      if (list.length === 0) return [];
      // List endpoint returns thin records; fetch full details (with prices +
      // set + rarity) for the top `pageSize` candidates in parallel.
      const detailed = await Promise.all(list.slice(0, pageSize).map(async (thin) => {
        try {
          const res = await fetch(`${base}/${thin.id}`, { headers: { Accept: 'application/json' } });
          if (!res.ok) return null;
          return await res.json();
        } catch (_) { return null; }
      }));
      const out = [];
      for (const hit of detailed) {
        if (!hit?.name) continue;
        // Walk every variant looking for a Cardmarket / TCGplayer price.
        // First non-null wins. EUR values from Cardmarket aren't currency-
        // converted, but they're a reasonable USD-ish proxy.
        const variants = hit.variants_detailed || [];
        let price = null;
        for (const v of variants) {
          const cm = v?.pricing?.cardmarket;
          const tp = v?.pricing?.tcgplayer;
          const candidate = num(cm?.avg) ?? num(cm?.trend) ?? num(cm?.low)
                         ?? num(tp?.market) ?? num(tp?.mid) ?? num(tp?.low);
          if (candidate != null) { price = candidate; break; }
        }
        // Top-level pricing (older TCGdex shape) as a final fallback.
        if (price == null) {
          const cm = hit.pricing?.cardmarket;
          price = num(cm?.avg) ?? num(cm?.trend) ?? num(cm?.low);
        }
        // Result language tag — JP for the JA db, CH for zh-tw/zh-cn, else EN.
        const isJA = db === 'ja';
        const isZH = db.startsWith('zh');
        // TCGdex images need a size+ext suffix; high.png is highest quality.
        const img = hit.image ? `${hit.image}/high.png` : null;
        const langTag = isJA ? 'japanese' : (isZH ? 'chinese' : 'english');
        out.push(normalizeCard({
          id: `tcgdex-${db}-${hit.id}`,
          name: hit.name,
          card_number: hit.localId || hit.id?.split('-').pop() || '',
          set_name: hit.set?.name || '',
          language: langTag,
          condition: 'NM',
          is_graded: false,
          variant: hit.rarity || null,
          hp: hit.hp || null,
          image_url: img,
          current_market_price: price,
          last_priced_at: cm?.updated || null,
          _set_release: hit.set?.releaseDate,
          _set_id:      hit.set?.id,
          _rarity:      hit.rarity,
          _source:      'tcgdex',
        }));
      }
      console.info(`[searchTCGdex/${lang}] ${out.length} hits for "${cleanName}"`);
      return out;
    },

    // Last-resort identity fallback — PriceCharting indexes some
    // Chinese-exclusive sets (e.g. "Pokemon Chinese CSV4C") that TCGdex has
    // registered as a set but never populated with card data. Goes through
    // our backend (PriceCharting's HTML pages have no CORS headers for
    // direct browser fetches, unlike TCGdex's API).
    async searchPriceCharting(query, { pageSize = 10 } = {}) {
      const q = String(query || '').trim();
      if (!q) return [];
      try {
        const data = await request(P.pricechartingSearch(q, pageSize));
        const list = unwrapList(data, 'results');
        const out = list.map(r => normalizeCard({
          id: r.id,
          name: r.name,
          card_number: r.card_number,
          set_name: r.set_name,
          language: r.language,
          condition: 'NM',
          is_graded: false,
          image_url: r.image_url,
          market_price: r.market_price,
          _source: 'pricecharting',
          _pricecharting_url: r.pricecharting_url,
        })).filter(Boolean);
        console.info(`[searchPriceCharting] ${out.length} hits for "${q}"`);
        return out;
      } catch (_) { return []; }
    },

    // Direct image lookup against the public Pokemon TCG API.
    //
    // STRICT name match. Previously this fell back to `candidates[0]` when no
    // exact match existed — that's how an "Ancient Mew" row ended up with the
    // "Ancient Technical Machine [Ice]" image (no Ancient Mew in the API →
    // stem search for "Ancient*" → first hit is ATM[Ice] → PATCHed onto the
    // row). For obscure cards (movie promos, regional exclusives) we now
    // return null rather than attaching wrong art.
    async lookupCardImage(card) {
      if (!card?.name) return null;
      const cleanName = String(card.name).replace(/"/g, '').trim();
      if (!cleanName) return null;
      const codeRaw = card.code ? String(card.code).replace(/[^\w/-]/g, '') : '';
      const codeStripped = codeRaw.replace(/^0+/, ''); // 024 → 24

      // Queries narrow → broad, but each requires a name match before we
      // accept it. The `name:firstWord*` query is intentionally OMITTED: it
      // was the prefix-stem fallback that returned unrelated cards (e.g.
      // "Ancient Mew" → "Ancient*" → ATM/Tomb/Ruins). It's the line that
      // turned a lookup miss into a confidently-wrong image.
      const queries = [];
      if (codeRaw) {
        queries.push(`name:"${cleanName}" number:"${codeRaw}"`);
        if (codeStripped && codeStripped !== codeRaw) {
          queries.push(`name:"${cleanName}" number:"${codeStripped}"`);
        }
      }
      queries.push(`name:"${cleanName}"`);
      // Stripped-suffix retry (Mewtwo ex → Mewtwo). Still requires the base
      // name to match exactly against the returned card's name.
      const baseName = cleanName.replace(/\s+(ex|EX|V|VMAX|GX|VSTAR|VUNION)$/i, '').trim();
      if (baseName && baseName.toLowerCase() !== cleanName.toLowerCase()) {
        queries.push(`name:"${baseName}"`);
      }

      const namesEqual = (a, b) => (a || '').trim().toLowerCase() === (b || '').trim().toLowerCase();

      let hit = null;
      for (const q of queries) {
        const url = `https://api.pokemontcg.io/v2/cards?q=${encodeURIComponent(q)}&pageSize=5`;
        try {
          const res = await fetch(url, { headers: { Accept: 'application/json' } });
          if (!res.ok) {
            console.warn('[lookupCardImage] non-ok', res.status, q);
            continue;
          }
          const data = await res.json();
          const candidates = data?.data || [];
          if (candidates.length === 0) continue;
          // STRICT: name must equal the search name (or its base form after
          // ex/V/VMAX strip). If nothing matches, advance to the next query
          // — do NOT attach an unrelated card's image.
          hit = candidates.find(c => namesEqual(c.name, cleanName))
             || candidates.find(c => namesEqual(c.name, baseName))
             || null;
          if (hit) break;
        } catch (e) {
          console.warn('[lookupCardImage] error', q, e.message);
        }
      }
      if (!hit) {
        console.info('[lookupCardImage] no match for', cleanName, codeRaw);
        return null;
      }
      const img = hit.images?.large || hit.images?.small || null;
      // Best-effort price: TCGplayer market price (USD) → Cardmarket trend (EUR-ish, treat as USD-ish proxy)
      const tcgp = hit.tcgplayer?.prices;
      const tcgPrice = num(tcgp?.normal?.market) ?? num(tcgp?.holofoil?.market)
                    ?? num(tcgp?.reverseHolofoil?.market) ?? num(tcgp?.['1stEditionNormal']?.market)
                    ?? num(tcgp?.unlimitedHolofoil?.market);
      const cmPrice  = num(hit.cardmarket?.prices?.averageSellPrice) ?? num(hit.cardmarket?.prices?.trendPrice);
      const price = tcgPrice ?? cmPrice ?? null;
      if (!img && price == null) return null;
      // Persist onto a real backend row if we have one.
      if (card.id && !/^tmp-/.test(String(card.id))) {
        try {
          const patch = {};
          if (img) patch.image_url = img;
          if (price != null) patch.current_market_price = price;
          if (Object.keys(patch).length > 0) {
            return await api.patchCard(card.id, patch);
          }
        } catch (_) { /* best-effort */ }
      }
      return normalizeCard({
        ...card.raw, ...card,
        image_url: img || card.image_url || null,
        current_market_price: price ?? card.usd ?? null,
      });
    },

    async proposeTrade({ user_id, target_value, tolerance = 5.0, max_combo_size = 5, max_results = 10, exclude_card_ids = [] }) {
      return request(P.tradePropose(), {
        method: 'POST',
        body: {
          user_id: user_id ?? state.currentUserId,
          target_value,
          tolerance,
          max_combo_size,
          max_results,
          exclude_card_ids,
        },
      });
    },

    async uploadCardPhoto(cardId, file) {
      const fd = new FormData();
      fd.append('photo', file);
      return request(P.cardPhoto(cardId), { method: 'POST', body: fd });
    },

    photoUrl(card) {
      if (!card) return null;
      if (card.image_url && /^https?:\/\//.test(card.image_url)) return card.image_url;
      if (card.image_url) return state.base + card.image_url;
      if (card.photo_path) return state.base + P.cardPhoto(card.id);
      return null;
    },

    async ping() { try { await request('/'); return true; } catch { return false; } },

    // No-op for compatibility with the previous bootstrap()-based flow.
    async bootstrap() { return true; },

    normalizeCard,
    isSealedProduct,
  };

  window.api = api;
})();
