import { useLayoutEffect, useRef, useState } from 'react';
import { messageById, type MessagesState, type ProjectState, type ThreadView } from '@desk/client';
import type { AgentStatus, AttentionItem } from '@desk/protocol';
import { AnsweringBadge } from '../components/AnsweringBadge';
import { ago, clock, duration } from '../format';
import { href } from '../router';
import { answeringLabel, waitHop, waitLabel } from '../waits';
import type { LaneGeometry, LineGeometry } from './lineGeometry';

type StationG = LineGeometry['stations'][number];

/** A lane label's second line. `wait`: what a waiting thread waits on, from the message fold (waitLabel). */
function laneStatus(l: LaneGeometry, reviewRounds: number, wait: string | null, now: number): { text: string; tone: AgentStatus } {
  const t = l.thread;
  const status = t?.status ?? l.lane.status;
  const since = ago(l.lane.forkedAt, now);
  switch (status) {
    case 'running':
      return { text: t?.review_round ? `revision ${t.review_round} of ${reviewRounds} · ${since}` : `running · ${since}`, tone: status };
    case 'waiting':
      return { text: wait ?? `waiting · ${since}`, tone: status };
    case 'queued':
      return { text: /restart/i.test(t?.reason ?? '') ? 'will resume' : 'queued', tone: status };
    case 'done': {
      const last = l.lane.segments.at(-1);
      return { text: `done · ${duration(Date.parse(last?.from ?? l.lane.forkedAt) - Date.parse(l.lane.forkedAt))}`, tone: status };
    }
    default:
      return { text: status, tone: status };
  }
}

const shortModel = (m: string) => m.replace(/^claude-/, '');

/**
 * The transit diagram: Desk's trunk with stations, thread lanes forking and rejoining, trains at "now".
 * The lanes scroll sideways (older history to the left) and follow "now" unless the user has scrolled back;
 * the label column and the legend stay put.
 */
