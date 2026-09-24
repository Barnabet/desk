import { useEffect, useState } from 'react';
import type { ModelInfo } from '@desk/protocol';
import { call } from '../bridge';
import { Button } from '../components/Button';
import { describeError, toast, toastError } from '../components/Toast';

const blank = (): ModelInfo => ({ id: '', family: 'claude', context_window: 200_000, max_output_tokens: 32_000, supports_reasoning_effort: false, concurrency: 4 });

/** Problems that would make PUT /models fail, in words. */
export function modelProblems(list: ModelInfo[]): string[] {
  const out: string[] = [];
  if (!list.length) out.push('Keep at least one model.');
  const ids = list.map((m) => m.id.trim());
  if (ids.some((id) => !id)) out.push('Every model needs an id.');
  const dup = ids.find((id, i) => id && ids.indexOf(id) !== i);
  if (dup) out.push(`${dup} is listed twice.`);
  if (list.some((m) => !(m.context_window > 0) || !(m.max_output_tokens > 0) || !(m.concurrency >= 1))) out.push('Token limits must be positive and concurrency at least 1.');
  return out;
}

/** The model registry (PUT /models): the models Desk can pick, their limits and how many calls may run at once. */
export function ModelsEditor() {
  const [saved, setSaved] = useState<ModelInfo[] | null>(null);
  const [draft, setDraft] = useState<ModelInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  useEffect(() => {
    call('models.list', {})
      .then((m) => (setSaved(m), setDraft(m)))
      .catch((err) => setError(describeError(err).message));
  }, []);
  if (error) return <p className="field-error">{error}</p>;
  if (!saved) return <p className="muted">Loading…</p>;
  const set = (i: number, patch: Partial<ModelInfo>) => setDraft((d) => d.map((m, k) => (k === i ? { ...m, ...patch } : m)));
  const problems = modelProblems(draft);
  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);
  const save = async () => {
    setPending(true);
    try {
      const next = await call('models.replace', { models: draft.map((m) => ({ ...m, id: m.id.trim() })) });
      setSaved(next);
      setDraft(next);
      toast({ tone: 'info', message: 'Model registry saved.' });
    } catch (err) {
      toastError(err);
    } finally {
      setPending(false);
    }
  };
  const num = (v: string) => Math.max(0, Math.floor(Number(v) || 0));
  return (
    <div className="models-editor">
      <table className="models-table">
        <thead>
          <tr>
            <th scope="col">Model id</th>
            <th scope="col">Family</th>
            <th scope="col">Context</th>
            <th scope="col">Max output</th>
            <th scope="col">Reasoning effort</th>
            <th scope="col">At once</th>
            <th scope="col">
              <span className="sr-only">Remove</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {draft.map((m, i) => (
            <tr key={i}>
              <td>
                <input className="input mono" aria-label={`Model ${i + 1} id`} value={m.id} onChange={(e) => set(i, { id: e.target.value })} />
              </td>
              <td>
                <select className="select" aria-label={`Model ${i + 1} family`} value={m.family} onChange={(e) => set(i, { family: e.target.value as ModelInfo['family'] })}>
                  <option value="claude">claude</option>
                  <option value="gpt">gpt</option>
                </select>
              </td>
              <td>
                <input className="input" type="number" aria-label={`Model ${i + 1} context window`} value={m.context_window} onChange={(e) => set(i, { context_window: num(e.target.value) })} />
              </td>
              <td>
                <input className="input" type="number" aria-label={`Model ${i + 1} max output tokens`} value={m.max_output_tokens} onChange={(e) => set(i, { max_output_tokens: num(e.target.value) })} />
              </td>
              <td>
                <input type="checkbox" aria-label={`Model ${i + 1} supports reasoning effort`} checked={m.supports_reasoning_effort} onChange={(e) => set(i, { supports_reasoning_effort: e.target.checked })} />
              </td>
              <td>
                <input className="input narrow" type="number" aria-label={`Model ${i + 1} concurrency`} value={m.concurrency} onChange={(e) => set(i, { concurrency: num(e.target.value) })} />
              </td>
              <td>
                <button type="button" className="icon-btn" aria-label={`Remove model ${i + 1}`} onClick={() => setDraft((d) => d.filter((_, k) => k !== i))}>
                  ✕
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {problems.length ? (
        <ul className="field-error" role="alert">
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      ) : null}
      <div className="actions">
        <Button size="sm" onClick={() => setDraft((d) => [...d, blank()])}>
          Add model
        </Button>
        <Button size="sm" variant="primary" pending={pending} disabled={!dirty || problems.length > 0} onClick={() => void save()}>
          Save registry
        </Button>
        {dirty ? (
          <Button size="sm" variant="ghost" onClick={() => setDraft(saved)}>
            Discard changes
          </Button>
        ) : null}
      </div>
    </div>
  );
}
