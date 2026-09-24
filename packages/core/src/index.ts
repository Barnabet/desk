export { newId } from './ids';
export { ConflictError, DeskError, NotFoundError, ValidationError, type DeskErrorCode } from './errors';
export { openDb, type Db, type Tx } from './db/open';
export { EventStore, type ListQuery, type StreamItem } from './events/store';
export {
  getAgent,
  getApproval,
  getProject,
  getUsageByProject,
  getUsageTotals,
  getDeskAgent,
  getSource,
  hasProjectEventAfter,
  lastEvent,
  lastProjectEvent,
  lastSeq,
  lastStallFor,
  lastProjectSeq,
  listActiveThreads,
  listAgents,
  listApprovals,
  listLiveAgents,
  listProjects,
  listSources,
  listThreads,
  pendingApprovalsFor,
  type AgentRow,
  type ApprovalRow,
  type ApprovalStatus,
  type ProjectRow,
  type SourceRow,
  type UsageRow,
} from './state/queries';
export { listAttention } from './state/attention';
export { listOverview } from './state/overview';
export { listWorkspace, resolveWorkspaceFile, threadDiff } from './workspaces/inspect';
export { loadModelConfig, modelConfigFromEnv, modelConfigFromFile, normalizeBaseURL, parseEnvFile, type ModelConfig } from './model/config';
export { KEYCHAIN_ACCOUNT, KEYCHAIN_SERVICE, macKeychain, resolveModelEndpoint, testModelEndpoint, type EndpointSource, type Keychain, type KeychainExec } from './model/endpoint';
export { createSwitchableAdapter, type SwitchableAdapter } from './model/switchable';
export { DEFAULT_MODEL_ID, effortFor, ModelRegistry, SEED_MODELS, STANDARD_EFFORTS } from './model/registry';
export { classifyModelError, ModelError, type ModelErrorKind } from './model/errors';
export { createModelAdapter } from './model/adapter';
export { abortableSleep, withRetry, type RetryOptions } from './model/retry';
export type { ChatMessage, CompletionRequest, CompletionResult, CompletionUsage, ModelAdapter, ToolSpec } from './model/types';
export { defineTool, ToolDenied, type PolicySubject, type Tool, type ToolContext, type ToolGate, type ToolOutput, type ToolResult } from './tools/types';
export { executeToolCall, MAX_TOOL_OUTPUT_CHARS, prepareToolCall, runPreparedTool, toToolSpecs, type PreparedCall } from './tools/registry';
export { resolveInside } from './tools/paths';
export { runProcess, type ProcessResult } from './tools/process';
export { fileTools } from './tools/fs';
export { bashTool, scrubbedEnv, withSkillEnv } from './tools/bash';
export { completeTool } from './tools/thread';
export { buildSandboxProfile, detectSandbox, NO_SANDBOX, shellInvocation, type SandboxSpec } from './tools/sandbox';
export { bashBackgroundTool, bashKillTool, bashOutputTool, JobManager, jobTools, type JobSnapshot, type JobStatus } from './tools/jobs';
export {
  braveProvider,
  createWebSearchTool,
  defaultSearchProvider,
  duckDuckGoProvider,
  webFetchTool,
  webSearchTool,
  webTools,
  type SearchProvider,
  type SearchResult,
} from './tools/web';
export { evaluatePolicy, globToRegExp, type PolicyDecision } from './policy/evaluate';
export { buildToolContext, type ToolEnvironment } from './agent/context';
export { buildConversation, buildCurrentConversation, CHECKPOINT_HEADER } from './agent/transcript';
export { chooseSplit, compactionPrompt, shouldCompact } from './agent/compaction';
export { drainInbox, hasPendingInbox } from './agent/inbox';
export { deskSystemPrompt, threadSystemPrompt, type PromptContext } from './agent/prompts';
export { runAgent, SHUTDOWN_REASON, type RunDeps, type RunOutcome } from './agent/run';
export { Scheduler, type Job, type SchedulerOptions } from './runtime/scheduler';
export { Runtime, type RuntimeOptions } from './runtime/runtime';
export { deskToolsFor, threadToolsFor, toolsForRole } from './runtime/toolsets';
export { gitCommitTool, gitDiffTool, gitPushTool, gitStatusTool, gitTools, openPrTool } from './tools/git';
export { bashReadonlyTool } from './tools/bash';
export { memorySearchTool, memoryTools, memoryWriteTool } from './tools/memory';
export { libraryListTool, libraryPublishTool, libraryReadTool, libraryTools } from './tools/library';
export { messageDeskTool, threadCoordinationTools, waitForReplyTool } from './tools/thread';
export { deskCoordinationTools } from './tools/desk';
export { activeMemory, getMemory, memoryDigest, searchMemory, type MemoryRow } from './memory/memory';
export { listArtifacts, type ArtifactRow } from './library/library';
export { formatPlan, getPlan, type PlanRow } from './coordination/plan';
export { formatThreadLine, formatThreadSummary, renderTranscript } from './coordination/render';
export { createWorkspace, removeWorkspace, threadBranchName } from './workspaces/workspaces';
export { SkillStore, parseSkillMd, serializeSkillMd, type SkillDetail, type SkillSummary, type SkillSaveInput } from './skills/store';
export { skillUseTools, skillAuthoringTools, renderSkill } from './tools/skills';
export { CatalogService, catalogMarker, httpFetch, loadCatalog, type CatalogFetch, type CatalogRuntimes, type CatalogServiceOptions, type SkillRef } from './catalog/service';
export { extractSubtree, DEFAULT_CAPS, type ExtractCaps, type ExtractedFile } from './catalog/tar';
export { readTree, treeDigest } from './catalog/digest';
export { isScript, scanSkill } from './catalog/review';
export { SkillRuntimes, type Exec, type SkillEnv, type SkillEnvProvider, type SkillRuntimesOptions } from './catalog/runtimes';
