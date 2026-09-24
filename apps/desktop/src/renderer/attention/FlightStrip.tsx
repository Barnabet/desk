import type { AttentionItem } from '@desk/protocol';
import { gauge, STRIP_CODE, stripWho, waited } from './strips';
import './attention.css';

const CAP_COLOR: Record<AttentionItem['kind'], string> = { approval: 'var(--accent)', question: 'var(--ink)', needs_you: 'var(--ink)', stalled: 'var(--wait)', failed: 'var(--accent)' };

/** One flight strip: end cap (code and age), project and title, who, the wait gauge, and a chevron. */
export function FlightStrip(o: { item: AttentionItem; now: number; selected: boolean; threadTitle(id: string): string | null; onSelect(): void; compact?: boolean }) {
  const { item: i } = o;
  const who = stripWho(i, o.threadTitle);
  const g = gauge(i.created_at, o.now);
  const age = waited(i.created_at, o.now);
  return (
    <button
      type="button"
      className={`strip strip-${i.kind}${o.selected ? ' selected' : ''}${o.compact ? ' compact' : ''}`}
      aria-current={o.selected || undefined}
      aria-label={`${STRIP_CODE[i.kind]}, ${i.project_name}: ${i.title}. ${who.label} ${who.name}. Waiting ${age}.`}
      onClick={o.onSelect}
    >
      <span className="strip-cap" aria-hidden="true">
        <span className="strip-code">{STRIP_CODE[i.kind]}</span>
        <span className="strip-age">{age}</span>
      </span>
      <span className="strip-title" aria-hidden="true">
        <span className="strip-project">{i.project_name}</span>
        <span className="strip-text">{i.title}</span>
      </span>
      {o.compact ? null : (
        <>
          <span className="strip-col strip-who" aria-hidden="true">
            <span className="strip-label">{who.label}</span>
            <span className="strip-name">{who.name}</span>
            <span className={`strip-tag${i.kind === 'stalled' ? ' wait' : ''}`}>{who.tag}</span>
          </span>
          <span className="strip-col strip-wait" aria-hidden="true">
            <span className="strip-label">Waiting</span>
            <svg width="76" height="10" viewBox="0 0 76 10">
              <rect x="0" y="3" width="76" height="4" rx="2" fill="#E6E0D4" />
              <path d="M38 1 V9 M75.5 1 V9" stroke="#8A857B" strokeWidth="1" />
              <rect x="0" y="3" width={Math.max(3, 76 * g)} height="4" rx="2" fill={CAP_COLOR[i.kind]} />
            </svg>
            <span className="strip-age-big">{age}</span>
          </span>
        </>
      )}
      <span className="strip-chevron" aria-hidden="true">
        ›
      </span>
    </button>
  );
}
