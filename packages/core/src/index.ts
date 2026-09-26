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
  listServices,
  listSources,
  listThreads,
  getService,
  findService,
  pendingApprovalsFor,
  type AgentRow,
  type ApprovalRow,
  type ApprovalStatus,
  type ProjectRow,
  type ServiceRow,
  type SourceRow,
  type UsageRow,
} from './state/queries';
export { listAttention } from './state/attention';
export { listOverview } from './state/overview';
export { listWorkspace, resolveWorkspaceFile, threadDiff } from './workspaces/inspect';
export { loadModelConfig, modelConfigFromEnv, modelConfigFromFile, modelCredentialsFile, normalizeBaseURL, parseEnvFile, type ModelConfig } from './model/config';
export { KEYCHAIN_ACCOUNT, KEYCHAIN_SERVICE, macKeychain, resolveModelEndpoint, testModelEndpoint, type EndpointSource, type Keychain, type KeychainExec } from './model/endpoint';
export { createSwitchableAdapter, type SwitchableAdapter } from './model/switchable';
export { DEFAULT_MODEL_ID, effortFor, ModelRegistry, SEED_MODELS, STANDARD_EFFORTS } from './model/registry';
export { classifyModelError, imageRefusal, ModelError, type ModelErrorKind } from './model/errors';
export { createModelAdapter } from './model/adapter';
export { abortableSleep, withRetry, type RetryOptions } from './model/retry';
export type { ChatMessage, ContentPart, CompletionRequest, CompletionResult, CompletionUsage, ModelAdapter, ToolSpec } from './model/types';
export { defineTool, ToolDenied, type PolicySubject, type Tool, type ToolContext, type ToolGate, type ToolOutput, type ToolResult } from './tools/types';
export { executeToolCall, MAX_TOOL_OUTPUT_CHARS, prepareToolCall, runPreparedTool, toToolSpecs, type PreparedCall } from './tools/registry';
export { resolveInside } from './tools/paths';
export { runProcess, type ProcessResult } from './tools/process';
export { fileTools } from './tools/fs';
export { MAX_CALL_BYTES, MAX_IMAGE_BYTES, MAX_IMAGE_SIDE, viewImageTool, visionTools } from './tools/vision';
export { AttachmentStore } from './attachments/store';
export { imageProblem, imageTokens, readDataUrlInfo, readImageInfo, sniffImageType, type ImageInfo } from './attachments/image';
export { bashTool, scrubbedEnv, withSkillEnv } from './tools/bash';
export { completeTool } from './tools/thread';
export { buildSandboxProfile, detectSandbox, NO_SANDBOX, sandboxGuard, shellInvocation, type SandboxGuard, type SandboxSpec } from './tools/sandbox';
export { bashBackgroundTool, bashKillTool, bashOutputTool, JobManager, jobTools, type JobSnapshot, type JobStatus } from './tools/jobs';
export { serviceTools, SERVICE_START_WAIT_MS } from './tools/services';
export { detectLoopbackUrl, ServiceProcesses, stripAnsi, tailLog } from './services/manager';
export { formatServiceLine } from './coordination/render';
export {
  bingProvider,
  braveProvider,
  createWebFetchTool,
  createWebSearchTool,
  defaultSearchProvider,
  duckDuckGoProvider,
  fallbackSearchProvider,
  isPublicHost,
  marginaliaProvider,
  webFetchTool,
  webSearchTool,
  webTools,
  type SearchProvider,
  type SearchResult,
  type WebFetchOptions,
} from './tools/web';
export { evaluatePolicy, globToRegExp, type PolicyDecision } from './policy/evaluate';
export { buildToolContext, type ToolEnvironment } from './agent/context';
export {
  buildConversation,
  buildCurrentConversation,
  CHECKPOINT_END,
  CHECKPOINT_HEADER,
  IMAGES_HEADER,
  imagesInWindow,
  MAX_IMAGE_BYTES_SHOWN,
  MAX_IMAGES_SHOWN,
  pixelGroups,
  showImages,
  type ConversationImage,
  type ConversationOptions,
  type ImageLoader,
  type ImageWindow,
} from './agent/transcript';
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
export { latestWhatsUp } from './coordination/whatsup';
export { formatThreadLine, formatThreadSummary, renderTranscript } from './coordination/render';
export { createWorkspace, removeWorkspace, threadBranchName } from './workspaces/workspaces';
export { SkillStore, parseSkillMd, serializeSkillMd, type SkillDetail, type SkillSummary, type SkillSaveInput } from './skills/store';
export { BuiltinSkills, builtinEnabled, loadBuiltinsManifest, runtimeKey, type RuntimeSpec } from './skills/builtins';
export { skillUseTools, skillAuthoringTools, renderSkill } from './tools/skills';
export { CatalogService, catalogMarker, httpFetch, loadCatalog, type CatalogFetch, type CatalogRuntimes, type CatalogServiceOptions, type SkillRef } from './catalog/service';
export { extractSubtree, DEFAULT_CAPS, type ExtractCaps, type ExtractedFile } from './catalog/tar';
export { readTree, treeDigest } from './catalog/digest';
export { isScript, scanSkill } from './catalog/review';
export { SkillRuntimes, type Exec, type SkillEnv, type SkillEnvProvider, type SkillRuntimesOptions } from './catalog/runtimes';
