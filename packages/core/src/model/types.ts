import type { ReasoningEffort, ToolCall } from '@desk/protocol';

export type ChatToolCall = { id: string; type: 'function'; function: { name: string; arguments: string } };

/** A part of a user message: text, or an image as a (base64 data) URL. */
export type ContentPart = { type: 'text'; text: string } | { type: 'image_url'; image_url: { url: string } };

export type ChatMessage =
  | { role: 'system'; content: string }
  | { role: 'user'; content: string | ContentPart[] }
  | { role: 'assistant'; content: string | null; tool_calls?: ChatToolCall[] }
  | { role: 'tool'; tool_call_id: string; content: string };

export type ToolSpec = {
  type: 'function';
  function: { name: string; description: string; parameters: Record<string, unknown> };
};

export type CompletionRequest = {
  model: string;
  messages: ChatMessage[];
  tools: ToolSpec[];
  reasoningEffort?: ReasoningEffort;
};

export type CompletionUsage = { prompt_tokens: number; completion_tokens: number; cached_tokens?: number; estimated: boolean };

export type CompletionResult = { content: string | null; toolCalls: ToolCall[]; finishReason: string; usage: CompletionUsage };

export interface ModelAdapter {
  complete(
    req: CompletionRequest,
    opts?: { signal?: AbortSignal; onText?: (delta: string) => void },
  ): Promise<CompletionResult>;
  /** True when the model endpoint answers (used to detect the end of a proxy outage). */
  health?(): Promise<boolean>;
}
