import { useId, useMemo, useRef, useState, type ChangeEvent, type KeyboardEvent, type SyntheticEvent } from 'react';
import { activeToken, filterSuggestions, insertSuggestion, isKnownPath, pathsIn, type TemplateSuggestion } from '@desk/ui-core';

type Box = HTMLInputElement | HTMLTextAreaElement;

/** A text field with `{{…}}` autocomplete (inputs, upstream steps' results, the run), showing the paths it uses as chips (spec §8.2). */
export function TemplateField(o: {
  id: string;
  label: string;
  value: string;
  onChange(value: string): void;
  suggestions: TemplateSuggestion[];
  multiline?: boolean;
  rows?: number;
  placeholder?: string;
  hint?: string;
  errors?: string[];
  /** Keeps the label for screen readers only (a row of a list). */
  labelHidden?: boolean;
}) {
  const box = useRef<Box | null>(null);
  const [caret, setCaret] = useState<number | null>(null);
  const [active, setActive] = useState(0);
  const listId = useId();
  const query = caret === null ? null : (activeToken(o.value, caret)?.query ?? null);
  const options = useMemo(() => (query === null ? [] : filterSuggestions(o.suggestions, query)), [query, o.suggestions]);
  const open = options.length > 0;

  const choose = (s: TemplateSuggestion) => {
    const next = insertSuggestion(o.value, caret ?? o.value.length, s);
    o.onChange(next.text);
    setCaret(s.open ? next.caret : null);
    setActive(0);
    requestAnimationFrame(() => {
      box.current?.focus();
      box.current?.setSelectionRange(next.caret, next.caret);
    });
  };
  const track = (e: SyntheticEvent<Box>) => setCaret(e.currentTarget.selectionStart);
  const onChange = (e: ChangeEvent<Box>) => {
    o.onChange(e.target.value);
    setCaret(e.target.selectionStart);
    setActive(0);
  };
  const onKeyDown = (e: KeyboardEvent<Box>) => {
    if (!open) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((a) => (a + (e.key === 'ArrowDown' ? 1 : options.length - 1)) % options.length);
    } else if (e.key === 'Enter' || e.key === 'Tab') {
      e.preventDefault();
      const s = options[Math.min(active, options.length - 1)];
      if (s) choose(s);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      setCaret(null);
    }
  };
  const shared = {
    id: o.id,
    value: o.value,
    placeholder: o.placeholder,
    role: 'combobox',
    'aria-expanded': open,
    'aria-controls': listId,
    'aria-autocomplete': 'list' as const,
    onChange,
    onKeyDown,
    onKeyUp: track,
    onClick: track,
    onBlur: () => setTimeout(() => setCaret(null), 150),
  };
  const used = pathsIn(o.value);
  return (
    <div className="field auto-template">
      <label htmlFor={o.id} className={o.labelHidden ? 'sr-only' : undefined}>
        {o.label}
      </label>
      {o.multiline ? (
        <textarea {...shared} className="textarea" rows={o.rows ?? 4} ref={(el) => void (box.current = el)} />
      ) : (
        <input {...shared} className="input" ref={(el) => void (box.current = el)} />
      )}
      {open ? (
        <ul id={listId} role="listbox" className="auto-suggest">
          {options.map((s, i) => (
            <li
              key={s.path}
              role="option"
              aria-selected={i === active}
              onMouseDown={(e) => {
                e.preventDefault();
                choose(s);
              }}
            >
              <span className="mono">{s.open ? `${s.path}…` : s.path}</span>
              <span className="muted small">{s.label}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {used.length ? (
        <div className="auto-chips">
          {used.map((p) => {
            const known = isKnownPath(p, o.suggestions);
            return (
              <span key={p} className={known ? 'auto-tpl' : 'auto-tpl unknown'} title={known ? undefined : 'Not available here'}>
                {`{{${p}}}`}
              </span>
            );
          })}
        </div>
      ) : null}
      {o.errors?.length ? (
        o.errors.map((e) => (
          <p key={e} className="field-error" role="alert">
            {e}
          </p>
        ))
      ) : o.hint ? (
        <p className="field-hint">{o.hint}</p>
      ) : null}
    </div>
  );
}
