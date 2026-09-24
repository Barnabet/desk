import type {
  AddSourceRequest,
  AttentionResponse,
  CatalogInstallRequest,
  CatalogInstallResult,
  CatalogItem,
  CatalogReview,
  RuntimeState,
  RuntimesReport,
  CreateProjectRequest,
  DaemonConfig,
  DaemonConfigPatch,
  HealthResponse,
  LibraryUploadRequest,
  MemoryUpdateRequest,
  MemoryWriteRequest,
  ModelEndpointPutRequest,
  ModelEndpointStatus,
  ModelEndpointTestResult,
  ModelInfo,
  ProjectSummary,
  SkillImportRequest,
  SkillWriteRequest,
  StreamServerMessage,
  ThreadDiff,
  UpdateProjectRequest,
  UsageResponse,
  WorkspaceEntry,
  EventType,
} from '@desk/protocol';
import { ApiError, DaemonUnavailable } from './errors';
import type {
  AgentRow,
  ApprovalRow,
  ArtifactRow,
  Credentials,
  EventPage,
  MemoryRow,
  PlanRow,
  ProjectOverview,
  ProjectRow,
  ProjectUsage,
  SkillDetail,
  SkillHistoryEntry,
  SkillSaveResult,
  SkillSummary,
  SourceRow,
} from './types';

export type ClientOptions = Credentials & {
  /** Called once after a 401: return fresh credentials (e.g. re-read daemon.json), or null. */
  refresh?: () => Promise<Credentials | null>;
  fetch?: typeof fetch;
};

/** Where a skill lives: a project (resolving project first, then global, as agents do) or, when omitted, global. */
export type SkillScopeRef = { projectId?: string };

const enc = encodeURIComponent;
const encPath = (p: string) => p.split('/').map(enc).join('/');
const page = (p?: { after?: number; limit?: number }) => {
  const q = new URLSearchParams();
  if (p?.after !== undefined) q.set('after', String(p.after));
  if (p?.limit !== undefined) q.set('limit', String(p.limit));
  const s = q.toString();
  return s ? `?${s}` : '';
};

/** Typed client for the deskd HTTP API. Environment-neutral: uses the global fetch and WebSocket. */
export class DeskClient {
  private creds: Credentials;

  constructor(private readonly o: ClientOptions) {
    this.creds = { baseUrl: o.baseUrl, token: o.token };
  }

  get baseUrl(): string {
    return this.creds.baseUrl;
  }

  credentials(): Credentials {
    return { ...this.creds };
  }

  /** Asks `refresh` for new credentials; true when they changed. */
  async refresh(): Promise<boolean> {
    const next = await this.o.refresh?.();
    if (!next) return false;
    const changed = next.baseUrl !== this.creds.baseUrl || next.token !== this.creds.token;
    this.creds = next;
    return changed;
  }

  async request<T = unknown>(method: string, path: string, body?: unknown, opts: { raw?: boolean } = {}): Promise<T> {
    const send = async (): Promise<Response | null> => {
      try {
        return await (this.o.fetch ?? fetch)(`${this.creds.baseUrl}/v1${path}`, {
          method,
          headers: { authorization: `Bearer ${this.creds.token}`, ...(body !== undefined ? { 'content-type': 'application/json' } : {}) },
          ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
        });
      } catch {
        return null;
      }
    };
    let res = await send();
    // The daemon may have restarted with a new token or port: re-read credentials once and retry.
    if ((!res || res.status === 401) && (await this.refresh())) res = await send();
    if (!res) throw new DaemonUnavailable(this.creds.baseUrl);
    if (opts.raw && res.ok) return new Uint8Array(await res.arrayBuffer()) as T;
    const isJson = (res.headers.get('content-type') ?? '').includes('json');
    const data: unknown = isJson ? await res.json() : await res.text();
    if (!res.ok) {
      const e = (data as { error?: { code?: string; message?: string; details?: unknown } } | null)?.error;
      throw new ApiError(res.status, e?.code ?? 'error', e?.message ?? `HTTP ${res.status}`, e?.details);
    }
    return data as T;
  }

  get = <T = any>(path: string) => this.request<T>('GET', path);
  post = <T = any>(path: string, body: unknown = {}) => this.request<T>('POST', path, body);
  patch = <T = any>(path: string, body: unknown) => this.request<T>('PATCH', path, body);
  put = <T = any>(path: string, body: unknown) => this.request<T>('PUT', path, body);
  del = <T = any>(path: string) => this.request<T>('DELETE', path);
  private raw = (path: string) => this.request<Uint8Array>('GET', path, undefined, { raw: true });

