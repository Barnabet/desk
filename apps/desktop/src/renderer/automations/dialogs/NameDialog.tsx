import { useState } from 'react';
import { AutomationName } from '@desk/protocol';
import { Button } from '../../components/Button';
import { Field } from '../../components/Field';
import { Sheet } from '../../components/Sheet';

/** Asks for an automation name (fixed at creation): Blank automation, and importing under another name. */
export function NameDialog(o: { title: string; confirmLabel: string; initial?: string; taken: string[]; hint?: string; onClose(): void; onConfirm(name: string): void }) {
  const [name, setName] = useState(o.initial ?? '');
  const parsed = AutomationName.safeParse(name);
  const error = !name ? null : !parsed.success ? (parsed.error.issues[0]?.message ?? 'Not a valid name') : o.taken.includes(name) ? 'Another automation already has this name.' : null;
  const ok = name !== '' && error === null;
  return (
    <Sheet
      title={o.title}
      onClose={o.onClose}
      width={460}
      footer={
        <>
          <Button onClick={o.onClose}>Cancel</Button>
          <Button variant="primary" disabled={!ok} onClick={() => o.onConfirm(name)}>
            {o.confirmLabel}
          </Button>
        </>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (ok) o.onConfirm(name);
        }}
      >
        <Field id="automation-name" label="Name" hint={o.hint ?? 'Lowercase letters, digits and dashes, e.g. weekly-digest. The name cannot change later; the title can.'} error={error}>
          <input id="automation-name" className="input mono" value={name} onChange={(e) => setName(e.target.value.toLowerCase().replace(/\s+/g, '-'))} />
        </Field>
      </form>
    </Sheet>
  );
}
