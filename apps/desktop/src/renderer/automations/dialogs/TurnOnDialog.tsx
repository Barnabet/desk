import { useEffect, useMemo, useState } from 'react';
import type { AutomationDetail } from '@desk/protocol';
import { dayTime, describeGrant, describeSchedule, sameGrant, uniqueGrants } from '@desk/ui-core';
import { call } from '../../bridge';
import { Button } from '../../components/Button';
import { Sheet } from '../../components/Sheet';
import { toastError } from '../../components/Toast';
import { useNow } from '../../state/now';

/**
 * Turning an automation on (spec §5.4): its schedules with their next time, the grants its runs were approved for
 * (ticked), and a warning when the current version has no succeeded test. Grants are set first, then the switch.
 */
export function TurnOnDialog(o: { detail: AutomationDetail; onClose(): void; onDone(detail: AutomationDetail): void; onTestFirst(): void }) {
  const d = o.detail;
  const now = useNow();
  const proposals = useMemo(() => uniqueGrants([...(d.enable_request?.proposed_grants ?? []), ...d.proposed_grants]).filter((g) => !d.grants.some((x) => sameGrant(x, g))), [d]);
  const [ticked, setTicked] = useState<boolean[]>(() => proposals.map(() => true));
  const [next, setNext] = useState<Record<string, string[]>>({});
  const [pending, setPending] = useState(false);
  useEffect(() => {
    let live = true;
    call('automations.validate', { projectId: d.project_id, req: { definition: d.definition, name: d.name } })
      .then((r) => live && setNext(r.next_times))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [d.project_id, d.definition, d.name]);
  const untested = d.tested_version !== d.version;
  const turnOn = async () => {
    setPending(true);
    try {
      const grants = uniqueGrants([...d.grants, ...proposals.filter((_, i) => ticked[i])]);
      await call('automations.setGrants', { id: d.id, grants, reason: 'enabled' });
      o.onDone(await call('automations.setEnabled', { id: d.id, enabled: true }));
    } catch (err) {
      toastError(err);
      setPending(false);
    }
  };
  return (
    <Sheet
      title={`Turn on ${d.title}`}
      onClose={o.onClose}
      width={560}
      footer={
        <>
          <Button onClick={o.onClose}>Cancel</Button>
          {untested ? <Button onClick={o.onTestFirst}>Test first</Button> : null}
          <Button variant="primary" pending={pending} onClick={() => void turnOn()}>
            {untested ? 'Turn on anyway' : 'Turn on'}
          </Button>
        </>
      }
    >
      {untested ? (
        <p className="auto-warn" role="alert">
          {`v${d.version} hasn't been tested${d.tested_version ? ` (v${d.tested_version} was)` : ''}.`}
        </p>
      ) : null}
      <h3 className="auto-sub">When it runs</h3>
      {d.definition.triggers.length ? (
        <ul className="auto-plain">
          {d.definition.triggers.map((t, i) => {
            const first = next[String(i)]?.[0];
            return (
              <li key={i}>
                {describeSchedule(t.cron, t.timezone)}
                {first ? <span className="muted">{` · next ${dayTime(first, now)}`}</span> : null}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="muted">It has no schedule, so turning it on changes nothing until you add one. Run now works either way.</p>
      )}
      {d.enable_request?.note ? <blockquote className="auto-quote">{`Desk: ${d.enable_request.note}`}</blockquote> : null}
      <h3 className="auto-sub">Grants</h3>
      {d.grants.length ? (
        <ul className="auto-plain">
          {d.grants.map((g, i) => (
            <li key={i}>
              {describeGrant(g)} <span className="muted">(kept)</span>
            </li>
          ))}
        </ul>
      ) : null}
      {proposals.length ? (
        <>
          <p className="muted small">Approved during its runs. Ticked ones let later runs go ahead without asking you.</p>
          {proposals.map((g, i) => (
            <label key={i} className="auto-check">
              <input type="checkbox" checked={ticked[i] ?? false} onChange={(e) => setTicked((t) => t.map((v, j) => (j === i ? e.target.checked : v)))} /> {describeGrant(g)}
            </label>
          ))}
        </>
      ) : !d.grants.length ? (
        <p className="muted">No grants: anything its steps need approval for will ask you when it runs.</p>
      ) : null}
    </Sheet>
  );
}
