import OpenAI from 'openai';
import { imageTokens, readDataUrlInfo } from '../attachments/image';
import type { ToolCall } from '@desk/protocol';
import { newId } from '../ids';
import type { ModelConfig } from './config';
import { classifyModelError } from './errors';
import type { ModelRegistry } from './registry';
import type { ChatMessage, CompletionUsage, ModelAdapter } from './types';

/** Tokens for an image part: W×H/750 from the data URL's header (a typical page when it cannot be read). */
function imagePartTokens(url: string): number {
  const info = readDataUrlInfo(url);
  return info ? imageTokens(info.width, info.height) : imageTokens(1240, 1754);
}

/** A rough count when the endpoint reports no usage: 4 characters per token, images by their pixel size. */
export function estimateUsage(messages: ChatMessage[], content: string, toolCalls: ToolCall[]): CompletionUsage {
  let images = 0;
  const text = JSON.stringify(messages, (key, value: unknown) => {
    if (key !== 'image_url' || typeof (value as { url?: unknown })?.url !== 'string') return value;
    const url = (value as { url: string }).url;
    if (url) images += imagePartTokens(url);
    return {};
  });
  return {
    prompt_tokens: Math.ceil(text.length / 4) + images,
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
            // A level the registry does not list for this model is never sent (the endpoint would reject it).
            ...(req.reasoningEffort && info.reasoning_efforts.includes(req.reasoningEffort) ? { reasoning_effort: req.reasoningEffort as never } : {}),
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
