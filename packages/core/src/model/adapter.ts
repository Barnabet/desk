import OpenAI from 'openai';
import type { ToolCall } from '@desk/protocol';
import { newId } from '../ids';
import type { ModelConfig } from './config';
import { classifyModelError } from './errors';
import type { ModelRegistry } from './registry';
import type { ChatMessage, CompletionUsage, ModelAdapter } from './types';

export function estimateUsage(messages: ChatMessage[], content: string, toolCalls: ToolCall[]): CompletionUsage {
  return {
    prompt_tokens: Math.ceil(JSON.stringify(messages).length / 4),
    completion_tokens: Math.ceil((content.length + JSON.stringify(toolCalls).length) / 4),
    estimated: true,
  };
}

export function createModelAdapter(config: ModelConfig, registry: ModelRegistry): ModelAdapter {
  const client = new OpenAI({ baseURL: config.baseURL, apiKey: config.apiKey, maxRetries: 0, timeout: 10 * 60_000 });

  return {
    async health() {
      try {
        await client.models.list({ timeout: 5000 });
        return true;
      } catch {
        return false;
      }
    },
    async complete(req, { signal, onText } = {}) {
      const info = registry.get(req.model);
      try {
        const stream = await client.chat.completions.create(
          {
            model: req.model,
            messages: req.messages,
            stream: true,
            stream_options: { include_usage: true },
            ...(req.tools.length ? { tools: req.tools } : {}),
            ...(req.reasoningEffort && info.supports_reasoning_effort ? { reasoning_effort: req.reasoningEffort } : {}),
          },
          { signal },
        );

        let content = '';
        let finishReason = 'stop';
        let usage: CompletionUsage | undefined;
        const calls = new Map<number, ToolCall>();

        for await (const chunk of stream) {
          if (chunk.usage) {
            const cached = chunk.usage.prompt_tokens_details?.cached_tokens;
            usage = {
              prompt_tokens: chunk.usage.prompt_tokens,
              completion_tokens: chunk.usage.completion_tokens,
              ...(cached ? { cached_tokens: cached } : {}),
              estimated: false,
            };
          }
          const choice = chunk.choices[0];
          if (!choice) continue;
          if (choice.finish_reason) finishReason = choice.finish_reason;
          const delta = choice.delta;
          if (delta?.content) {
            content += delta.content;
            onText?.(delta.content);
          }
          for (const tc of delta?.tool_calls ?? []) {
            const current = calls.get(tc.index) ?? { id: '', name: '', arguments: '' };
            if (tc.id) current.id = tc.id;
            if (tc.function?.name) current.name += tc.function.name;
            if (tc.function?.arguments) current.arguments += tc.function.arguments;
            calls.set(tc.index, current);
          }
        }

        const toolCalls = [...calls.entries()]
          .sort(([a], [b]) => a - b)
          .map(([, c]) => ({ id: c.id || `call_${newId()}`, name: c.name, arguments: c.arguments || '{}' }));

        return {
          content: content.length ? content : null,
          toolCalls,
          finishReason,
          usage: usage ?? estimateUsage(req.messages, content, toolCalls),
        };
      } catch (err) {
        throw classifyModelError(err);
      }
    },
  };
}