export function LineDiagram(o: {
  g: LineGeometry;
  project: ProjectState;
  /** The session's message fold: what waiting threads wait on, and which threads are answering. */
  messages: MessagesState;
  /** The project's attention items: only a thread with one is "waiting on you". */
  attention: readonly AttentionItem[];
  now: number;
  onStation(s: StationG): void;
  /** Opens the pair sheet of two agents: a question mark's asker and its recipient (design spec §8 item 8). */
  onPair(a: string, b: string): void;
}) {
  const { g, project } = o;
  const desk = project.desk;
  const pendingApproval = (threadId: string) => project.approvals.find((a) => a.agent_id === threadId && !a.delegate_to_desk);
  const threadHref = (threadId: string) => href({ name: 'project', id: project.project.id, tab: 'threads', threadId });
  const scroller = useRef<HTMLDivElement>(null);
  /** Whether the view follows "now" (the user hasn't scrolled back into history). */
  const pinned = useRef(true);
  /** The counterpart of the question mark under the pointer or focus: its label lights up. */
  const [lit, setLit] = useState<string | null>(null);
  const hasQuestions = g.lanes.some((l) => l.marks.some((m) => m.kind === 'question'));
  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && pinned.current) el.scrollLeft = el.scrollWidth;
  }, [g.contentWidth, g.nowX]);
  return (
    <section className="line-diagram" aria-label="Line diagram: Desk and its threads since the brief" style={{ height: g.height }}>
      <div
        className="line-scroll"
        ref={scroller}
        style={{ left: g.viewportLeft, width: g.viewportWidth, height: g.height }}
        onScroll={(e) => {
          const el = e.currentTarget;
          pinned.current = el.scrollWidth - el.scrollLeft - el.clientWidth < 8;
        }}
      >
        <div className="line-canvas" style={{ width: g.contentWidth, height: g.height }}>
          <svg width={g.contentWidth} height={g.height} aria-hidden="true" className="line-svg">
            {g.ticks.map((t) => (
              <path key={t.t} d={`M${t.x} 24 V${g.height}`} stroke="var(--rule-soft)" strokeDasharray="2 4" />
            ))}
            <path d={`M${g.nowX} 22 V${g.height}`} stroke="var(--text-min)" strokeDasharray="3 3" />
            {g.lanes.map((l) => (
              <g key={l.lane.threadId} opacity={l.lane.archived ? 0.45 : 1}>
                <path d={l.fork} fill="none" stroke={l.forkColor} strokeWidth={4} strokeLinecap="round" />
                {l.segments.map((s, i) => (
                  <path key={i} d={s.d} fill="none" stroke={s.color} strokeWidth={4} strokeLinecap="round" strokeDasharray={s.dashed ? '3 5' : undefined} />
                ))}
                {l.rejoins.map((d, i) => (
                  <path key={`r${i}`} d={d} fill="none" stroke={l.rejoinColor} strokeWidth={4} strokeLinecap="round" />
                ))}
                {l.stub ? <path className="line-stub" d={l.stub.d} fill="none" stroke="var(--muted)" strokeWidth={3} strokeLinecap="round" strokeDasharray="1 5" /> : null}
                {l.marks
                  .filter((m) => m.kind === 'detour')
                  .map((m) => (
                    <g key={`d${m.eventId}`}>
                      <path d={`M${m.x - 16} ${l.y} C${m.x - 8} ${l.y} ${m.x - 8} ${l.y - 12} ${m.x} ${l.y - 12} C${m.x + 8} ${l.y - 12} ${m.x + 8} ${l.y} ${m.x + 16} ${l.y}`} fill="none" stroke="var(--ground)" strokeWidth={9} />
                      <path d={`M${m.x - 16} ${l.y} C${m.x - 8} ${l.y} ${m.x - 8} ${l.y - 12} ${m.x} ${l.y - 12} C${m.x + 8} ${l.y - 12} ${m.x + 8} ${l.y} ${m.x + 16} ${l.y}`} fill="none" stroke={l.color} strokeWidth={4} strokeLinecap="round" />
                    </g>
                  ))}
              </g>
            ))}
            <path d={`M${g.trunkStart - 6} ${g.trunkY} H${g.nowX}`} stroke="var(--ink)" strokeWidth={6} strokeLinecap="round" />
          </svg>

          {g.ticks.map((t) => (
            <span key={t.t} className="line-tick" style={{ left: t.x }}>
              {clock(t.t)}
            </span>
          ))}
          <span className="line-now" style={{ left: g.nowX }}>
            now {clock(o.now)}
          </span>

          {g.stations.map((st) => (
            <div key={st.eventId}>
              <button
                type="button"
                className={`line-station line-station-${st.kind}`}
                style={{ left: st.x, top: g.trunkY }}
                aria-label={`${clock(st.ts)}, ${st.label}`}
                title={`${clock(st.ts)} · ${st.label}`}
                onClick={() => o.onStation(st)}
              />
              {st.showLabel ? (
                <span className={`line-station-label${st.x > g.x1 - 260 ? ' end' : st.x < 120 ? ' start' : ''}`} style={{ left: st.x, top: g.trunkY - 31 }}>
                  <span className="mono muted">{clock(st.ts)}</span> {st.kind === 'brief' ? `Your brief · ${st.label}` : st.label}
                </span>
              ) : null}
            </div>
          ))}

          {g.lanes.flatMap((l) =>
            l.marks
              .filter((m) => m.kind === 'detour' || m.kind === 'sent_back' || m.kind === 'stalled')
              .map((m) => (
                <span key={`${l.lane.threadId}-${m.eventId}`} className={`line-mark line-mark-${m.kind}`} style={{ left: m.x, top: m.kind === 'detour' ? l.y + 8 : l.y - 24 }}>
                  {m.kind === 'detour' ? (
                    <>
                      <span className="mono">
                        {clock(m.ts)} {shortModel(m.label)}
                      </span>
                      <span className="sr-only">: rate limited, continued on the fallback model</span>
                    </>
                  ) : m.kind === 'sent_back' ? (
                    `sent back · ${m.label}`
                  ) : (
                    'stalled'
                  )}
                </span>
              )),
          )}

          {g.lanes.flatMap((l) =>
            l.marks
              .filter((m) => m.kind === 'question')
              .map((m) => {
                // A tracked question on its asker's lane (design spec §8 item 10); its state is the fold's.
                const to = messageById(o.messages, m.eventId)?.to;
                const state = messageById(o.messages, m.eventId)?.state ?? 'open';
                return (
                  <button
                    key={`q-${m.eventId}`}
                    type="button"
                    className={`line-q line-q-${state}`}
                    style={{ left: m.x, top: l.y }}
                    aria-label={`${l.lane.title} asked ${m.label}, ${clock(m.ts)}`}
                    title={`${l.lane.title} asked ${m.label} · ${clock(m.ts)} · ${state}`}
                    onClick={() => to && o.onPair(l.lane.threadId, to)}
                    onMouseEnter={() => setLit(to ?? null)}
                    onMouseLeave={() => setLit(null)}
                    onFocus={() => setLit(to ?? null)}
                    onBlur={() => setLit(null)}
                  />
                );
              }),
          )}

          {g.lanes.map((l) =>
            l.stub ? (
              <span key={`answering-${l.lane.threadId}`} className="line-answering" style={{ left: l.stub.x, top: l.y }} aria-hidden="true">
                <span className="live-dot" />
              </span>
            ) : null,
          )}

          {g.lanes.map((l) => {
            const approval = l.signal ? pendingApproval(l.lane.threadId) : undefined;
            return l.signal ? (
              <a key={`sig-${l.lane.threadId}`} className="line-signal" style={{ left: l.signal.x, top: l.y }} href={href({ name: 'attention', ...(approval ? { item: `approval:${approval.id}` } : {}) })}>
                <span className="line-signal-chip">
                  <strong>Waiting for your approval</strong> · <span className="mono">{l.signal.label}</span> · <span className="mono muted">{clock(l.signal.ts)}</span>
                </span>
                <span className="line-signal-dot" aria-hidden="true" />
              </a>
            ) : null;
          })}

          {g.lanes.map((l) => {
            // A waiting lane whose wait leads to something that needs the user ends in a vermilion dot linking to it.
            const hop = (l.thread?.status ?? l.lane.status) === 'waiting' ? waitHop(o.messages, l.lane.threadId, o.attention) : null;
            return hop ? (
              <a
                key={`hop-${l.lane.threadId}`}
                className="line-hop"
                style={{ left: g.nowX, top: l.y }}
                href={href({ name: 'attention', item: hop.item.id })}
                aria-label={hop.label}
                title={`${l.lane.title} waits on it: ${hop.label}`}
              />
            ) : null;
          })}

          {g.lanes.map((l) =>
            l.trainX !== null ? (
              <div key={`train-${l.lane.threadId}`}>
                {l.thread?.activity ? (
                  <span className="line-activity" style={{ left: l.trainX - 18, top: l.y - 26 }}>
                    {l.thread.activity}
                  </span>
                ) : null}
                <a
                  className="line-train"
                  style={{ left: l.trainX, top: l.y }}
                  href={threadHref(l.lane.threadId)}
                  aria-label={`${l.lane.title}, running now`}
                >
                  <span aria-hidden="true" />
                </a>
              </div>
            ) : null,
          )}

          {g.lanes.map((l) =>
            l.inlineLabel ? (
              <a
                key={`title-${l.lane.threadId}`}
                className={`line-lane-title${lit === l.lane.threadId ? ' lit' : ''}`}
                style={{ left: l.inlineLabel.x, top: l.y - 20, maxWidth: l.inlineLabel.width }}
                href={threadHref(l.lane.threadId)}
              >
                {l.lane.title}
              </a>
            ) : null,
          )}

          {desk?.status === 'running' ? (
            <span className="line-desk-writing" style={{ left: g.nowX - 10, top: g.trunkY }}>
              <span className="live-dot" aria-hidden="true" />
              Desk · writing
            </span>
          ) : null}
        </div>
      </div>

      <div className={`line-label${lit !== null && lit === desk?.id ? ' lit' : ''}`} style={{ top: g.trunkY - 15 }}>
        <span className="line-label-title">
          <span className="line-swatch line-swatch-desk" />
          Desk
        </span>
        <span className={`line-label-sub status-text-${desk?.status ?? 'idle'}`}>
          {desk ? `${desk.status === 'running' ? 'writing' : desk.status} · ${shortModel(desk.model_override ?? desk.model)}` : ''}
        </span>
      </div>
      {g.rows.map((l) => {
        const id = l.lane.threadId;
        const waiting = (l.thread?.status ?? l.lane.status) === 'waiting';
        const wait = waiting ? waitLabel(o.messages, id, o.attention, o.now) : null;
        // One hop further when what it waits on needs the user (design spec §8 item 11); the dot at the lane's end links there.
        const hop = wait ? waitHop(o.messages, id, o.attention) : null;
        const s = laneStatus(l, project.project.settings.review_rounds, wait && hop ? `${wait} ${hop.text}` : wait, o.now);
        // An answer run keeps the thread's status: the label says it is answering (design spec §8 item 3).
        const answering = answeringLabel(o.messages, id);
        return (
          <a key={id} className={`line-label${lit === id ? ' lit' : ''}`} style={{ top: l.y - 15 }} href={threadHref(id)}>
            <span className="line-label-title">
              <span className="line-swatch" style={{ background: l.color }} />
              {l.lane.title}
            </span>
            <span className={`line-label-sub status-text-${s.tone}`}>
              {answering ? (
                <>
                  {s.tone} · <AnsweringBadge label={answering} />
                </>
              ) : (
                s.text
              )}
            </span>
          </a>
        );
      })}


      <div className="line-legend" aria-hidden="true">
        <span><span className="line-swatch line-swatch-desk" />Desk</span>
        <span><span className="line-swatch" style={{ background: 'var(--run)' }} />running</span>
        <span><span className="line-swatch" style={{ background: 'var(--muted)' }} />done</span>
        <span><span className="line-swatch" style={{ background: 'var(--wait)' }} />waiting</span>
        <span><span className="line-legend-dot" />needs you</span>
        {hasQuestions ? (
          <span>
            <span className="line-legend-q" />
            question
          </span>
        ) : null}
        <span>
          <svg width="18" height="10" viewBox="0 0 18 10">
            <path d="M1 8 H4 C6 8 6 2 9 2 C12 2 12 8 14 8 H17" fill="none" stroke="var(--run)" strokeWidth="2" strokeLinecap="round" />
          </svg>
          fallback
        </span>
      </div>
    </section>
  );
}

export type { ThreadView };
