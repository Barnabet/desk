import { useState } from 'react';
import { call } from '../bridge';
import { Button } from '../components/Button';
import { Field } from '../components/Field';
import { Sheet } from '../components/Sheet';
import { toast, toastError } from '../components/Toast';
import { navigate } from '../router';

/** Sends Desk a message about a skill: build a new one, or refine a named one. Global skills need a project whose Desk does the work. */
export function AskDesk(o: { skillName?: string; projects: Array<{ id: string; name: string }>; defaultProjectId?: string; onClose(): void }) {
  const [projectId, setProjectId] = useState(o.defaultProjectId ?? o.projects[0]?.id ?? '');
  const [text, setText] = useState(o.skillName ? `Refine the skill "${o.skillName}": ` : 'Build a new skill that ');
  const [pending, setPending] = useState(false);
  const send = async () => {
    setPending(true);
    try {
      await call('projects.send', { id: projectId, text: text.trim() });
      toast({ tone: 'info', message: 'Sent to Desk.' });
      o.onClose();
      navigate({ name: 'project', id: projectId, tab: 'conversation' });
    } catch (err) {
      toastError(err);
    } finally {
      setPending(false);
    }
  };
  return (
    <Sheet
      title={o.skillName ? `Refine ${o.skillName} with Desk` : 'Ask Desk for a new skill'}
      onClose={o.onClose}
      footer={
        <>
          <Button onClick={o.onClose}>Cancel</Button>
          <Button variant="primary" pending={pending} disabled={!projectId || text.trim().length < 12} onClick={() => void send()}>
            Send to Desk
          </Button>
        </>
      }
    >
      {o.projects.length ? (
        <>
          <Field id="ask-project" label="Which project's Desk" hint="Desk has a thread draft and test it, then installs it.">
            <select id="ask-project" className="select" value={projectId} onChange={(e) => setProjectId(e.target.value)}>
              {o.projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </Field>
          <Field id="ask-text" label="Message">
            <textarea id="ask-text" className="textarea" rows={4} value={text} onChange={(e) => setText(e.target.value)} />
          </Field>
        </>
      ) : (
        <p>Create a project first; Desk works on skills from inside a project.</p>
      )}
    </Sheet>
  );
}
