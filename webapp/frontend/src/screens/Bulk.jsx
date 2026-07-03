/* Bulk scan cart — review queue */

import React, { useState } from 'react'
import { CardArt, Icon, Price, NavBar, NavBackButton } from '../components.jsx'

function BulkScreen({ tweaks, navigate, scanQueue, setScanQueue, addToCollection }) {
  const cur = tweaks.currency;
  const total = scanQueue.reduce((s, c) => s + (c.usd || 0), 0);
  const bulkCount = scanQueue.filter(c => c.bulk).length;
  const [committing, setCommitting] = useState(false);

  const setCondition = (i, cond) =>
    setScanQueue(q => q.map((c, j) => j === i ? { ...c, condition: cond } : c));

  const removeAt = (i) => setScanQueue(q => q.filter((_, j) => j !== i));
  const commitAll = async () => {
    if (committing) return;
    setCommitting(true);
    try {
      // Sequential so each backend write completes (and any error surfaces)
      // before the next; keeps the optimistic order in the collection list.
      for (const c of scanQueue) {
        try { await addToCollection(c); } catch (e) { /* banner shows error */ }
      }
      setScanQueue([]);
      navigate('browse');
    } finally {
      setCommitting(false);
    }
  };

  return (
    <div className="screen" style={{ animation: 'pushIn 0.25s ease-out' }}>
      <NavBar
        title="Scan cart"
        left={<NavBackButton onClick={() => navigate('scan')} label="Scan"/>}
        right={scanQueue.length > 0 ? <button className="tap" onClick={() => setScanQueue([])} style={{ color: 'var(--neg)', fontSize: 14 }}>Clear</button> : null}
      />

      {scanQueue.length === 0 ? (
        <EmptyCart navigate={navigate}/>
      ) : (
        <>
          <div className="screen-scroll">
            {/* Summary */}
            <div style={{ padding: '12px 16px 16px' }}>
              <div className="row gap-2">
                <div style={{ flex: 1, padding: 12, background: 'var(--bg-1)', borderRadius: 12, border: '1px solid var(--hairline-soft)' }}>
                  <div style={{ fontSize: 10, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase' }}>Cards</div>
                  <div className="mono" style={{ fontSize: 22, fontWeight: 600, marginTop: 2 }}>{scanQueue.length}</div>
                </div>
                <div style={{ flex: 1, padding: 12, background: 'var(--bg-1)', borderRadius: 12, border: '1px solid var(--hairline-soft)' }}>
                  <div style={{ fontSize: 10, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase' }}>Value</div>
                  <Price usd={total} currency={cur === 'BOTH' ? 'USD' : cur} size="lg" decimals={0}/>
                </div>
              </div>
              {bulkCount > 0 && (
                <div className="row gap-2" style={{ marginTop: 10, padding: '8px 12px', background: 'var(--bg-1)', borderRadius: 10, fontSize: 12, color: 'var(--ink-3)' }}>
                  <Icon name="info" size={14} stroke={1.8}/>
                  <span>{bulkCount} bulk · auto-grouped, hidden from active tracking</span>
                </div>
              )}
            </div>

            {/* List */}
            <div style={{ fontSize: 11, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', padding: '0 16px 8px' }}>Identified · {scanQueue.length}</div>

            <div className="col" style={{ padding: '0 16px 100px' }}>
              {scanQueue.map((c, i) => (
                <div key={i} style={{
                  display: 'grid', gridTemplateColumns: '54px 1fr auto', gap: 12, alignItems: 'center',
                  padding: '12px 0',
                  borderTop: i === 0 ? 'none' : '1px solid var(--hairline-soft)',
                }}>
                  <CardArt card={c} renderMode={tweaks.cardRender} size="sm"/>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 14, fontWeight: 500 }}>{c.name}</div>
                    <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>{c.code} · {c.set}</div>
                    <div className="row gap-1" style={{ marginTop: 4 }}>
                      {['NM', 'LP', 'MP', 'HP', 'DMG'].map(g => (
                        <button key={g} className="tap" onClick={() => setCondition(i, g)} style={{
                          padding: '2px 8px', borderRadius: 6, fontSize: 10, fontWeight: 600,
                          background: g === (c.condition || 'NM') ? 'var(--bg-3)' : 'transparent',
                          color: g === (c.condition || 'NM') ? 'var(--ink)' : 'var(--ink-3)',
                          border: '1px solid var(--hairline-soft)',
                        }}>{g}</button>
                      ))}
                    </div>
                  </div>
                  <div style={{ textAlign: 'right', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
                    <Price usd={c.usd} currency={cur === 'BOTH' ? 'USD' : cur} size="sm"/>
                    <button className="tap" onClick={() => removeAt(i)} style={{ color: 'var(--ink-3)' }}>
                      <Icon name="trash" size={16}/>
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Commit bar */}
          <div style={{
            flexShrink: 0,
            padding: '10px 16px calc(env(safe-area-inset-bottom, 0px) + 12px)',
            background: 'oklch(0.16 0.01 250 / 0.85)',
            backdropFilter: 'blur(20px)',
            borderTop: '1px solid var(--hairline-soft)',
            display: 'flex', gap: 8, alignItems: 'center',
          }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 11, color: 'var(--ink-3)' }}>Total · {scanQueue.length} cards</div>
              <Price usd={total} currency={cur} size="md"/>
            </div>
            <button className="tap" onClick={() => navigate('scan')} style={{
              padding: '14px 18px', borderRadius: 14, background: 'var(--bg-2)', color: 'var(--ink)', fontWeight: 500, fontSize: 14,
            }}>Scan more</button>
            <button className="tap" onClick={commitAll} disabled={committing} style={{
              padding: '14px 18px', borderRadius: 14, background: 'var(--accent)', color: 'var(--accent-ink)', fontWeight: 600, fontSize: 14,
              opacity: committing ? 0.6 : 1,
            }}>{committing ? 'Adding…' : 'Add all'}</button>
          </div>
        </>
      )}
    </div>
  );
}

function EmptyCart({ navigate }) {
  return (
    <div className="col" style={{ flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24, gap: 14 }}>
      <div className="foil-soft" style={{ width: 72, height: 72, borderRadius: 18 }}/>
      <div style={{ fontSize: 18, fontWeight: 600 }}>No scans yet</div>
      <div style={{ fontSize: 13, color: 'var(--ink-3)', textAlign: 'center', maxWidth: 240 }}>Scan a stack of cards. We'll queue them here for review before adding to your collection.</div>
      <button className="tap" onClick={() => navigate('scan')} style={{
        marginTop: 8, padding: '12px 20px', borderRadius: 14, background: 'var(--accent)', color: 'var(--accent-ink)', fontWeight: 600, fontSize: 14,
      }}>Start scanning</button>
    </div>
  );
}

export default BulkScreen
