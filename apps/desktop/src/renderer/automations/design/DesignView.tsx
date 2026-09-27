import { useEffect, useMemo, useRef, useState } from 'react';
import type { AutomationDefinition, AutomationDetail, AutomationLayout, StepKind, ValidationIssue } from '@desk/protocol';
import {
  addStep,
  autoLayout,
  blankDefinition,
  connect,
  diffDefinitions,
  ensureLayout,
  mapIssues,
  moveNodes,
  originText,
  removeSelection,
  STEP_KIND_LABEL,
  whenText,
  type AutomationDoc,
  type GraphSelection,
} from '@desk/ui-core';
import { call, DeskCallError } from '../../bridge';
import { Button } from '../../components/Button';
import { toast, toastError } from '../../components/Toast';
import { navigate } from '../../router';
import { ConflictDialog, type Conflict } from './ConflictDialog';
import { EdgeInspector } from './EdgeInspector';
import { GraphCanvas } from './GraphCanvas';
import { SettingsInspector } from './SettingsInspector';
import { StartInspector } from './StartInspector';
import { StepInspector } from './StepInspector';

const KINDS: StepKind[] = ['script', 'agent', 'ask', 'wait', 'automation', 'tell_desk'];
const KIND_CLASS: Record<StepKind, string> = { script: 'k-script', agent: 'k-agent', ask: 'k-ask', wait: 'k-wait', automation: 'k-automation', tell_desk: 'k-tell' };
type Validation = { errors: ValidationIssue[]; warnings: ValidationIssue[]; next_times: Record<string, string[]> };
const NO_ISSUES: Validation = { errors: [], warnings: [], next_times: {} };

export type DesignProps = { projectId: string; sources: Array<{ id: string; label: string }> } & ({ detail: AutomationDetail; onChange(d: AutomationDetail): void } | { draftName: string });

const docOf = (def: AutomationDefinition, layout: AutomationLayout): AutomationDoc => ({ def, layout: ensureLayout(def, layout) });

