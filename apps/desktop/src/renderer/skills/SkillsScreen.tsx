import { useMemo, useState } from 'react';
import type { SkillDetail } from '@desk/client';
import { call } from '../bridge';
import { Button } from '../components/Button';
import { EmptyState } from '../components/EmptyState';
import { Field } from '../components/Field';
import { Sheet } from '../components/Sheet';
import { toast, toastError } from '../components/Toast';
import { projectTone } from '../map/OrbitMap';
import { replaceRoute } from '../router';
import { useGlobal } from '../state/global';
import { AskDesk } from './AskDesk';
import { parseSkillKey, skillKey, useSkills, type SkillRef } from './data';
import { SkillEditor } from './SkillEditor';
import { SkillList } from './SkillList';
import { SkillPanel } from './SkillPanel';
import { SkillsMapView } from './SkillsMapView';
import './skills.css';

type View = 'map' | 'list';
type Filter = 'all' | 'used' | 'shadowed';

function useView(): [View, (v: View) => void] {
  const [v, setV] = useState<View>(() => {
    try {
      return localStorage.getItem('desk.skillsView') === 'list' ? 'list' : 'map';
    } catch {
      return 'map';
    }
  });
  return [
    v,
    (next) => {
      setV(next);
      try {
        localStorage.setItem('desk.skillsView', next);
      } catch {
        // A convenience only.
      }
    },
  ];
}

function ImportSheet(o: { path: string; projects: Array<{ id: string; name: string }>; onDone(ref: SkillRef): void; onClose(): void }) {
  const [scope, setScope] = useState('global');
  const [name, setName] = useState('');
  const [pending, setPending] = useState(false);
  const run = async () => {
    setPending(true);
    try {
      const projectId = scope === 'global' ? undefined : scope;
      const r = await call('skills.import', { ...(projectId ? { projectId } : {}), path: o.path, ...(name.trim() ? { name: name.trim() } : {}) });
      const imported = r.dir.split('/').filter(Boolean).pop() ?? name.trim();
      toast({ tone: 'info', message: `Imported ${imported}.` });
      o.onDone(projectId ? { scope: 'project', projectId, name: imported } : { scope: 'global', name: imported });
    } catch (err) {
      toastError(err);
    } finally {
      setPending(false);
    }
  };
  return (
    <Sheet
      title="Import a skill"
      onClose={o.onClose}
      footer={
        <>
          <Button onClick={o.onClose}>Cancel</Button>
          <Button variant="primary" pending={pending} onClick={() => void run()}>
            Import
          </Button>
        </>
      }
    >
      <p className="small">
        From <span className="mono">{o.path}</span>. The folder needs a SKILL.md. It's copied in; the original stays where it is.
      </p>
      <Field id="import-scope" label="Scope">
        <select id="import-scope" className="select" value={scope} onChange={(e) => setScope(e.target.value)}>
          <option value="global">Global (every project)</option>
          {o.projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name} only
            </option>
          ))}
        </select>
      </Field>
      <Field id="import-name" label="Name (optional)" hint="Defaults to the folder's name.">
        <input id="import-name" className="input mono" value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
    </Sheet>
  );
}

