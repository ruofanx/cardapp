// api.js — backend client for the CardApp FastAPI server.
// Auth token is set by app.jsx via setAuthToken() when Supabase session changes.

let _authToken = null
export function setAuthToken(token) { _authToken = token }
export function getAuthToken() { return _authToken }

function _authHeader() {
  return _authToken ? { 'Authorization': `Bearer ${_authToken}` } : {}
}

function defaultBase() {
  if (typeof window !== 'undefined' && window.POKECOLLECT_API) return window.POKECOLLECT_API
  // In Capacitor native shell window.location is capacitor://localhost — use env URL instead.
  if (typeof window !== 'undefined' && window.Capacitor?.isNativePlatform?.()) {
    return import.meta.env.VITE_API_URL || 'https://pokecollect.up.railway.app'
  }
  if (typeof window !== 'undefined' && window.location?.hostname) {
    const port = window.location.port
    if (!port || port === '80' || port === '443') {
      return `${window.location.protocol}//${window.location.hostname}`
    }
    return `${window.location.protocol}//${window.location.hostname}:8000`
  }
  return 'http://localhost:8000'
}

export const state = {
  base: defaultBase(),
  online: null,
  lastError: null,
  currentUserId: null,
}

const P = {
  users:               () => `/api/users`,
  userPortfolio:       (uid) => `/api/users/${uid}/portfolio`,
  userCards:           (uid) => `/api/users/${uid}/cards`,
  cardsSearch:         (q) => `/api/cards/search?q=${encodeURIComponent(q)}`,
  pricechartingSearch: (q, limit) => `/api/pricecharting/search?q=${encodeURIComponent(q)}&limit=${limit}`,
  card:                (cid) => `/api/cards/${cid}`,
  cardPhoto:           (cid) => `/api/cards/${cid}/photo`,
  cardPriceHistory:    (cid) => `/api/cards/${cid}/price-history`,
  identify:            () => `/api/identify`,
  refreshPrice:        () => `/api/refresh-price`,
  refreshAll:          () => `/api/refresh-prices/run-now`,
  health:              () => `/api/health`,
  soldListings:        () => `/api/sold-listings`,
  tradePropose:        () => `/api/trade/propose`,
}

async function request(path, opts = {}) {
  const url = `${state.base}${path}`
  const init = {
    method: opts.method || 'GET',
    headers: { 'Accept': 'application/json', ..._authHeader(), ...(opts.headers || {}) },
    ...opts,
  }
  if (opts.body && !(opts.body instanceof FormData) && typeof opts.body === 'object') {
    init.headers['Content-Type'] = 'application/json'
    init.body = JSON.stringify(opts.body)
  } else if (opts.body) {
    init.body = opts.body
  }
  try {
    const res = await fetch(url, init)
    if (!res.ok) {
      const text = await res.text().catch(() => '')
      const err = new Error(`${res.status} ${res.statusText}${text ? ' — ' + text.slice(0, 200) : ''}`)
      err.status = res.status
      err.networkError = false
      state.online = true
      state.lastError = null
      throw err
    }
    state.online = true
    state.lastError = null
    const ct = res.headers.get('content-type') || ''
    return ct.includes('application/json') ? res.json() : res.text()
  } catch (e) {
    if (e.networkError === false || e.status) throw e
    e.networkError = true
    state.online = false
    state.lastError = String(e.message || e)
    throw e
  }
}

function normalizeLang(v) {
  const s = String(v ?? 'EN').toLowerCase()
  if (s.startsWith('jp') || s.startsWith('ja')) return 'JP'
  if (s.startsWith('ch') || s.startsWith('zh')) return 'CH'
  return 'EN'
}
function denormalizeLang(v) {
  const u = String(v).toUpperCase()
  if (u === 'JP') return 'japanese'
  if (u === 'CH') return 'chinese'
  return 'english'
}
export function normalizeCard(c) {
  if (!c || typeof c !== 'object') return null
  const usd = num(c.current_market_price) ?? num(c.market_value_usd)
           ?? num(c.estimated_price) ?? num(c.market_price)
           ?? num(c.usd) ?? num(c.price)
           ?? num(c.value) ?? num(c.market_value) ?? num(c.fair_value)
           ?? null
  const isGraded = Boolean(c.is_graded ?? (c.grade_company && c.grade))
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
    image_url: (() => {
      const raw = c.image_url ?? c.image ?? null
      const lang = normalizeLang(c.language ?? c.lang)
      if (raw && (lang === 'JP' || lang === 'CH')) {
        if (raw.includes('images.pokemontcg.io')) return null
        if (raw.includes('assets.tcgdex.net/en/') && !raw.includes('/en/sv/')) return null
      }
      return raw
    })(),
    product_type: c.product_type || 'card',
    photo_path: c.photo_path ?? null,
    last_priced_at: c.last_priced_at ?? c.last_refreshed ?? null,
    notes:     c.notes ?? null,
    tags:      c.tags ?? [],
    raw:       c,
  }
}
export function isSealedProduct(card) {
  return card.product_type != null && card.product_type !== 'card'
}
const num = (v) => (v == null || v === '' || isNaN(Number(v))) ? null : Number(v)
const cryptoId = () => 'c' + Math.random().toString(36).slice(2, 9)
const hashHue = (s) => {
  let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0
  return Math.abs(h) % 360
}

