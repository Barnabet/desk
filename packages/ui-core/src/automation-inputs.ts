import type { InputSpec, InputValue } from '@desk/protocol';

/** A run input while the user fills the form: empty is `''` (unticked is `false`). */
export type InputDraft = InputValue | '';

/** Each input's default, or empty (unticked for booleans). */
export function initialValues(inputs: InputSpec[]): Record<string, InputDraft> {
  return Object.fromEntries(inputs.map((i) => [i.key, i.default ?? (i.type === 'boolean' ? false : '')]));
}

/** The inputs to send: empty optional ones left out, numbers parsed, text trimmed. Null while one is missing or not a number. */
export function runInputs(inputs: InputSpec[], values: Record<string, InputDraft>): Record<string, InputValue> | null {
  const out: Record<string, InputValue> = {};
  for (const i of inputs) {
    const v = values[i.key];
    if (v === undefined || v === '' || (typeof v === 'string' && !v.trim())) {
      if (i.required) return null;
      continue;
    }
    if (i.type === 'number') {
      const n = typeof v === 'number' ? v : Number(v);
      if (!Number.isFinite(n)) return null;
      out[i.key] = n;
    } else out[i.key] = typeof v === 'string' ? v.trim() : v;
  }
  return out;
}