/** Every skill Desk and its threads can use: a map (or list) with global, project and shadowed skills, and what's in use now. */
export function SkillsScreen({ skill }: { skill?: string }) {
  const overview = useGlobal((g) => g.overview);
  const data = useSkills();
  const [view, setView] = useView();
  const [filter, setFilter] = useState<Filter>('all');
  const [editor, setEditor] = useState<{ skill?: { ref: SkillRef; detail: SkillDetail } } | null>(null);
  const [ask, setAsk] = useState<{ name?: string; projectId?: string } | null>(null);
  const [importPath, setImportPath] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  const projects = useMemo(() => overview.map((p) => ({ id: p.project.id, name: p.project.name, tone: projectTone(p) })), [overview]);
  const projectNames = useMemo(() => new Map(projects.map((p) => [p.id, p.name])), [projects]);
  const threadTitles = useMemo(() => new Map(overview.flatMap((p) => p.threads.map((t) => [t.id, t.title ?? 'Thread'] as const))), [overview]);
  const counts = { all: data.nodes.length, used: data.nodes.filter((n) => n.usedBy.length).length, shadowed: data.nodes.filter((n) => n.shadows || n.shadowedIn.length).length };
  const shown = useMemo(
    () => data.nodes.filter((n) => filter === 'all' || (filter === 'used' ? n.usedBy.length > 0 : n.shadows || n.shadowedIn.length > 0)),
    [data.nodes, filter],
  );
  const ref = skill ? parseSkillKey(skill) : null;
  const node = skill ? data.nodes.find((n) => n.key === skill) : undefined;
  const select = (key: string | null) => replaceRoute({ name: 'skills', ...(key ? { skill: key } : {}) });
  const changed = () => {
    setVersion((v) => v + 1);
    void data.refresh();
  };
  const startImport = async () => {
    try {
      const path = await call('app.pickFolder', { purpose: 'skill-import' });
      if (path) setImportPath(path);
    } catch (err) {
      toastError(err);
    }
  };

  return (
    <div className={`skills${ref ? ' with-panel' : ''}`}>
      <div className="skills-main">
        <div className="skills-head">
          <h1 className="title">Skill map</h1>
          <p className="muted">Global skills sit in the middle; project skills live inside their project. Lines show the threads using a skill right now.</p>
          <div className="skills-controls">
            <div className="segmented" role="group" aria-label="View">
              <button type="button" aria-pressed={view === 'map'} onClick={() => setView('map')}>
                Map
              </button>
              <button type="button" aria-pressed={view === 'list'} onClick={() => setView('list')}>
                List
              </button>
            </div>
            <div className="chips" role="group" aria-label="Show">
              {(
                [
                  ['all', 'All'],
                  ['used', 'In use now'],
                  ['shadowed', 'Shadowed'],
                ] as Array<[Filter, string]>
              ).map(([f, label]) => (
                <button key={f} type="button" className="filter-chip" aria-pressed={filter === f} onClick={() => setFilter(f)}>
                  {label} · {counts[f]}
                </button>
              ))}
            </div>
          </div>
        </div>
        {data.status === 'loading' ? (
          <p className="muted skills-body">Loading…</p>
        ) : data.status === 'error' ? (
          <EmptyState title="Couldn't load skills">{data.error}</EmptyState>
        ) : !data.nodes.length ? (
          <div className="skills-body">
            <EmptyState title="No skills yet">Skills are instructions and scripts Desk and its threads reuse. Import one, write one, or ask Desk to build one.</EmptyState>
          </div>
        ) : view === 'map' ? (
          <div className="skills-body map">
            <SkillsMapView nodes={shown} projects={projects} selected={skill ?? null} onSelect={(k) => select(k === skill ? null : k)} />
          </div>
        ) : (
          <div className="skills-body">
            <SkillList nodes={shown} projectNames={projectNames} selected={skill ?? null} onSelect={(k) => select(k === skill ? null : k)} />
          </div>
        )}
        <div className="skills-foot">
          {view === 'map' ? (
            <div className="skills-legend" aria-hidden="true">
              <span>
                <span className="lg-dot global" />
                Global skill
              </span>
              <span>
                <span className="lg-dot project" />
                Project skill
              </span>
              <span>
                <span className="lg-dot shadowed" />
                Shadowed
              </span>
              <span>
                <span className="lg-line run" />
                Used by a running thread
              </span>
              <span>
                <span className="lg-line wait" />
                Waiting
              </span>
              <span>Size = how often it's used</span>
            </div>
          ) : (
            <span />
          )}
          <span className="grow" />
          <Button onClick={() => void startImport()}>Import from ~/.claude/skills</Button>
          <Button onClick={() => setEditor({})}>New skill</Button>
          <Button variant="primary" onClick={() => setAsk({})}>
            + Ask Desk for a new skill
          </Button>
        </div>
      </div>
      {ref ? (
        <SkillPanel
          skill={ref}
          node={node}
          projectNames={projectNames}
          threadTitles={threadTitles}
          version={version + (node?.version ?? 0)}
          onEdit={(detail) => setEditor({ skill: { ref, detail } })}
          onAskDesk={() => setAsk({ name: ref.name, ...(ref.projectId ? { projectId: ref.projectId } : node?.usedBy[0] ? { projectId: node.usedBy[0].projectId } : {}) })}
          onChanged={changed}
          onClose={() => select(null)}
        />
      ) : null}
      {editor ? (
        <SkillEditor
          {...(editor.skill ? { skill: editor.skill } : {})}
          projects={projects}
          onClose={() => setEditor(null)}
          onSaved={(saved) => {
            setEditor(null);
            toast({ tone: 'info', message: `Saved ${saved.name}.` });
            changed();
            select(skillKey(saved));
          }}
        />
      ) : null}
      {ask ? <AskDesk {...(ask.name ? { skillName: ask.name } : {})} projects={projects} {...(ask.projectId ? { defaultProjectId: ask.projectId } : {})} onClose={() => setAsk(null)} /> : null}
      {importPath ? (
        <ImportSheet
          path={importPath}
          projects={projects}
          onClose={() => setImportPath(null)}
          onDone={(r) => {
            setImportPath(null);
            changed();
            select(skillKey(r));
          }}
        />
      ) : null}
    </div>
  );
}