const VARIANT_KEY_LABELS = {
  '1stEditionHolofoil': '1st Edition Holo',
  'unlimitedHolofoil':  'Unlimited Holo',
  '1stEditionNormal':   '1st Edition',
  '1stEdition':         '1st Edition',
  'unlimited':          'Unlimited',
  'reverseHolofoil':    'Reverse Holo',
  'holofoil':           'Holo',
  'normal':             'Normal',
}
const VARIANT_DISPLAY_ORDER = [
  '1st Edition Holo', 'Unlimited Holo',
  '1st Edition', 'Unlimited',
  'Holo', 'Normal', 'Reverse Holo',
]
function explodeVariants(hit, cmFallback) {
  const prices = hit.tcgplayer?.prices || {}
  const seen = new Set()
  const unique = []
  for (const [key, label] of Object.entries(VARIANT_KEY_LABELS)) {
    const market = num(prices[key]?.market)
    if (market == null || seen.has(label)) continue
    seen.add(label)
    unique.push([label, market])
  }
  if (unique.length >= 2) {
    const order = Object.fromEntries(VARIANT_DISPLAY_ORDER.map((l, i) => [l, i]))
    unique.sort((a, b) => (order[a[0]] ?? 99) - (order[b[0]] ?? 99))
    return unique
  }
  if (unique.length === 1) return [[null, unique[0][1]]]
  return [[null, cmFallback ?? null]]
}

function unwrapList(data, key) {
  if (Array.isArray(data)) return data
  if (data && Array.isArray(data[key])) return data[key]
  if (data && Array.isArray(data.items)) return data.items
  if (data && Array.isArray(data.results)) return data.results
  return []
}

