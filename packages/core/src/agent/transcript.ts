import { imageLabel, type EventOf, type StoredEvent, type ToolImage } from '@desk/protocol';
import type { ChatMessage, ContentPart } from '../model/types';
import { renderInboxItem } from '../coordination/render';

/** Why an image goes as text after the endpoint refused a request with it: its answer, and how many images were withheld together. */
export type Withheld = { reason: string; count: number };

/** An image a tool result showed the model (view_image), as the conversation carries it. */
export type ConversationImage = {
  image: ToolImage;
  /** The tool call whose result showed it: with the digest, it names this occurrence in `images.withheld`. */
  toolCallId: string;
  /** Set once the endpoint refused a request with this image (see Withheld). */
  withheld?: Withheld;
  /** Set by showImages when the image goes to the model as pixels. */
  pixels?: boolean;
};

/**
 * A conversation message and the id of the event that produced it (ids increase along the conversation).
 * `images` marks the user message that carries the images of a batch of tool results (view_image).
 */
export type TaggedMessage = { message: ChatMessage; eventId: number; images?: ConversationImage[] };

export const CHECKPOINT_HEADER = '[Checkpoint — summary of the earlier conversation]';
/** Closes the checkpoint, so it is told apart from raw user text merged into the same message. */
export const CHECKPOINT_END = '[End of checkpoint]';
/** First part of the user message that follows tool results with images (Chat Completions takes images only from users). */
export const IMAGES_HEADER = '[Images from view_image]';
/** Images sent as pixels: the most recent ones of the conversation. Older ones become text placeholders. */
export const MAX_IMAGES_SHOWN = 8;
/**
 * Bytes of the images sent as pixels, together: about 27 MB once base64-encoded, under the 32 MB request limit of
 * Claude endpoints (a 39 MB request was refused with 413 through the local proxy, 29 MB went through).
 */
export const MAX_IMAGE_BYTES_SHOWN = 20 * 1024 * 1024;

/** Loads a stored image as a data URL; null when the attachment is missing. */
export type ImageLoader = (image: ToolImage) => string | null;

/** Which images go as pixels: the most recent ones, up to `maxImages` of them and `maxBytes` together. */
export type ImageWindow = { maxImages?: number; maxBytes?: number };

export type ConversationOptions = ImageWindow & {
  /** Without a loader (a model without vision, compaction) every image is a text reference. */
  images?: ImageLoader | null;
};

const imageRef = (image: ToolImage) => `[image: ${imageLabel(image)}]`;
const occurrence = (toolCallId: string, sha256: string) => `${toolCallId}\n${sha256}`;
/** Names one showing of an image (the same image viewed again later is another occurrence). */
export const occurrenceOf = (c: ConversationImage) => occurrence(c.toolCallId, c.image.sha256);
/** An images message without pixels is plain text: endpoints that take no images may take no content parts either. */
const textMessage = (lines: string[]): ChatMessage => ({ role: 'user', content: [IMAGES_HEADER, ...lines].join('\n') });
const clip = (s: string, n = 160) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

/**
 * Rebuilds the full model conversation for one agent from its events (in id order), ignoring checkpoints.
 * After each run of consecutive tool results that carry images comes one user message with those images,
 * as text references (`showImages` turns the most recent into pixels).
 */
