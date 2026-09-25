import type { AttentionBays } from '@desk/client';
import { BAYS } from '@desk/ui-core';
import { FlightStrip } from './FlightStrip';

/** The rack: four bays, each a recessed tray holding its strips. */
export function StripRack(o: { bays: AttentionBays; now: number; selectedId: string | null; threadTitle(id: string): string | null; onSelect(id: string): void }) {
  return (
    <section className="rack" aria-label="Strip rack">
      {BAYS.map((b) => {
        const items = o.bays[b.key];
        return (
          <div key={b.key} className="bay" role="group" aria-label={`${b.name}: ${b.sub}, ${items.length}`}>
            <div className="bay-name">
              <span className="bay-title">{b.name}</span>
              <span className="bay-sub">
                {b.sub} · {items.length}
              </span>
            </div>
            <div className="bay-tray">
              {items.length ? (
                items.map((i) => <FlightStrip key={i.id} item={i} now={o.now} selected={i.id === o.selectedId} threadTitle={o.threadTitle} onSelect={() => o.onSelect(i.id)} />)
              ) : (
                <span className="bay-empty">Nothing here</span>
              )}
            </div>
          </div>
        );
      })}
    </section>
  );
}
