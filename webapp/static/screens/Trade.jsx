/* Trade proposer — drag cards into "you give" / "you get" pans, fairness shown live */

const { useState: useStateTrade } = React;

function TradeScreen({ tweaks, navigate, collection, params = {} }) {
  const cur = tweaks.currency === 'BOTH' ? 'USD' : tweaks.currency;
  const myPool = Array.isArray(collection) ? collection : [];
  const theirPool = window.THEIR_CARDS || [];

  // If we were navigated here from Detail with a specific card to trade,
  // seed the "give" pan with that card. Otherwise pick the first from pool.
  const [give, setGive] = useStateTrade(() => {
    if (params.giveCard) return [params.giveCard];
    return myPool.slice(0, 1);
  });
  const [get, setGet] = useStateTrade(() => theirPool.slice(0, 1));

  const giveTotal = give.reduce((s, c) => s + (c.usd || 0), 0);
  const getTotal = get.reduce((s, c) => s + (c.usd || 0), 0);
  const delta = getTotal - giveTotal;
  const pct = giveTotal === 0 ? 0 : (delta / giveTotal) * 100;
  const fair = Math.abs(pct) <= 8;
  const verdict = fair ? 'fair' : (pct > 0 ? 'favors you' : 'favors them');

  return (
    <div className="screen" style={{ animation: 'pushIn 0.25s ease-out' }}>
      <NavBar
        title="New trade"
        left={<NavBackButton onClick={() => navigate('home')} label="Home"/>}
        right={<button className="tap" style={{ fontSize: 14, color: fair ? 'var(--accent)' : 'var(--ink-3)', fontWeight: 600 }}>Send</button>}
      />

      {/* Counterparty header */}
      <div className="row gap-2" style={{ padding: '4px 16px 12px', alignItems: 'center' }}>
        <div style={{
          width: 36, height: 36, borderRadius: 18, flexShrink: 0,
          background: 'linear-gradient(135deg, oklch(0.7 0.12 60), oklch(0.55 0.14 30))',
        }}/>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 14, fontWeight: 500 }}>Trading with @kira_holo</div>
          <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>4.9★ · 218 trades · Tokyo</div>
        </div>
        <button className="tap" style={{ fontSize: 12, color: 'var(--ink-3)' }}>Profile</button>
      </div>

      {/* Fairness bar */}
      <div style={{ padding: '0 16px 16px' }}>
        <FairnessBar pct={pct} fair={fair} verdict={verdict}/>
      </div>

      <div className="screen-scroll">
        {/* You give */}
        <TradePan
          title="You give"
          subtitle={`${give.length} cards · ${fmtPrice(giveTotal, cur)}`}
          cards={give}
          renderMode={tweaks.cardRender}
          onRemove={i => setGive(g => g.filter((_, j) => j !== i))}
          onAdd={() => {
            const next = myPool.find(c => !give.includes(c));
            if (next) setGive([...give, next]);
          }}
        />

        {/* Direction */}
        <div className="row" style={{ justifyContent: 'center', padding: '4px 0 6px', gap: 10 }}>
          <div style={{ flex: 1, height: 1, background: 'var(--hairline-soft)' }}/>
          <div style={{
            width: 36, height: 36, borderRadius: 18,
            background: 'var(--bg-2)', border: '1px solid var(--hairline-soft)',
            display: 'grid', placeItems: 'center', color: 'var(--ink-3)',
          }}>
            <Icon name="swap" size={16}/>
          </div>
          <div style={{ flex: 1, height: 1, background: 'var(--hairline-soft)' }}/>
        </div>

        {/* You get */}
        <TradePan
          title="You get"
          subtitle={`${get.length} cards · ${fmtPrice(getTotal, cur)}`}
          cards={get}
          renderMode={tweaks.cardRender}
          onRemove={i => setGet(g => g.filter((_, j) => j !== i))}
          onAdd={() => {
            const next = theirPool.find(c => !get.includes(c));
            if (next) setGet([...get, next]);
          }}
          their
        />

        {/* Diagnostics */}
        <div style={{ padding: '20px 16px 100px' }}>
          <div style={{ fontSize: 11, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 10 }}>Trade analysis</div>
          <div style={{ background: 'var(--bg-1)', border: '1px solid var(--hairline-soft)', borderRadius: 14, padding: 14 }}>
            <DiagRow label="Mid-market delta" value={`${delta >= 0 ? '+' : ''}${fmtPrice(delta, cur)}`} mono accent={delta >= 0}/>
            <DiagRow label="As percent of give" value={`${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%`} mono accent={pct >= 0}/>
            <DiagRow label="Liquidity skew" value="−2.1 days" mono note="Their cards sell faster"/>
            <DiagRow label="Set diversity gain" value="+1 set" mono note="Adds Twilight Mask"/>
            <DiagRow label="Confidence" value="High" mono note="6 comps in 30 days"/>
          </div>
        </div>
      </div>
    </div>
  );
}