  /** One-shot subscription (no reconnect); resolves once replay is done with a closer. Prefer DeskStream in apps. */
  stream(projectId: string, afterSeq: number, onMessage: (m: StreamServerMessage) => void): Promise<() => void> {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(`${this.creds.baseUrl.replace(/^http/, 'ws')}/v1/stream?token=${enc(this.creds.token)}`);
      let ready = false;
      ws.onopen = () => ws.send(JSON.stringify({ subscribe: { project_id: projectId, after_seq: afterSeq } }));
      ws.onmessage = (e) => {
        const m = JSON.parse(String(e.data)) as StreamServerMessage;
        onMessage(m);
        if (m.kind === 'ready' && !ready) {
          ready = true;
          resolve(() => ws.close());
        }
      };
      ws.onerror = () => {
        if (!ready) reject(new Error('Event stream connection failed'));
      };
    });
  }

  health = () => this.get<HealthResponse>('/health');
  overview = () => this.get<ProjectSummary[]>('/overview');
  usage = (since?: string) => this.get<UsageResponse>(`/usage${since ? `?since=${enc(since)}` : ''}`);

  projects = {
    list: (all = false) => this.get<ProjectRow[]>(`/projects${all ? '?all=1' : ''}`),
    create: (req: CreateProjectRequest) => this.post<ProjectOverview>('/projects', req),
    get: (id: string) => this.get<ProjectOverview>(`/projects/${enc(id)}`),
    update: (id: string, patch: UpdateProjectRequest) => this.patch<ProjectRow>(`/projects/${enc(id)}`, patch),
    archive: (id: string) => this.post<{ ok: true }>(`/projects/${enc(id)}/archive`),
    addSource: (id: string, req: AddSourceRequest) => this.post<SourceRow>(`/projects/${enc(id)}/sources`, req),
    removeSource: (id: string, sourceId: string) => this.del<{ ok: true }>(`/projects/${enc(id)}/sources/${enc(sourceId)}`),
    send: (id: string, text: string) => this.post<{ ok: true }>(`/projects/${enc(id)}/messages`, { text }),
    chat: (id: string, p?: { after?: number; limit?: number }) => this.get<EventPage>(`/projects/${enc(id)}/chat${page(p)}`),
    plan: (id: string) => this.get<PlanRow | null>(`/projects/${enc(id)}/plan`),
    usage: (id: string) => this.get<ProjectUsage>(`/projects/${enc(id)}/usage`),
    events: (id: string, q?: { after?: number; limit?: number; types?: EventType[] }) => {
      const qs = page(q);
      const types = q?.types?.length ? `${qs ? '&' : '?'}types=${q.types.join(',')}` : '';
      return this.get<EventPage>(`/projects/${enc(id)}/events${qs}${types}`);
    },
  };

  threads = {
    list: (projectId: string, all = false) => this.get<AgentRow[]>(`/projects/${enc(projectId)}/threads${all ? '?all=1' : ''}`),
    get: (id: string) => this.get<AgentRow>(`/threads/${enc(id)}`),
    transcript: (id: string, p?: { after?: number; limit?: number }) => this.get<EventPage>(`/threads/${enc(id)}/transcript${page(p)}`),
    send: (id: string, text: string) => this.post<{ ok: true }>(`/threads/${enc(id)}/messages`, { text }),
    stop: (id: string) => this.post<{ ok: true }>(`/threads/${enc(id)}/stop`),
    archive: (id: string) => this.post<{ ok: true }>(`/threads/${enc(id)}/archive`),
    diff: (id: string) => this.get<ThreadDiff>(`/threads/${enc(id)}/diff`),
    files: (id: string, path = '') => this.get<WorkspaceEntry[]>(`/threads/${enc(id)}/files${path ? `?path=${enc(path)}` : ''}`),
    file: (id: string, path: string) => this.raw(`/threads/${enc(id)}/files/raw/${encPath(path)}`),
  };

  approvals = {
    list: (projectId: string, status?: 'pending' | 'approved' | 'denied') =>
      this.get<ApprovalRow[]>(`/projects/${enc(projectId)}/approvals${status ? `?status=${status}` : ''}`),
    resolve: (id: string, decision: 'approved' | 'denied', note?: string) =>
      this.post<{ ok: true }>(`/approvals/${enc(id)}/resolve`, { decision, ...(note ? { note } : {}) }),
  };

  attention = {
    list: (projectId?: string) => this.get<AttentionResponse>(`/attention${projectId ? `?project_id=${enc(projectId)}` : ''}`),
    dismiss: (id: string) => this.post<{ ok: true }>(`/attention/${enc(id)}/dismiss`),
  };

  memory = {
    list: (projectId: string, q?: string) => this.get<MemoryRow[]>(`/projects/${enc(projectId)}/memory${q ? `?q=${enc(q)}` : ''}`),
    add: (projectId: string, req: MemoryWriteRequest) => this.post<MemoryRow>(`/projects/${enc(projectId)}/memory`, req),
    correct: (projectId: string, memoryId: string, req: MemoryUpdateRequest) =>
      this.patch<MemoryRow>(`/projects/${enc(projectId)}/memory/${enc(memoryId)}`, req),
    remove: (projectId: string, memoryId: string) => this.del<{ ok: true }>(`/projects/${enc(projectId)}/memory/${enc(memoryId)}`),
  };

  library = {
    list: (projectId: string) => this.get<ArtifactRow[]>(`/projects/${enc(projectId)}/library`),
    upload: (projectId: string, req: LibraryUploadRequest) => this.post<ArtifactRow>(`/projects/${enc(projectId)}/library`, req),
    file: (projectId: string, path: string) => this.raw(`/projects/${enc(projectId)}/library/file/${encPath(path)}`),
  };

  private skillBase = (s: SkillScopeRef) => (s.projectId ? `/projects/${enc(s.projectId)}/skills` : '/skills');

  skills = {
    list: (s: SkillScopeRef) => this.get<SkillSummary[]>(this.skillBase(s)),
    get: (s: SkillScopeRef, name: string) => this.get<SkillDetail>(`${this.skillBase(s)}/${enc(name)}`),
    file: (s: SkillScopeRef, name: string, path: string) => this.raw(`${this.skillBase(s)}/${enc(name)}/files/${encPath(path)}`),
    save: (s: SkillScopeRef, name: string, req: SkillWriteRequest) => this.put<SkillSaveResult>(`${this.skillBase(s)}/${enc(name)}`, req),
    remove: (s: SkillScopeRef, name: string) => this.del<{ ok: true }>(`${this.skillBase(s)}/${enc(name)}`),
    history: (s: SkillScopeRef, name: string) => this.get<SkillHistoryEntry[]>(`${this.skillBase(s)}/${enc(name)}/history`),
    restore: (s: SkillScopeRef, name: string, version: number) => this.post<SkillSaveResult>(`${this.skillBase(s)}/${enc(name)}/restore`, { version }),
    import: (s: SkillScopeRef, req: SkillImportRequest) => this.post<SkillSaveResult>(`${this.skillBase(s)}/import`, req),
    version: (s: SkillScopeRef, name: string, v: number) => this.get<SkillDetail>(`${this.skillBase(s)}/${enc(name)}/versions/${v}`),
    versionFile: (s: SkillScopeRef, name: string, v: number, path: string) => this.raw(`${this.skillBase(s)}/${enc(name)}/versions/${v}/files/${encPath(path)}`),
  };

  catalog = {
    list: () => this.get<CatalogItem[]>('/catalog'),
    prepare: (id: string) => this.request<CatalogReview>('POST', `/catalog/${enc(id)}/prepare`),
    install: (id: string, req: CatalogInstallRequest = {}) => this.post<CatalogInstallResult>(`/catalog/${enc(id)}/install`, req),
    /** Rebuilds a catalog skill's runtime (after a failure). */
    retryRuntime: (s: SkillScopeRef, name: string) => this.request<{ state: RuntimeState; reason: string | null }>('POST', `${this.skillBase(s)}/${enc(name)}/runtime/retry`),
    runtimes: () => this.get<RuntimesReport>('/system/runtimes'),
    cleanupRuntimes: () => this.request<{ removed: number; bytes: number }>('POST', '/system/runtimes/cleanup'),
  };

  models = {
    list: () => this.get<ModelInfo[]>('/models'),
    replace: (list: ModelInfo[]) => this.put<ModelInfo[]>('/models', list),
  };

  config = {
    get: () => this.get<DaemonConfig>('/config'),
    patch: (p: DaemonConfigPatch) => this.patch<DaemonConfig>('/config', p),
    endpoint: () => this.get<ModelEndpointStatus>('/config/model-endpoint'),
    saveEndpoint: (req: ModelEndpointPutRequest) => this.put<ModelEndpointStatus>('/config/model-endpoint', req),
    testEndpoint: (req?: ModelEndpointPutRequest) =>
      req ? this.post<ModelEndpointTestResult>('/config/model-endpoint/test', req) : this.request<ModelEndpointTestResult>('POST', '/config/model-endpoint/test'),
  };
}
