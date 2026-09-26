import { useState } from 'react';
import type { BuiltinSkillInfo } from '@desk/protocol';
import { call } from '../../bridge';
import { toastError } from '../../components/Toast';
import { useGlobal } from '../../state/global';
import { runtimeKey } from '../../../shared/state';
import { builtinKey, runtimeLabel } from './data';

const OPEN_KEY = 'desk.builtinsOpen';

/** The on/off switch of a built-in skill; `label` names the skill for screen readers. */
export function BuiltinSwitch({ item, label, onChanged }: { item: BuiltinSkillInfo; label: string; onChanged(): void }) {
  const [pending, setPending] = useState(false);
  const toggle = async () => {
    setPending(true);
    try {
      await call('builtins.setEnabled', { name: item.name, enabled: !item.enabled });
      onChanged();
    } catch (err) {
      toastError(err);
    } finally {
      setPending(false);
    }
  };
  return (
    <button type="button" role="switch" aria-checked={item.enabled} aria-label={label} title={item.enabled ? 'On: agents can use it' : 'Off: agents never see it'} className="switch" disabled={pending} onClick={() => void toggle()}>
      <span className="switch-knob" aria-hidden="true" />
    </button>
  );
}

/** A built-in skill's environment line, with live progress while it is being set up. */
export function BuiltinRuntime({ item }: { item: BuiltinSkillInfo }) {
  const progress = useGlobal((g) => g.runtimes.progress[runtimeKey('builtin', null, item.name)]);
  if (item.broken) {
    return (
      <span className="runtime-line failed">
        <span className="runtime-dot" aria-hidden="true" />
        Damaged: reinstall Desk
      </span>
    );
  }
  return (
    <span className={`runtime-line ${item.runtime.state}`}>
      <span className="runtime-dot" aria-hidden="true" />
      {runtimeLabel(item, progress)}
    </span>
  );
}

/**
 * Desk's own skills, above the user's: a card each with its environment and an on/off switch. `collapsible` (the
 * map view) folds the cards into one line until opened.
 */
export function BuiltinGroup(o: { items: BuiltinSkillInfo[]; selected: string | null; collapsible?: boolean; onSelect(key: string): void; onChanged(): void }) {
  const [open, setOpen] = useState(() => {
    try {
      return localStorage.getItem(OPEN_KEY) !== 'false';
    } catch {
      return true;
    }
  });
  const shown = !o.collapsible || open;
  const flip = () => {
    setOpen(!open);
    try {
      localStorage.setItem(OPEN_KEY, String(!open));
    } catch {
      // A convenience only.
    }
  };
  const off = o.items.filter((b) => !b.enabled).length;
  return (
    <section className="builtin-group" aria-label="Built into Desk">
      <header className="builtin-head">
        <h2>Built into Desk</h2>
        <span className="muted small grow">
          Any file type: read, create, edit, convert — and see it; plus web research · {o.items.length}
          {off ? ` · ${off} off` : ''}
        </span>
        {o.collapsible ? (
          <button type="button" className="builtin-fold" aria-expanded={shown} onClick={flip}>
            {shown ? 'Hide' : 'Show'}
          </button>
        ) : null}
      </header>
      {shown ? (
        <ul className="builtin-cards">
          {o.items.map((b) => (
            <li key={b.name} className={`card builtin-card${o.selected === builtinKey(b.name) ? ' selected' : ''}${b.enabled && !b.broken ? '' : ' off'}`}>
              <button type="button" className="builtin-open" aria-label={`Open ${b.title}`} onClick={() => o.onSelect(builtinKey(b.name))}>
                <span className="builtin-title">{b.title}</span>
                <span className="builtin-meta">
                  <span className="chip chip-idle">Built in</span>
                  {b.shadowed_by ? <span className="chip chip-wait">Shadowed by your {b.shadowed_by} skill</span> : null}
                </span>
                <BuiltinRuntime item={b} />
              </button>
              {b.broken ? null : <BuiltinSwitch item={b} label={b.title} onChanged={o.onChanged} />}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
