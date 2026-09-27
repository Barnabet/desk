import type { DefinitionDiff, FieldChange } from '@desk/ui-core';
import { STEP_KIND_LABEL } from '@desk/ui-core';

const TAG = { added: 'added', removed: 'removed', changed: 'changed' } as const;

function Fields({ fields }: { fields: FieldChange[] }) {
  return (
    <table className="auto-diff-fields">
      <tbody>
        {fields.map((f) => (
          <tr key={f.field}>
            <th className="mono">{f.field}</th>
            <td className="auto-before">{f.before}</td>
            <td className="auto-after">{f.after}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** A structured diff between two definitions (spec §8.4). Everything is plain text. */
export function DiffView({ diff, labels }: { diff: DefinitionDiff; labels?: { before: string; after: string } }) {
  if (diff.empty) return <p className="muted">No changes.</p>;
  return (
    <div className="auto-diff">
      {labels ? <p className="muted small">{`Red is ${labels.before}, green is ${labels.after}.`}</p> : null}
      {diff.steps.length ? (
        <section>
          <h3 className="auto-sub">Steps</h3>
          <ul className="auto-plain">
            {diff.steps.map((s) => (
              <li key={s.id} className="auto-diff-item">
                <span className={`auto-tag ${TAG[s.change]}`}>{s.change}</span> <b>{s.title}</b> <span className="muted small">{`${STEP_KIND_LABEL[s.kind]} · ${s.id}`}</span>
                {s.fields.length ? <Fields fields={s.fields} /> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {diff.edges.added.length || diff.edges.removed.length ? (
        <section>
          <h3 className="auto-sub">Edges</h3>
          <ul className="auto-plain">
            {diff.edges.removed.map((e) => (
              <li key={`-${e}`} className="auto-diff-item">
                <span className="auto-tag removed">removed</span> <span>{e}</span>
              </li>
            ))}
            {diff.edges.added.map((e) => (
              <li key={`+${e}`} className="auto-diff-item">
                <span className="auto-tag added">added</span> <span>{e}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {diff.inputs.length ? (
        <section>
          <h3 className="auto-sub">Inputs</h3>
          <ul className="auto-plain">
            {diff.inputs.map((i) => (
              <li key={i.key} className="auto-diff-item">
                <span className={`auto-tag ${TAG[i.change]}`}>{i.change}</span> <b>{i.label}</b> <span className="muted small mono">{i.key}</span>
                {i.fields.length ? <Fields fields={i.fields} /> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {diff.schedules ? (
        <section>
          <h3 className="auto-sub">Schedules</h3>
          <p className="auto-before">{diff.schedules.before.join(' · ') || 'Run now only'}</p>
          <p className="auto-after">{diff.schedules.after.join(' · ') || 'Run now only'}</p>
        </section>
      ) : null}
      {diff.settings.length ? (
        <section>
          <h3 className="auto-sub">Settings</h3>
          <Fields fields={diff.settings} />
        </section>
      ) : null}
    </div>
  );
}
