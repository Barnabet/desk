import { useRef } from 'react';
import { useWidth } from '../state/width';
import { routeLayout, stopText, type Stop, type StopKind } from './route';

const INNER: Record<StopKind, (s: Stop) => string> = {
  brief: () => 'Brief',
  work: (s) => (s.tools.length ? String(s.tools.length) : '…'),
  detour: () => 'detour',
  result: () => 'Report',
  revision: (s) => {
    const e = s.entries[0];
    return e?.kind === 'revision' ? `R${e.round}` : 'R';
  },
  steer: () => 'You',
  approval: () => '!',
  incoming: () => 'Desk',
};

/** A thread's route: numbered stops on a serpentine path, the live stretch in blue, and what comes next dashed. */
export function RouteView(o: { stops: Stop[]; running: boolean; activity: string | null; reviewRounds: number; selected: number | null; onSelect(n: number): void }) {
  const ref = useRef<HTMLDivElement>(null);
  const width = useWidth(ref, 900);
  const l = routeLayout(o.stops, Math.max(520, width), o.running);
  return (
    <div className="route" ref={ref}>
      <div className="route-canvas" style={{ height: l.height }}>
        <svg width={l.width} height={l.height} aria-hidden="true" className="route-svg">
          {l.pieces.map((p, i) => (
            <path key={i} d={p.d} fill="none" stroke={p.live ? '#2F5BD3' : '#1C1B18'} strokeWidth={p.live ? 2.5 : 2} />
          ))}
          {l.tailPath ? <path d={l.tailPath} fill="none" stroke="#8A857B" strokeWidth={2} strokeDasharray="4 5" /> : null}
        </svg>
        {l.points.map((p) => {
          const t = stopText(p.stop, o.reviewRounds);
          const above = p.stop.kind === 'detour';
          const label = `Stop ${p.stop.n}: ${t.title}${t.sub ? `, ${t.sub}` : ''}`;
          return (
            <div key={p.stop.n}>
              <button
                type="button"
                className={`route-stop route-stop-${p.stop.kind}${p.stop.live ? ' live' : ''}${o.selected === p.stop.n ? ' selected' : ''}`}
                style={{ left: p.x, top: p.y, width: p.r * 2, height: p.r * 2 }}
                aria-label={label}
                aria-pressed={o.selected === p.stop.n}
                onClick={() => o.onSelect(p.stop.n)}
              >
                <span aria-hidden="true">{INNER[p.stop.kind](p.stop)}</span>
              </button>
              <span className="route-num" aria-hidden="true" style={{ left: p.x + p.r * 0.8, top: p.y - p.r * 0.8 }}>
                {p.stop.n}
              </span>
              <div className={`route-label${above ? ' above' : ''}`} style={{ left: p.x, top: above ? p.y - p.r - 6 : p.y + p.r + 6 }}>
                <span className="route-label-title">{t.title}</span>
                {t.sub ? <span className="route-label-sub">{t.sub}</span> : null}
                {t.quote ? <span className="route-label-quote">“{t.quote}”</span> : null}
              </div>
            </div>
          );
        })}
        {l.now ? (
          <>
            <span className="route-now" aria-hidden="true" style={{ left: l.now.x, top: l.now.y }} />
            <div className="route-label" style={{ left: l.now.x, top: l.now.y + 20 }}>
              <span className="route-label-title run">Now{o.activity ? ` · ${o.activity.split(' ')[0]}` : ''}</span>
              {o.activity ? <span className="route-label-sub mono">{o.activity.split(' ').slice(1).join(' ')}</span> : null}
            </div>
          </>
        ) : null}
        {l.tail.map((t, i) => (
          <div key={i}>
            <span className="route-next" aria-hidden="true" style={{ left: t.x, top: t.y }} />
            <div className="route-label" style={{ left: t.x, top: t.y + 18 }}>
              <span className="route-label-sub">Next: report to Desk</span>
            </div>
          </div>
        ))}
      </div>
      <div className="route-legend">
        <span>
          <span className="route-legend-line" />
          Route taken
        </span>
        <span>
          <span className="route-legend-line live" />
          Live since sent back
        </span>
        <span>
          <span className="route-legend-line next" />
          Next
        </span>
        <span>Detour = continued on the fallback model</span>
        <strong>Numbers match the transcript</strong>
      </div>
    </div>
  );
}
