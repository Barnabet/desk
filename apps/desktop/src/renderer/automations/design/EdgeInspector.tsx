import { conditionSuggestions, edgeRouteOptions, patchEdge, type AutomationDoc } from '@desk/ui-core';
import { Button } from '../../components/Button';
import { Field } from '../../components/Field';

/** The selected edge (spec §3.2): what its source must do for it to fire, and an optional condition. */
export function EdgeInspector(o: { doc: AutomationDoc; index: number; errors: string[]; onChange(doc: AutomationDoc): void; onDelete(): void }) {
  const edge = o.doc.def.edges[o.index];
  if (!edge) return null;
  const title = (id: string) => o.doc.def.steps.find((s) => s.id === id)?.title ?? id;
  const set = (p: { route?: string | undefined; when?: string | undefined }) => o.onChange(patchEdge(o.doc, o.index, p));
  const paths = conditionSuggestions(o.doc.def, edge.from).filter((s) => !s.open);
  const append = (path: string) => set({ when: edge.when ? `${edge.when.trimEnd()} ${path}` : path });
  return (
    <aside className="auto-inspector" aria-label="Edge">
      <p className="eyebrow">Edge</p>
      <h2>{`${title(edge.from)} → ${title(edge.to)}`}</h2>
      {o.errors.length ? (
        <ul className="auto-issues" role="alert">
          {o.errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      ) : null}
      <Field id="edge-route" label="Fires">
        <select id="edge-route" className="select" value={edge.route ?? ''} onChange={(e) => set({ route: e.target.value || undefined })}>
          {edgeRouteOptions(o.doc.def, edge.from, edge.route).map((r) => (
            <option key={r.value} value={r.value}>
              {r.label}
            </option>
          ))}
        </select>
      </Field>
      <Field id="edge-when" label="Only if" hint="Optional, e.g. steps.fetch.outputs.count > 0. Use == != < <= > >= contains exists(…) and or not.">
        <input id="edge-when" className="input mono" value={edge.when ?? ''} onChange={(e) => set({ when: e.target.value.trim() ? e.target.value : undefined })} />
      </Field>
      {paths.length ? (
        <div className="auto-paths" aria-label="Paths it can use">
          {paths.map((p) => (
            <button key={p.path} type="button" className="auto-path" title={p.label} onClick={() => append(p.path)}>
              {p.path}
            </button>
          ))}
        </div>
      ) : null}
      <div>
        <Button variant="danger" size="sm" onClick={o.onDelete}>
          Delete edge
        </Button>
      </div>
    </aside>
  );
}
