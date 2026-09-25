import { marked } from 'marked';

/** Inline content: text only ever reaches the page by interpolation. */
export type MdInline =
  | { kind: 'text'; text: string }
  | { kind: 'strong' | 'em' | 'del'; children: MdInline[] }
  | { kind: 'code'; text: string }
  | { kind: 'br' }
  | { kind: 'link'; href: string; children: MdInline[] }
  | { kind: 'image'; src: string; alt: string }
  /** A task item's disabled checkbox, first in its first paragraph (as react-markdown puts it). */
  | { kind: 'checkbox'; checked: boolean };

/** A task item's checkbox is already in `children`, as a `checkbox` inline. */
export type MdListItem = { task: boolean; checked: boolean; children: MdBlock[] };

/** Blocks. `text` is a tight list item's content, shown without a paragraph. */
export type MdBlock =
  | { kind: 'paragraph' | 'text'; children: MdInline[] }
  | { kind: 'heading'; depth: number; children: MdInline[] }
  | { kind: 'code'; code: string; language?: string }
  | { kind: 'blockquote'; children: MdBlock[] }
  | { kind: 'list'; ordered: boolean; start: number | null; tasks: boolean; items: MdListItem[] }
  | { kind: 'table'; align: Array<'left' | 'center' | 'right' | null>; header: MdInline[][]; rows: MdInline[][][] }
  | { kind: 'hr' };

/** The token fields read here, by name, whatever a marked version calls its token types. */
type Raw = {
  type: string;
  text?: string;
  href?: string;
  lang?: string;
  depth?: number;
  ordered?: boolean;
  start?: number | '';
  loose?: boolean;
  task?: boolean;
  checked?: boolean;
  tokens?: Raw[];
  items?: Raw[];
  align?: Array<'left' | 'center' | 'right' | null>;
  header?: Array<{ tokens: Raw[] }>;
  rows?: Array<Array<{ tokens: Raw[] }>>;
};

const NAMED: Record<string, string> = {
  amp: '&',
  lt: '<',
  gt: '>',
  quot: '"',
  apos: "'",
  nbsp: ' ',
  copy: '©',
  reg: '®',
  trade: '™',
  hellip: '…',
  mdash: '—',
  ndash: '–',
  lsquo: '‘',
  rsquo: '’',
  ldquo: '“',
  rdquo: '”',
  laquo: '«',
  raquo: '»',
  middot: '·',
  bull: '•',
  times: '×',
  divide: '÷',
  deg: '°',
  plusmn: '±',
  euro: '€',
  pound: '£',
  yen: '¥',
  sect: '§',
  para: '¶',
  larr: '←',
  rarr: '→',
  uarr: '↑',
  darr: '↓',
  harr: '↔',
};

/** Decodes numeric and common named character references; unknown names stay as written. */
export function decodeEntities(text: string): string {
  return text.replace(/&(#\d{1,7}|#[xX][0-9a-fA-F]{1,6}|[a-zA-Z][a-zA-Z0-9]{1,31});/g, (whole, ref: string) => {
    if (ref.startsWith('#')) {
      const code = ref[1] === 'x' || ref[1] === 'X' ? parseInt(ref.slice(2), 16) : parseInt(ref.slice(1), 10);
      return code > 0 && code <= 0x10ffff && (code < 0xd800 || code > 0xdfff) ? String.fromCodePoint(code) : '�';
    }
    // Own keys only, so `&constructor;` or `&toString;` never reach Object.prototype.
    return Object.hasOwn(NAMED, ref) ? NAMED[ref]! : whole;
  });
}

function pushText(out: MdInline[], text: string): void {
  if (!text) return;
  const last = out.at(-1);
  if (last?.kind === 'text') out[out.length - 1] = { kind: 'text', text: last.text + text };
  else out.push({ kind: 'text', text });
}

