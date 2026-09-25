import { useMemo, useState } from 'react';
import { activityOf, layoutMap, plural } from '@desk/ui-core';
import { Button } from '../components/Button';
import { EmptyState } from '../components/EmptyState';
import { Sheet } from '../components/Sheet';
import { navigate } from '../router';
import { ProjectForm } from '../screens/ProjectForm';
import { useGlobal } from '../state/global';
import { useNow } from '../state/now';
import { MapCanvas } from './MapCanvas';
import { OrbitMap } from './OrbitMap';
import { ProjectList } from './ProjectList';
import { TerritoryInspector } from './TerritoryInspector';
import './map.css';

const VIEW_KEY = 'desk.mapView';
const readView = (): 'map' | 'list' => {
  try {
    return localStorage.getItem(VIEW_KEY) === 'list' ? 'list' : 'map';
  } catch {
    return 'map';
  }
};

function Legend() {
  return (
    <div className="map-legend">
      <span><span className="legend-disc legend-running" />Running</span>
      <span><span className="legend-disc legend-waiting" />Waiting</span>
      <span><span className="legend-disc legend-idle" />Idle</span>
      <span><span className="legend-dot legend-dot-running" />Thread running</span>
      <span><span className="legend-dot legend-dot-waiting" />Waiting</span>
      <span><span className="legend-dot legend-dot-needs" />Needs you</span>
      <span className="muted">Disc size = activity</span>
    </div>
  );
}

export function MapScreen({ newProject }: { newProject: boolean }) {
  const overview = useGlobal((s) => s.overview);
  const attention = useGlobal((s) => s.attention);
  const health = useGlobal((s) => s.health);
  const status = useGlobal((s) => s.connection.status);
  const proxy = useGlobal((s) => s.system.proxy);
  const now = useNow();
  const [view, setView] = useState(readView);
  const [picked, setPicked] = useState<string | null>(null);
  const [creating, setCreating] = useState(newProject);

  const running = overview.reduce((n, p) => n + p.threads.filter((t) => t.status === 'running').length, 0);
  const busy = overview.filter((p) => p.threads.some((t) => t.status === 'running')).length;
  const selected = overview.find((p) => p.project.id === picked) ?? overview.find((p) => p.attention_count > 0) ?? overview[0];
  const selectedItems = useMemo(() => attention.filter((i) => i.project_id === selected?.project.id), [attention, selected?.project.id]);
  const mapProjects = useMemo(() => overview.map((p) => ({ id: p.project.id, activity: activityOf(p), threads: p.threads })), [overview]);

  const choose = (v: 'map' | 'list') => {
    setView(v);
    try {
      localStorage.setItem(VIEW_KEY, v);
    } catch {
      // A convenience only.
    }
  };
  const close = () => {
    setCreating(false);
    if (newProject) navigate({ name: 'map' });
  };

  return (
    <div className="map-screen">
      <div className="map-head">
        <h1 className="title">Projects</h1>
        <p className="subtitle">
          {plural(running, 'thread')} running in {plural(busy, 'project')}. {attention.length === 1 ? '1 thing is' : `${attention.length} things are`} waiting on you.
        </p>
        <div className="segmented" role="group" aria-label="View">
          <button type="button" aria-pressed={view === 'map'} onClick={() => choose('map')}>
            Map
          </button>
          <button type="button" aria-pressed={view === 'list'} onClick={() => choose('list')}>
            List
          </button>
        </div>
      </div>

      {!overview.length ? (
        <div className="page">
          <EmptyState title="No projects yet" action={<Button onClick={() => setCreating(true)}>Create a project</Button>}>
            A project is a goal Desk works toward with its own threads, library and memory.
          </EmptyState>
        </div>
      ) : view === 'map' ? (
        <>
          <MapCanvas label="Projects map">
            {(size) => {
              const width = size.width > 900 ? size.width - 380 : size.width;
              const layout = layoutMap(mapProjects, width, size.height);
              return (
                <OrbitMap
                  layout={layout}
                  projects={overview}
                  attention={attention}
                  selected={selected?.project.id ?? null}
                  onSelect={setPicked}
                  width={width}
                  height={size.height}
                  now={now}
                  sunLabel={[`${health?.version ?? ''} · ${status === 'live' ? 'running' : status}`, `proxy ${proxy} · ${plural(running, 'thread')} running`]}
                  sunAria={`deskd, ${status === 'live' ? 'running' : status}, model proxy ${proxy}`}
                />
              );
            }}
          </MapCanvas>
          <Legend />
          {selected ? <TerritoryInspector p={selected} items={selectedItems} now={now} /> : null}
        </>
      ) : (
        <div className="page">
          <ProjectList projects={overview} />
        </div>
      )}

      <button type="button" className="new-project-btn" onClick={() => setCreating(true)}>
        <span aria-hidden="true">+</span> New project <span className="muted">· brief Desk in a sentence</span>
      </button>

      {creating ? (
        <Sheet title="New project" onClose={close}>
          <ProjectForm
            onCancel={close}
            onCreated={(id) => {
              setCreating(false);
              navigate({ name: 'project', id, tab: 'conversation' });
            }}
          />
        </Sheet>
      ) : null}
    </div>
  );
}