export const api = {
  state,
  setBase(url) { state.base = url },

  async listUsers() {
    try {
      const data = await request(P.users())
      const users = unwrapList(data, 'users')
      return users.map(u => ({ id: u.id ?? u.user_id, name: u.name ?? u.display_name ?? `User ${u.id}` }))
    } catch { return [{ id: 1, name: 'Demo' }] }
  },

  async portfolio(userId) {
    try { return await request(P.userPortfolio(userId)) }
    catch { return null }
  },

  async listCards(userId) {
    const id = userId ?? state.currentUserId
    if (id == null) return []
    const data = await request(P.userCards(id))
    const list = unwrapList(data, 'cards')
    return list.map(normalizeCard).filter(Boolean)
  },

  async addCard(payload) {
    const uid = payload.user_id ?? state.currentUserId
    if (uid == null) throw new Error('No current user')
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
    }
    let data
    try {
      data = await request(P.userCards(uid), { method: 'POST', body })
    } catch (e) {
      const legacy = { ...body }
      delete legacy.image_url; delete legacy.current_market_price
      delete legacy.purchase_price; delete legacy.tags
      data = await request(P.userCards(uid), { method: 'POST', body: legacy })
    }
    let saved = normalizeCard(data)
    const patch = {}
    if (!saved.image_url && body.image_url) patch.image_url = body.image_url
    if (!saved.usd && body.current_market_price != null) patch.current_market_price = body.current_market_price
    if (body.is_graded && !saved.is_graded) {
      patch.is_graded = true
      if (body.grade != null) patch.grade = body.grade
      if (body.grade_company) patch.grade_company = body.grade_company
    }
    if (body.purchase_price != null && saved.purchase_price == null) patch.purchase_price = body.purchase_price
    if (body.tags && body.tags.length && (!saved.tags || saved.tags.length === 0)) patch.tags = body.tags
    if (Object.keys(patch).length > 0 && saved.id) {
      try { saved = await api.patchCard(saved.id, patch) } catch (_) {}
    }
    return saved
  },

  async patchCard(cardId, fields) {
    const data = await request(P.card(cardId), { method: 'PATCH', body: fields })
    return normalizeCard(data)
  },

  async deleteCard(cardId) {
    await request(P.card(cardId), { method: 'DELETE' })
    return true
  },

  async searchCards(query) {
    if (!query) return []
    try {
      const data = await request(P.cardsSearch(query))
      const list = unwrapList(data, 'cards')
      return list.map(normalizeCard).filter(Boolean)
    } catch { return [] }
  },

  async identifyPhoto(file, productTypeHint) {
    const fd = new FormData()
    fd.append('photo', file)
    if (productTypeHint && productTypeHint !== 'auto') fd.append('product_type_hint', productTypeHint)
    const res = await request(P.identify(), { method: 'POST', body: fd })
    if (res?.candidates?.length > 0) {
      const identity = res.identity || {}
      return res.candidates.map(c => normalizeCard({ ...identity, ...c })).filter(Boolean)
    }
    const flat = { ...(res?.identity || {}), ...res }
    const one = normalizeCard(flat)
    return one ? [one] : []
  },

  async identifyTextSealed(query) {
    const res = await request(P.identify(), { method: 'POST', body: { query, product_type_hint: 'sealed' } })
    const flat = { ...(res?.identity || {}), ...res }
    const one = normalizeCard(flat)
    return one ? [one] : []
  },

  async identify({ query, image, productTypeHint }) {
    if (image) return this.identifyPhoto(image, productTypeHint)
    if (query) {
      if (productTypeHint === 'sealed') return this.identifyTextSealed(query)
      return this.searchCards(query)
    }
    return []
  },

  async quotePrice(card) {
    if (!card?.name) return null
    const body = {
      name: card.name, set_name: card.set ?? null, card_number: card.code ?? null,
      language: denormalizeLang(card.lang), condition: card.condition ?? 'NM',
      variant: card.variant ?? null, is_graded: Boolean(card.is_graded ?? card.grade),
      grade_company: card.grader ?? null, grade: card.grade ?? null,
      product_type: card.product_type || 'card',
    }
    const res = await request(P.refreshPrice(), { method: 'POST', body })
    return { estimated_price: num(res?.estimated_price), source: res?.source ?? null, image_url: res?.image_url ?? null, note: res?.note ?? null, raw: res }
  },

  async refreshPrice(card) {
    const body = {
      name: card.name, set_name: card.set ?? null, card_number: card.code ?? null,
      language: denormalizeLang(card.lang), condition: card.condition ?? 'NM',
      variant: card.variant ?? null, is_graded: Boolean(card.is_graded ?? card.grade),
      grade_company: card.grader ?? null, grade: card.grade ?? null,
      product_type: card.product_type || 'card',
    }
    const res = await request(P.refreshPrice(), { method: 'POST', body })
    const newPrice = num(res?.estimated_price)
    const newImage = res?.image_url || null
    if (newPrice != null && card.id && !/^tmp-/.test(String(card.id))) {
      try {
        const patch = { current_market_price: newPrice }
        if (newImage && !card.image_url) patch.image_url = newImage
        const patched = await api.patchCard(card.id, patch)
        return { ...patched, _quote: res }
      } catch (_) {}
    }
    return normalizeCard({ ...card.raw, ...card, current_market_price: newPrice, image_url: newImage || card.image_url, _quote: res })
  },

  async refreshAllPrices() { return request(P.refreshAll(), { method: 'POST' }) },
  async getHealth() { return request(P.health()) },

  async getPriceHistory(cardId, opts = {}) {
    if (!cardId) return null
    const qs = new URLSearchParams()
    if (opts.since) qs.set('since', opts.since)
    if (opts.limit) qs.set('limit', String(opts.limit))
    const suffix = qs.toString() ? `?${qs}` : ''
    try {
      const res = await request(P.cardPriceHistory(cardId) + suffix)
      const points = Array.isArray(res?.points)
        ? res.points.map(p => ({ at: p.at, price: Number(p.price), source: p.source || null, source_url: p.source_url || null })).filter(p => Number.isFinite(p.price))
        : []
      return { points, current: num(res?.current), currency: res?.currency || 'USD' }
    } catch (e) {
      if (e.status === 404) return { points: [], current: null, currency: 'USD' }
      throw e
    }
  },

  async getSoldListings(card, opts = {}) {
    if (!card?.name) return null
    const body = {
      name: card.name, set_name: card.set ?? null, card_number: card.code ?? null,
      language: denormalizeLang(opts.lang ?? card.lang), variant: card.variant ?? null,
      condition: opts.condition || card.condition || 'NM',
      is_graded: Boolean(opts.is_graded ?? card.is_graded),
      grade_company: opts.grader ?? card.grader ?? null, grade: opts.grade ?? card.grade ?? null,
      period_days: opts.period_days ?? 60, max_listings: opts.max_listings ?? 25,
    }
    try { return await request(P.soldListings(), { method: 'POST', body }) }
    catch (e) {
      if (e.status === 502 || e.networkError) return null
      throw e
    }
  },

  async searchPokemonTCG(filters = {}, { pageSize = 20, page = 1 } = {}) {
    const qs = (v) => String(v).replace(/"/g, '')
    const buildParts = (nameTerm) => {
      const p = []
      if (nameTerm) p.push(nameTerm)
      if (filters.set) p.push(`set.name:"${qs(filters.set)}"`)
      if (filters.rarity) p.push(`rarity:"${qs(filters.rarity)}"`)
      if (filters.supertype) p.push(`supertype:"${qs(filters.supertype)}"`)
      if (filters.number) p.push(`number:"${String(filters.number).replace(/[^\w/-]/g, '')}"`)
      return p
    }
    const queryStrings = []
    if (filters.name) {
      const clean = qs(filters.name).trim()
      queryStrings.push(buildParts(`name:"${clean}"`).join(' '))
      const base = clean.replace(/\s+(ex|EX|V|VMAX|GX|VSTAR|VUNION)$/i, '').trim()
      if (base && base.toLowerCase() !== clean.toLowerCase()) {
        queryStrings.push(buildParts(`name:"${base}"`).join(' '))
      }
      const firstWord = (base || clean).split(/\s+/)[0]
      if (firstWord && firstWord.length >= 3) {
        queryStrings.push(buildParts(`name:${firstWord}*`).join(' '))
      }
    } else {
      const merged = buildParts(null).join(' ')
      if (merged) queryStrings.push(merged)
    }
    if (queryStrings.length === 0) return []

    for (const qstr of queryStrings) {
      const params = new URLSearchParams({ q: qstr, pageSize: String(pageSize), page: String(page), orderBy: '-set.releaseDate,number' })
      const url = `https://api.pokemontcg.io/v2/cards?${params}`
      let hits = []
      try {
        const res = await fetch(url, { headers: { Accept: 'application/json' } })
        if (!res.ok) continue
        hits = (await res.json())?.data || []
      } catch (_) { continue }
      if (hits.length === 0) continue
      return hits.flatMap(hit => {
        const cmPrice = num(hit.cardmarket?.prices?.averageSellPrice) ?? num(hit.cardmarket?.prices?.trendPrice)
        return explodeVariants(hit, cmPrice).map(([variantLabel, price]) => normalizeCard({
          id: variantLabel ? `${hit.id}::${variantLabel}` : hit.id,
          name: hit.name, card_number: hit.number, set_name: hit.set?.name,
          language: 'english', condition: 'NM', is_graded: false,
          variant: variantLabel || hit.rarity, hp: hit.hp,
          image_url: hit.images?.large || hit.images?.small || null,
          current_market_price: price ?? cmPrice ?? null,
          last_priced_at: hit.tcgplayer?.updatedAt || hit.cardmarket?.updatedAt || null,
          _set_release: hit.set?.releaseDate, _set_id: hit.set?.id, _rarity: hit.rarity,
        }))
      })
    }
    return []
  },

  buildEbayUrl(card, opts = {}) {
    if (!card) return null
    const tokens = []
    if (card.name) tokens.push(card.name)
    if (card.code) tokens.push(String(card.code).replace(/^0+/, ''))
    if (card.set)  tokens.push(card.set)
    if (card.lang === 'JP') tokens.push('Japanese')
    else if (card.lang === 'CH') tokens.push('Chinese')
    if (opts.grader && opts.grader !== 'Raw') {
      tokens.push(opts.grader)
      if (opts.grade) tokens.push(String(opts.grade))
    }
    const q = tokens.filter(Boolean).join(' ')
    return `https://www.ebay.com/sch/i.html?${new URLSearchParams({ _nkw: q, LH_Sold: '1', LH_Complete: '1', _sop: '13' })}`
  },

  async lookupPokemonNames(rawName) {
    if (!rawName) return null
    const base = String(rawName).replace(/\s+(ex|EX|V|VMAX|GX|VSTAR|VUNION)$/i, '').trim().toLowerCase().replace(/[^a-z0-9 -]/g, '').replace(/\s+/g, '-')
    if (!base) return null
    const cache = (state._nameCache = state._nameCache || {})
    if (cache[base]) return cache[base]
    try {
      const res = await fetch(`https://pokeapi.co/api/v2/pokemon-species/${base}`, { headers: { Accept: 'application/json' } })
      if (!res.ok) { cache[base] = null; return null }
      const data = await res.json()
      const pick = (codes) => { for (const code of codes) { const hit = (data.names || []).find(n => n.language?.name === code); if (hit?.name) return hit.name } return null }
      const out = { en: pick(['en']) || rawName, ja: pick(['ja-hrkt', 'ja']), zh: pick(['zh-hant', 'zh-hans']) }
      cache[base] = out; return out
    } catch (_) { cache[base] = null; return null }
  },

  async searchTCGdex(filters = {}, { pageSize = 20, lang = 'en', dbLang } = {}) {
    if (!filters.name) return []
    const cleanName = String(filters.name).replace(/"/g, '').trim().toLowerCase()
    if (!cleanName) return []
    const db = dbLang || lang
    const base = `https://api.tcgdex.net/v2/${db}/cards`
    const tries = [cleanName]
    const stripped = cleanName.replace(/\s+(ex|EX|V|VMAX|GX|VSTAR|VUNION)$/i, '').trim()
    if (stripped && stripped.toLowerCase() !== cleanName.toLowerCase()) tries.push(stripped)

    let list = []
    for (const t of tries) {
      try {
        const res = await fetch(`${base}?name=${encodeURIComponent(t)}`, { headers: { Accept: 'application/json' } })
        if (!res.ok) continue
        const data = await res.json()
        if (Array.isArray(data) && data.length > 0) { list = data; break }
      } catch (_) { continue }
    }
    if (list.length === 0) return []
    const detailed = await Promise.all(list.slice(0, pageSize).map(async (thin) => {
      try {
        const res = await fetch(`${base}/${thin.id}`, { headers: { Accept: 'application/json' } })
        return res.ok ? await res.json() : null
      } catch (_) { return null }
    }))
    const out = []
    for (const hit of detailed) {
      if (!hit?.name) continue
      const variants = hit.variants_detailed || []
      let price = null
      for (const v of variants) {
        const cm = v?.pricing?.cardmarket, tp = v?.pricing?.tcgplayer
        const candidate = num(cm?.avg) ?? num(cm?.trend) ?? num(cm?.low) ?? num(tp?.market) ?? num(tp?.mid) ?? num(tp?.low)
        if (candidate != null) { price = candidate; break }
      }
      if (price == null) { const cm = hit.pricing?.cardmarket; price = num(cm?.avg) ?? num(cm?.trend) ?? num(cm?.low) }
      const isJA = db === 'ja', isZH = db.startsWith('zh')
      const img = hit.image ? `${hit.image}/high.png` : null
      const langTag = isJA ? 'japanese' : (isZH ? 'chinese' : 'english')
      const hasFirstEd = isJA && hit.variants?.firstEdition === true
      const rarityLabel = hit.rarity || null
      const variants_to_add = hasFirstEd
        ? [['1st Edition' + (rarityLabel ? ` ${rarityLabel}` : ''), price], [rarityLabel ? `${rarityLabel} (Unlimited)` : 'Unlimited', price]]
        : [[rarityLabel, price]]
      for (const [variantLabel, variantPrice] of variants_to_add) {
        const varSuffix = hasFirstEd ? (variantLabel.startsWith('1st') ? '-1st' : '-unl') : ''
        out.push(normalizeCard({
          id: `tcgdex-${db}-${hit.id}${varSuffix}`, name: hit.name,
          card_number: hit.localId || hit.id?.split('-').pop() || '', set_name: hit.set?.name || '',
          language: langTag, condition: 'NM', is_graded: false, variant: variantLabel, hp: hit.hp || null,
          image_url: img, current_market_price: variantPrice, last_priced_at: null,
          _set_release: hit.set?.releaseDate, _set_id: hit.set?.id, _rarity: hit.rarity, _source: 'tcgdex',
        }))
      }
    }
    return out
  },

  async searchPriceCharting(query, { pageSize = 10 } = {}) {
    const q = String(query || '').trim()
    if (!q) return []
    try {
      const data = await request(P.pricechartingSearch(q, pageSize))
      return unwrapList(data, 'results').map(r => normalizeCard({
        id: r.id, name: r.name, card_number: r.card_number, set_name: r.set_name,
        language: r.language, condition: 'NM', is_graded: false, image_url: r.image_url,
        market_price: r.market_price, _source: 'pricecharting', _pricecharting_url: r.pricecharting_url,
      })).filter(Boolean)
    } catch (_) { return [] }
  },

  async lookupCardImage(card) {
    if (!card?.name) return null
    const cleanName = String(card.name).replace(/"/g, '').trim()
    if (!cleanName) return null
    const codeRaw = card.code ? String(card.code).replace(/[^\w/-]/g, '') : ''
    const codeStripped = codeRaw.replace(/^0+/, '')
    const queries = []
    if (codeRaw) {
      queries.push(`name:"${cleanName}" number:"${codeRaw}"`)
      if (codeStripped && codeStripped !== codeRaw) queries.push(`name:"${cleanName}" number:"${codeStripped}"`)
    }
    queries.push(`name:"${cleanName}"`)
    const baseName = cleanName.replace(/\s+(ex|EX|V|VMAX|GX|VSTAR|VUNION)$/i, '').trim()
    if (baseName && baseName.toLowerCase() !== cleanName.toLowerCase()) queries.push(`name:"${baseName}"`)
    const namesEqual = (a, b) => (a || '').trim().toLowerCase() === (b || '').trim().toLowerCase()
    let hit = null
    for (const q of queries) {
      const url = `https://api.pokemontcg.io/v2/cards?q=${encodeURIComponent(q)}&pageSize=5`
      try {
        const res = await fetch(url, { headers: { Accept: 'application/json' } })
        if (!res.ok) continue
        const candidates = (await res.json())?.data || []
        if (candidates.length === 0) continue
        hit = candidates.find(c => namesEqual(c.name, cleanName)) || candidates.find(c => namesEqual(c.name, baseName)) || null
        if (hit) break
      } catch (_) {}
    }
    if (!hit) return null
    const img = hit.images?.large || hit.images?.small || null
    const tcgp = hit.tcgplayer?.prices
    const tcgPrice = num(tcgp?.normal?.market) ?? num(tcgp?.holofoil?.market) ?? num(tcgp?.reverseHolofoil?.market) ?? num(tcgp?.['1stEditionNormal']?.market) ?? num(tcgp?.unlimitedHolofoil?.market)
    const cmPrice = num(hit.cardmarket?.prices?.averageSellPrice) ?? num(hit.cardmarket?.prices?.trendPrice)
    const price = tcgPrice ?? cmPrice ?? null
    if (!img && price == null) return null
    if (card.id && !/^tmp-/.test(String(card.id))) {
      try {
        const patch = {}
        if (img) patch.image_url = img
        if (price != null) patch.current_market_price = price
        if (Object.keys(patch).length > 0) return await api.patchCard(card.id, patch)
      } catch (_) {}
    }
    return normalizeCard({ ...card.raw, ...card, image_url: img || card.image_url || null, current_market_price: price ?? card.usd ?? null })
  },

  async proposeTrade({ user_id, target_value, tolerance = 5.0, max_combo_size = 5, max_results = 10, exclude_card_ids = [] }) {
    return request(P.tradePropose(), { method: 'POST', body: { user_id: user_id ?? state.currentUserId, target_value, tolerance, max_combo_size, max_results, exclude_card_ids } })
  },

  async uploadCardPhoto(cardId, file) {
    const fd = new FormData()
    fd.append('photo', file)
    return request(P.cardPhoto(cardId), { method: 'POST', body: fd })
  },

  photoUrl(card) {
    if (!card) return null
    if (card.image_url && /^https?:\/\//.test(card.image_url)) return card.image_url
    if (card.image_url) return state.base + card.image_url
    if (card.photo_path) return state.base + P.cardPhoto(card.id)
    return null
  },

  async ping() { try { await request('/'); return true } catch { return false } },
  async bootstrap() { return true },

  normalizeCard,
  isSealedProduct,
}

export default api