function inline(tokens: Raw[] | undefined): MdInline[] {
  const out: MdInline[] = [];
  for (const t of tokens ?? []) {
    switch (t.type) {
      case 'text':
        if (t.tokens?.length) {
          for (const piece of inline(t.tokens)) {
            if (piece.kind === 'text') pushText(out, piece.text);
            else out.push(piece);
          }
        } else pushText(out, decodeEntities(t.text ?? ''));
        break;
      case 'escape':
        pushText(out, t.text ?? '');
        break;
      case 'strong':
      case 'em':
      case 'del':
        out.push({ kind: t.type as 'strong' | 'em' | 'del', children: inline(t.tokens) });
        break;
      case 'codespan':
        out.push({ kind: 'code', text: t.text ?? '' });
        break;
      case 'br':
        out.push({ kind: 'br' });
        break;
      case 'link':
        out.push({ kind: 'link', href: t.href ?? '', children: inline(t.tokens) });
        break;
      case 'image':
        out.push({ kind: 'image', src: t.href ?? '', alt: decodeEntities(t.text ?? '') });
        break;
      default:
        // html: raw HTML is never rendered (the desktop's skipHtml). Unknown tokens are skipped too.
        break;
    }
  }
  return out;
}

/**
 * A task item's checkbox goes first in its first paragraph, then a space before any content, as mdast-util-to-hast does;
 * an item that starts with anything else gets a paragraph holding only the checkbox.
 */
function withCheckbox(children: MdBlock[], checked: boolean, loose: boolean): MdBlock[] {
  const box: MdInline = { kind: 'checkbox', checked };
  const first = children[0];
  if (first?.kind !== 'paragraph' && first?.kind !== 'text') return [{ kind: loose ? 'paragraph' : 'text', children: [box] }, ...children];
  const inlines: MdInline[] = [box];
  if (first.children.length) {
    pushText(inlines, ' ');
    for (const piece of first.children) {
      if (piece.kind === 'text') pushText(inlines, piece.text);
      else inlines.push(piece);
    }
  }
  return [{ kind: first.kind, children: inlines }, ...children.slice(1)];
}

function blocks(tokens: Raw[] | undefined, loose: boolean): MdBlock[] {
  const out: MdBlock[] = [];
  for (const t of tokens ?? []) {
    switch (t.type) {
      case 'paragraph':
        out.push({ kind: 'paragraph', children: inline(t.tokens) });
        break;
      case 'text': {
        const children = t.tokens?.length ? inline(t.tokens) : inline([{ type: 'text', text: t.text ?? '' }]);
        out.push({ kind: loose ? 'paragraph' : 'text', children });
        break;
      }
      case 'heading':
        out.push({ kind: 'heading', depth: Math.min(6, Math.max(1, t.depth ?? 1)), children: inline(t.tokens) });
        break;
      case 'code': {
        const language = t.lang?.match(/^\S+/)?.[0];
        out.push(language ? { kind: 'code', code: t.text ?? '', language } : { kind: 'code', code: t.text ?? '' });
        break;
      }
      case 'blockquote':
        out.push({ kind: 'blockquote', children: blocks(t.tokens, true) });
        break;
      case 'list': {
        const items = (t.items ?? []).map((i): MdListItem => {
          const [task, checked, itemLoose] = [i.task === true, i.checked === true, i.loose === true];
          const children = blocks(i.tokens, itemLoose);
          return { task, checked, children: task ? withCheckbox(children, checked, itemLoose) : children };
        });
        const start = t.ordered && typeof t.start === 'number' && t.start !== 1 ? t.start : null;
        out.push({ kind: 'list', ordered: t.ordered === true, start, tasks: items.some((i) => i.task), items });
        break;
      }
      case 'table':
        out.push({
          kind: 'table',
          align: t.align ?? [],
          header: (t.header ?? []).map((cell) => inline(cell.tokens)),
          rows: (t.rows ?? []).map((row) => row.map((cell) => inline(cell.tokens))),
        });
        break;
      case 'hr':
        out.push({ kind: 'hr' });
        break;
      default:
        // html blocks (never rendered), space, def (link definitions), and anything unknown.
        break;
    }
  }
  return out;
}

/** Agent markdown as blocks for SafeMarkdown's templates: GFM, raw HTML dropped, entities decoded in text only. */
export function toBlocks(text: string): MdBlock[] {
  return blocks(marked.lexer(text, { gfm: true }) as unknown as Raw[], true);
}