/** Design (mockup 2): the canvas, the inspector, the add-step strip, validation, Save and conflicts. */
export function DesignView(o: DesignProps) {
  const detail = 'detail' in o ? o.detail : null;
  const name = detail ? detail.name : (o as { draftName: string }).draftName;
  const [doc, setDoc] = useState<AutomationDoc>(() => (detail ? docOf(detail.definition, detail.layout) : docOf(blankDefinition(name), {})));
  const [saved, setSaved] = useState<{ def: AutomationDefinition; version: number } | null>(() => (detail ? { def: detail.definition, version: detail.version } : null));
  const [selection, setSelection] = useState<GraphSelection>({ kind: 'none' });
  const [validation, setValidation] = useState<Validation>(NO_ISSUES);
  const [checked, setChecked] = useState(false);
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState<Conflict | null>(null);
  const dirty = useMemo(() => !saved || !diffDefinitions(saved.def, doc.def).empty, [saved, doc.def]);
  const issues = useMemo(() => mapIssues(doc.def, validation.errors), [doc.def, validation.errors]);

  // Validation, 400 ms after the last edit; an answer for an older draft is dropped.
  const gen = useRef(0);
  useEffect(() => {
    const g = ++gen.current;
    setChecked(false);
    const t = setTimeout(() => {
      call('automations.validate', { projectId: o.projectId, req: { definition: doc.def, name } })
        .then((r) => {
          if (g !== gen.current) return;
          setValidation(r);
          setChecked(true);
        })
        .catch(() => {});
    }, 400);
    return () => clearTimeout(t);
  }, [doc.def, o.projectId, name]);

  // Another save landed (Desk, the CLI, a restore): adopt it unless the user has edits, which Save will meet as a 409.
  useEffect(() => {
    if (!detail || !saved || detail.version === saved.version || dirty) return;
    setSaved({ def: detail.definition, version: detail.version });
    setDoc((d) => docOf(detail.definition, d.layout));
    // Only a new version matters here.
  }, [detail?.version]);

  // Positions save on their own, 800 ms after the last move.
  const layoutTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(layoutTimer.current), []);
  const saveLayout = (layout: AutomationLayout) => {
    if (!detail) return;
    clearTimeout(layoutTimer.current);
    layoutTimer.current = setTimeout(() => void call('automations.layout', { id: detail.id, layout }).catch(toastError), 800);
  };
  const relayout = (next: AutomationDoc) => {
    setDoc(next);
    saveLayout(next.layout);
  };

  const sel: GraphSelection =
    (selection.kind === 'step' && !doc.def.steps.some((s) => s.id === selection.id)) || (selection.kind === 'edge' && !doc.def.edges[selection.index]) ? { kind: 'none' } : selection;

  const add = (kind: StepKind) => {
    const r = addStep(doc, kind, sel.kind === 'step' ? sel.id : null);
    relayout(r.doc);
    setSelection({ kind: 'step', id: r.id });
  };
  const remove = (steps: string[], edges: number[]) => {
    setDoc(removeSelection(doc, steps, edges));
    setSelection({ kind: 'none' });
  };
  const discard = () => {
    if (!saved) return;
    setDoc(docOf(saved.def, doc.layout));
    setSelection({ kind: 'none' });
  };

  const save = async (base?: number) => {
    const sent = doc.def;
    const changeNote = note.trim() ? { change_note: note.trim() } : {};
    setSaving(true);
    try {
      if (!detail) {
        const r = await call('automations.create', { projectId: o.projectId, req: { name, definition: sent, via: 'editor', ...changeNote } });
        await call('automations.layout', { id: r.automation.id, layout: doc.layout });
        toast({ tone: 'info', message: `Saved ${r.automation.title}. It stays off until you turn it on.` });
        navigate({ name: 'project', id: o.projectId, tab: 'automations', automationId: r.automation.id, view: 'design' });
        return;
      }
      const r = await call('automations.save', { id: detail.id, req: { definition: sent, base_version: base ?? saved!.version, via: 'editor', ...changeNote } });
      void call('automations.layout', { id: detail.id, layout: doc.layout }).catch(() => {});
      setSaved({ def: r.automation.definition, version: r.automation.version });
      setDoc((d) => (d.def === sent ? { ...d, def: r.automation.definition } : d));
      setNote('');
      setConflict(null);
      if ('onChange' in o) o.onChange(r.automation);
      if (r.warnings.length) toast({ tone: 'info', message: `Saved v${r.automation.version} with ${r.warnings.length} warning${r.warnings.length === 1 ? '' : 's'}: see the automation's settings.` });
    } catch (err) {
      if (detail && err instanceof DeskCallError && err.status === 409) {
        try {
          const [theirs, versions] = await Promise.all([call('automations.get', { id: detail.id }), call('automations.versions', { id: detail.id })]);
          setConflict({ theirs, by: versions[0] ? originText(versions[0]) : 'someone' });
        } catch (e) {
          toastError(e);
        }
      } else toastError(err);
    } finally {
      setSaving(false);
    }
  };
  const discardMine = () => {
    if (!conflict) return;
    const { theirs } = conflict;
    setSaved({ def: theirs.definition, version: theirs.version });
    setDoc(docOf(theirs.definition, doc.layout));
    setConflict(null);
    setSelection({ kind: 'none' });
    if ('onChange' in o) o.onChange(theirs);
  };

  const problems = validation.errors.length;
  const status = !checked ? 'Checking…' : problems ? `${problems} problem${problems === 1 ? '' : 's'}` : dirty ? 'Ready to save' : `Saved as v${saved?.version ?? 1}`;
  const startLabel = doc.def.triggers.length ? `${whenText(doc.def.triggers)} · or Run now` : 'Run now only';

  const inspector =
    sel.kind === 'start' ? (
      <StartInspector doc={doc} errors={issues.start} nextTimes={validation.next_times} onChange={setDoc} />
    ) : sel.kind === 'step' ? (
      <StepInspector
        key={sel.id}
        projectId={o.projectId}
        doc={doc}
        stepId={sel.id}
        selfName={name}
        sources={o.sources}
        errors={issues.steps[sel.id] ?? []}
        onChange={setDoc}
        onRenamed={(id) => setSelection({ kind: 'step', id })}
        onDelete={() => remove([sel.id], [])}
      />
    ) : sel.kind === 'edge' ? (
      <EdgeInspector doc={doc} index={sel.index} errors={issues.edges[sel.index] ?? []} onChange={setDoc} onDelete={() => remove([], [sel.index])} />
    ) : (
      <SettingsInspector doc={doc} issues={issues.general} warnings={validation.warnings} onChange={setDoc} />
    );

  return (
    <div className="auto-design">
      <div className="auto-design-main">
        <div className="auto-toolbar">
          <span className={`auto-status${problems ? ' bad' : ''}`} role="status">
            {status}
          </span>
          <span className="grow" />
          <Button size="sm" variant="ghost" onClick={() => relayout({ ...doc, layout: autoLayout(doc.def) })}>
            Tidy up
          </Button>
          {detail && dirty ? (
            <Button size="sm" variant="ghost" onClick={discard}>
              Discard changes
            </Button>
          ) : null}
          <input className="input auto-note" aria-label="Change note" placeholder="What changed (optional)" maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)} />
          <Button variant="primary" size="sm" pending={saving} disabled={!dirty || !checked || problems > 0} title={problems ? 'Fix the problems first' : undefined} onClick={() => void save()}>
            Save
          </Button>
        </div>
        <GraphCanvas
          def={doc.def}
          layout={doc.layout}
          startLabel={startLabel}
          selection={sel}
          onSelect={setSelection}
          issues={issues}
          editable
          onMove={(positions) => relayout(moveNodes(doc, positions))}
          onConnect={(from, to) => {
            const r = connect(doc, from, to);
            if ('error' in r) toast({ tone: 'error', message: r.error });
            else setDoc(r.doc);
          }}
          onDelete={({ steps, edges }) => remove(steps, edges)}
        />
        <div className="auto-strip" role="toolbar" aria-label="Add a step">
          <span className="muted small">Add a step</span>
          {KINDS.map((k) => (
            <button key={k} type="button" className={`auto-add ${KIND_CLASS[k]}`} onClick={() => add(k)}>
              {STEP_KIND_LABEL[k]}
            </button>
          ))}
        </div>
      </div>
      {inspector}
      {conflict ? <ConflictDialog conflict={conflict} mine={doc.def} saving={saving} onSaveMine={() => void save(conflict.theirs.version)} onDiscardMine={discardMine} onClose={() => setConflict(null)} /> : null}
    </div>
  );
}