export function buildTaggedConversation(events: StoredEvent[]): TaggedMessage[] {
  const withheld = new Map<string, Withheld>();
  for (const ev of events) {
    if (ev.type !== 'images.withheld') continue;
    for (const i of ev.payload.images) {
      const key = occurrence(i.tool_call_id, i.sha256);
      if (!withheld.has(key)) withheld.set(key, { reason: ev.payload.reason, count: ev.payload.images.length });
    }
  }

  const out: TaggedMessage[] = [];
  const pending: Array<EventOf<'message.user'> | EventOf<'message.agent'>> = [];
  /** Images of the current run of tool results, and the last result's event id. */
  let shown: ConversationImage[] = [];
  let shownAt = 0;

  const flushImages = () => {
    if (shown.length) {
      out.push({ eventId: shownAt, images: shown, message: textMessage(shown.map((c) => imageRef(c.image))) });
    }
    shown = [];
  };
  const push = (m: TaggedMessage) => {
    if (m.message.role !== 'tool') flushImages();
    out.push(m);
  };

  for (const ev of events) {
    switch (ev.type) {
      case 'message.user':
      case 'message.agent':
        pending.push(ev);
        break;
      case 'inbox.drained': {
        const batch: string[] = [];
        while (pending.length && pending[0]!.id <= ev.payload.up_to) batch.push(renderInboxItem(pending.shift()!));
        if (batch.length) push({ message: { role: 'user', content: batch.join('\n\n') }, eventId: ev.id });
        break;
      }
      case 'assistant.message': {
        const { content, tool_calls } = ev.payload;
        if (!content && tool_calls.length === 0) break;
        push({
          eventId: ev.id,
          message: tool_calls.length
            ? {
                role: 'assistant',
                content,
                tool_calls: tool_calls.map((tc) => ({ id: tc.id, type: 'function' as const, function: { name: tc.name, arguments: tc.arguments } })),
              }
            : { role: 'assistant', content },
        });
        break;
      }
      case 'tool.result': {
        push({ message: { role: 'tool', tool_call_id: ev.payload.tool_call_id, content: ev.payload.content }, eventId: ev.id });
        const toolCallId = ev.payload.tool_call_id;
        for (const image of ev.payload.images ?? []) {
          const w = withheld.get(occurrence(toolCallId, image.sha256));
          shown.push({ image, toolCallId, ...(w ? { withheld: w } : {}) });
        }
        shownAt = ev.id;
        break;
      }
      default:
        break;
    }
  }
  flushImages();
  return out;
}

/**
 * The images that go to the model as pixels: the most recent ones, newest first, while both budgets hold. Withheld
 * images keep their place and their bytes, so withholding one never brings an older image back: the window is a
 * suffix of the conversation that only moves forward, and older images never come back.
 */
export function imagesInWindow(messages: TaggedMessage[], w: ImageWindow = {}): Set<ConversationImage> {
  const out = new Set<ConversationImage>();
  let places = w.maxImages ?? MAX_IMAGES_SHOWN;
  let bytes = w.maxBytes ?? MAX_IMAGE_BYTES_SHOWN;
  for (let i = messages.length - 1; i >= 0; i--) {
    const images = messages[i]!.images ?? [];
    for (let k = images.length - 1; k >= 0; k--) {
      const c = images[k]!;
      if (places-- <= 0 || c.image.bytes > bytes) return out;
      bytes -= c.image.bytes;
      if (!c.withheld) out.add(c);
    }
  }
  return out;
}

/** Marks more images as withheld (while retrying after the endpoint refused a request), before anything records it. */
export function withholdMore(messages: TaggedMessage[], more: ReadonlyMap<string, Withheld>): TaggedMessage[] {
  if (!more.size) return messages;
  return messages.map((m) => {
    if (!m.images?.some((c) => !c.withheld && more.has(occurrenceOf(c)))) return m;
    return { ...m, images: m.images.map((c) => (c.withheld ? c : { ...c, ...(more.has(occurrenceOf(c)) ? { withheld: more.get(occurrenceOf(c))! } : {}) })) };
  });
}

function placeholder(c: ConversationImage, state: 'missing' | 'old' | 'crowded'): string {
  const label = imageLabel(c.image);
  if (c.withheld) {
    const reason = clip(c.withheld.reason);
    return c.withheld.count === 1
      ? `[image not shown: ${label}, the model provider refused it (${reason}); re-render it or convert it to PNG (images skill img_convert.py) before viewing it again]`
      : `[image not shown: ${label}, the model provider refused one of the ${c.withheld.count} images withheld together (${reason}); view them again one at a time]`;
  }
  if (state === 'crowded') return `[image not shown: ${label} — more images than the model is shown at once; view fewer or smaller images at a time]`;
  return state === 'missing' ? `[image no longer available: ${label} — view it again if needed]` : `[image no longer shown: ${label} — view it again if needed]`;
}