function FairnessBar({ pct, fair, verdict }) {
  const clamp = Math.max(-30, Math.min(30, pct));
  const pos = 50 + (clamp / 30) * 50;
  return (
    <div>
      <div className="row" style={{ justifyContent: 'space-between', marginBottom: 8 }}>
        <span style={{ fontSize: 11, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase' }}>Fairness</span>
        <span className="mono" style={{ fontSize: 11, color: fair ? 'var(--accent)' : 'var(--neg)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
          {verdict}
        </span>
      </div>
      <div style={{ position: 'relative', height: 8, background: 'var(--bg-2)', borderRadius: 4, overflow: 'hidden' }}>
        <div style={{
          position: 'absolute', left: '50%', top: 0, bottom: 0, width: 1,
          background: 'oklch(1 0 0 / 0.18)',
        }}/>
        {/* fair zone */}
        <div style={{
          position: 'absolute', left: `${50 - (8/30)*50}%`, right: `${50 - (8/30)*50}%`,
          top: 0, bottom: 0, background: 'oklch(0.78 0.14 165 / 0.16)',
        }}/>
        {/* marker */}
        <div style={{
          position: 'absolute', left: `${pos}%`, top: -4, bottom: -4, width: 3,
          background: fair ? 'var(--accent)' : 'var(--neg)',
          transform: 'translateX(-50%)',
          borderRadius: 2,
          transition: 'left 0.25s ease, background 0.2s',
        }}/>
      </div>
      <div className="row" style={{ justifyContent: 'space-between', marginTop: 6, fontSize: 10, color: 'var(--ink-3)' }} className="mono">
        <span>−30%</span><span>fair ±8%</span><span>+30%</span>
      </div>
    </div>
  );
}

function TradePan({ title, subtitle, cards, renderMode, onRemove, onAdd, their }) {
  return (
    <div style={{ padding: '0 16px 4px' }}>
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8 }}>
        <span style={{ fontSize: 13, fontWeight: 600, letterSpacing: '-0.01em' }}>{title}</span>
        <span className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>{subtitle}</span>
      </div>
      <div style={{
        background: their ? 'var(--bg-1)' : 'var(--bg-1)',
        border: '1px dashed var(--hairline)',
        borderRadius: 14, padding: 12, minHeight: 130,
      }}>
        {cards.length === 0 ? (
          <div style={{ display: 'grid', placeItems: 'center', height: 100, color: 'var(--ink-3)', fontSize: 12 }}>
            Empty
          </div>
        ) : (
          <div className="row" style={{ gap: 8, overflowX: 'auto', scrollbarWidth: 'none', paddingBottom: 4 }}>
            {cards.map((c, i) => (
              <div key={i} style={{ flexShrink: 0, position: 'relative' }}>
                <CardArt card={c} renderMode={renderMode} size="sm"/>
                <button className="tap" onClick={() => onRemove(i)} style={{
                  position: 'absolute', top: -6, right: -6,
                  width: 22, height: 22, borderRadius: 11,
                  background: 'oklch(0.16 0.01 250)', border: '1px solid var(--hairline)',
                  color: 'var(--ink-2)', display: 'grid', placeItems: 'center',
                }}>
                  <Icon name="x" size={12}/>
                </button>
                <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 4, textAlign: 'center' }}>
                  ${c.usd.toFixed(0)}
                </div>
              </div>
            ))}
            <button className="tap" onClick={onAdd} style={{
              flexShrink: 0,
              width: 76, height: 106, borderRadius: 8,
              border: '1px dashed var(--hairline)', color: 'var(--ink-3)',
              display: 'grid', placeItems: 'center', fontSize: 11, gap: 4,
              background: 'transparent',
            }}>
              <Icon name="plus" size={18}/>
              Add
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function DiagRow({ label, value, mono, accent, note }) {
  return (
    <div className="row" style={{
      padding: '8px 0', borderTop: '1px solid var(--hairline-soft)',
      alignItems: 'baseline', justifyContent: 'space-between', gap: 12,
    }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, color: 'var(--ink-2)' }}>{label}</div>
        {note && <div style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 2 }}>{note}</div>}
      </div>
      <div className={mono ? 'mono' : ''} style={{ fontSize: 13, fontWeight: 600, color: accent ? 'var(--accent)' : 'var(--ink)' }}>{value}</div>
    </div>
  );
}

window.TradeScreen = TradeScreen;
