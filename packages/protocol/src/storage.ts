/** Completed managed thread workspaces; bytes count only safely reinstallable dependencies and known tool caches. */
export type WorkspaceStorageReport = {
  retention_hours: number;
  reclaimable_bytes: number;
  workspaces: Array<{ agent_id: string; project_id: string; title: string; bytes: number; directories: number; reason: string | null }>;
};
export type WorkspaceCleanupResult = { removed: number; bytes: number; errors: Array<{ agent_id: string; message: string }> };