/**
 * Turns the images in the window (see imagesInWindow) into pixels (data URLs from `load`); older ones say they are
 * no longer shown, and withheld or missing ones say so too. An image that did not fit even when its own message was
 * the newest (parallel view_image calls showing more than the window at once) was never shown, and says that instead
 * of inviting the model to view it again. Deterministic for the same events, so prompt caching holds until an image
 * leaves the window.
 */
export function showImages(messages: TaggedMessage[], load: ImageLoader | null | undefined, w: ImageWindow = {}): TaggedMessage[] {
  if (!load) return messages;
  const window = imagesInWindow(messages, w);
  return messages.map((m) => {
    if (!m.images) return m;
    const own = imagesInWindow([m], w);
    const images: ConversationImage[] = [];
    const parts: ContentPart[] = [];
    for (const c of m.images) {
      const url = window.has(c) ? load(c.image) : null;
      if (url) {
        images.push({ ...c, pixels: true });
        parts.push({ type: 'image_url', image_url: { url } });
      } else {
        images.push(c);
        parts.push({ type: 'text', text: placeholder(c, window.has(c) ? 'missing' : own.has(c) ? 'old' : 'crowded') });
      }
    }
    const lines = parts.flatMap((p) => (p.type === 'text' ? [p.text] : []));
    const message: ChatMessage =
      lines.length === parts.length ? textMessage(lines) : { role: 'user', content: [{ type: 'text', text: IMAGES_HEADER }, ...parts] };
    return { ...m, images, message };
  });
}

/** The images sent as pixels, one group per images message, the most recent message first. */
export function pixelGroups(messages: TaggedMessage[]): ConversationImage[][] {
  return messages
    .map((m) => (m.images ?? []).filter((c) => c.pixels))
    .filter((g) => g.length > 0)
    .reverse();
}

/** Applies a checkpoint: drops the messages it summarises and puts the summary in front of the rest. */
/**
 * A checkpoint is the model's own summary: a runtime marker at the start of one of its lines (indented or not)
 * becomes `(marker…`, so only the runtime's own markers start lines (design spec §1.5).
 */
export function neutraliseMarkers(text: string): string {
  return text.replace(/^(\s*)\[(message|Checkpoint|End of checkpoint|Desk runtime|Images from view_image)/gim, '$1($2');
}

export function applyCheckpoint(messages: TaggedMessage[], checkpoint: EventOf<'context.compacted'> | undefined): TaggedMessage[] {
  if (!checkpoint) return messages;
  const tail = messages.filter((m) => m.eventId > checkpoint.payload.up_to);
  const summary = `${CHECKPOINT_HEADER}\n${neutraliseMarkers(checkpoint.payload.checkpoint)}\n${CHECKPOINT_END}`;
  const first = tail[0];
  // An images message keeps its own shape (its parts are rebuilt by showImages); the summary goes before it.
  if (first?.message.role === 'user' && !first.images) {
    const content = first.message.content;
    return [
      { eventId: first.eventId, message: { role: 'user', content: typeof content === 'string' ? `${summary}\n\n${content}` : [{ type: 'text', text: summary }, ...content] } },
      ...tail.slice(1),
    ];
  }
  return [{ eventId: checkpoint.payload.up_to, message: { role: 'user', content: summary } }, ...tail];
}

export function latestCheckpoint(events: StoredEvent[]): EventOf<'context.compacted'> | undefined {
  for (let i = events.length - 1; i >= 0; i--) {
    const ev = events[i]!;
    if (ev.type === 'context.compacted') return ev;
  }
  return undefined;
}

/** The conversation the model sees: the latest checkpoint (if any) followed by everything after it. */
export function buildCurrentConversation(events: StoredEvent[], opts: ConversationOptions = {}): TaggedMessage[] {
  return showImages(applyCheckpoint(buildTaggedConversation(events), latestCheckpoint(events)), opts.images, opts);
}

export function buildConversation(events: StoredEvent[], opts: ConversationOptions = {}): ChatMessage[] {
  return buildCurrentConversation(events, opts).map((m) => m.message);
}
