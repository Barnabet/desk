import type { TemplateSuggestion } from '@desk/ui-core';
import { Button } from '../../components/Button';
import { TemplateField } from './TemplateField';

/** A list of short texts (routes, globs, arguments): one row each, Remove per row, and Add. Rows are labelled "<label> <n>". */
export function ListEditor(o: {
  id: string;
  label: string;
  values: string[];
  onChange(values: string[]): void;
  placeholder?: string;
  hint?: string;
  addLabel?: string;
  /** Checks one value; its message shows under that row. */
  check?(value: string): string | null;
  /** Makes each row a template field (script arguments). */
  suggestions?: TemplateSuggestion[];
  max?: number;
}) {
  const set = (i: number, v: string) => o.onChange(o.values.map((x, j) => (j === i ? v : x)));
  const remove = (i: number) => o.onChange(o.values.filter((_, j) => j !== i));
  return (
    <fieldset className="field auto-rows">
      <legend>{o.label}</legend>
      {o.values.map((v, i) => {
        const rowId = `${o.id}-${i}`;
        const rowLabel = `${o.label} ${i + 1}`;
        const problem = v && o.check ? o.check(v) : null;
        return (
          <div key={i}>
            <div className="auto-row">
              {o.suggestions ? (
                <TemplateField id={rowId} label={rowLabel} labelHidden value={v} onChange={(x) => set(i, x)} suggestions={o.suggestions} {...(o.placeholder ? { placeholder: o.placeholder } : {})} />
              ) : (
                <input id={rowId} aria-label={rowLabel} className="input mono" value={v} placeholder={o.placeholder} onChange={(e) => set(i, e.target.value)} />
              )}
              <Button size="sm" variant="ghost" aria-label={`Remove ${rowLabel}`} onClick={() => remove(i)}>
                ✕
              </Button>
            </div>
            {problem ? <p className="field-error auto-row-error">{problem}</p> : null}
          </div>
        );
      })}
      {o.max === undefined || o.values.length < o.max ? (
        <div>
          <Button size="sm" onClick={() => o.onChange([...o.values, ''])}>
            {o.addLabel ?? 'Add'}
          </Button>
        </div>
      ) : null}
      {o.hint ? <p className="field-hint">{o.hint}</p> : null}
    </fieldset>
  );
}
