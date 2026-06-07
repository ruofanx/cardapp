/* Mock card data + price series. No copyrighted names — original mons-themed placeholders. */

window.CARDS = [
  { id: 'c1', name: 'Voltflare', code: '063/197', set: 'Stellar Crown', lang: 'EN', condition: 'NM', usd: 248.40, change: 0.124, holo: true,  hue: 28,  glyph: 'flame' },
  { id: 'c2', name: 'Tidewing',  code: '011/162', set: 'Twilight Mask', lang: 'JP', condition: 'NM', usd: 86.10,  change: 0.038, holo: true,  hue: 220, glyph: 'wave'  },
  { id: 'c3', name: 'Mosspaw',   code: '004/197', set: 'Stellar Crown', lang: 'EN', condition: 'LP', usd: 12.50,  change: -0.041, holo: false, hue: 150, glyph: 'leaf' },
  { id: 'c4', name: 'Sparkfox ex', code: '142/197', set: 'Stellar Crown', lang: 'EN', condition: 'NM', usd: 91.20, change: 0.082, holo: true,  hue: 56,  glyph: 'bolt' },
  { id: 'c5', name: 'Cinderhog', code: '028/197', set: 'Stellar Crown', lang: 'EN', condition: 'NM', usd: 4.80,   change: 0.012, holo: false, hue: 18,  glyph: 'flame', bulk: true },
  { id: 'c6', name: 'Glacefin',  code: '054/162', set: 'Twilight Mask', lang: 'JP', condition: 'NM', usd: 38.60,  change: -0.018, holo: true, hue: 200, glyph: 'wave' },
  { id: 'c7', name: 'Thornclaw', code: '098/197', set: 'Stellar Crown', lang: 'EN', condition: 'MP', usd: 7.20,   change: 0.004, holo: false, hue: 140, glyph: 'leaf', bulk: true },
  { id: 'c8', name: 'Petalpup',  code: '015/197', set: 'Stellar Crown', lang: 'EN', condition: 'NM', usd: 22.10,  change: 0.024, holo: false, hue: 320, glyph: 'leaf' },
  { id: 'c9', name: 'Magmite',   code: '111/197', set: 'Stellar Crown', lang: 'EN', condition: 'NM', usd: 64.80,  change: 0.061, holo: true,  hue: 12,  glyph: 'flame' },
  { id: 'c10', name: 'Lumipom',  code: '023/162', set: 'Twilight Mask', lang: 'JP', condition: 'NM', usd: 14.30,  change: -0.011, holo: false, hue: 60,  glyph: 'spark' },
  { id: 'c11', name: 'Gloomshade', code: '076/197', set: 'Stellar Crown', lang: 'EN', condition: 'NM', usd: 31.40, change: 0.018, holo: true, hue: 280, glyph: 'spark' },
  { id: 'c12', name: 'Brawltail', code: '120/197', set: 'Stellar Crown', lang: 'EN', condition: 'LP', usd: 9.80,   change: -0.007, holo: false, hue: 30, glyph: 'bolt', bulk: true },
];

window.THEIR_CARDS = [
  { id: 't1', name: 'Lumipom Holo', code: '023/162', set: 'Twilight Mask', lang: 'JP', condition: 'NM', usd: 168.40, change: 0.094, holo: true, hue: 60, glyph: 'spark' },
  { id: 't2', name: 'Frosthorn',    code: '041/162', set: 'Twilight Mask', lang: 'JP', condition: 'NM', usd: 72.20,  change: 0.022, holo: true, hue: 210, glyph: 'wave' },
  { id: 't3', name: 'Emberkit',     code: '009/162', set: 'Twilight Mask', lang: 'JP', condition: 'NM', usd: 18.40,  change: 0.005, holo: false, hue: 22, glyph: 'flame' },
];

window.SOLD_LISTINGS = [
  { src: 'eBay',         lang: 'EN', cond: 'NM', usd: 252, date: '2d', shipping: 4 },
  { src: 'eBay',         lang: 'EN', cond: 'NM', usd: 244, date: '3d', shipping: 0 },
  { src: 'PriceCharting',lang: 'EN', cond: 'NM', usd: 256, date: '4d', shipping: 0 },
  { src: 'eBay',         lang: 'EN', cond: 'LP', usd: 198, date: '5d', shipping: 4 },
  { src: 'Cardmarket',   lang: 'EN', cond: 'NM', usd: 247, date: '6d', shipping: 0 },
  { src: 'Yahoo Japan',  lang: 'JP', cond: 'NM', usd: 222, date: '6d', shipping: 8 },
  { src: 'eBay',         lang: 'EN', cond: 'NM', usd: 261, date: '8d', shipping: 0 },
  { src: 'eBay',         lang: 'JP', cond: 'NM', usd: 215, date: '9d', shipping: 6 },
];

window.FX = window.FX || { USD: 1, JPY: 152, EUR: 0.92 };

window.PRICE_SERIES = {
  c1: (() => {
    const base = 220, out = []; let v = base;
    for (let i = 0; i < 90; i++) { v += (Math.sin(i * 0.31) * 4) + (Math.random() - 0.4) * 3.2; out.push(Math.max(180, v)); }
    out[out.length - 1] = 248.40;
    return out;
  })(),
};

window.PRICE_SERIES.portfolio = (() => {
  const out = []; let v = 8400;
  for (let i = 0; i < 90; i++) { v += (Math.sin(i * 0.18) * 60) + (Math.random() - 0.35) * 70; out.push(Math.max(7600, v)); }
  out[out.length - 1] = 9842;
  return out;
})();
