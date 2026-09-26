import { describe, expect, it } from 'vitest';
import { decodeEntities, toBlocks, type MdBlock, type MdInline } from './markdown';

const text = (t: string): MdInline => ({ kind: 'text', text: t });
type List = Extract<MdBlock, { kind: 'list' }>;

describe('toBlocks', () => {
  it('reads GFM: emphasis, aligned tables and task lists', () => {
    const [para, table, list] = toBlocks('**Five drafts** ready\n\n| a | b |\n|:--|--:|\n| 1 | 2 |\n\n- [x] done\n- [ ] todo');
    expect(para).toEqual({ kind: 'paragraph', children: [{ kind: 'strong', children: [text('Five drafts')] }, text(' ready')] });
    expect(table).toEqual({ kind: 'table', align: ['left', 'right'], header: [[text('a')], [text('b')]], rows: [[[text('1')], [text('2')]]] });
    expect(list).toMatchObject({ kind: 'list', ordered: false, start: null, tasks: true });
    const items = (list as List).items;
    expect(items.map((i) => [i.task, i.checked])).toEqual([
      [true, true],
      [true, false],
    ]);
    expect(JSON.stringify(items[0]!.children)).toContain('done');
    expect(JSON.stringify(items[1]!.children)).toContain('todo');
  });

  it('puts a task item\'s checkbox first in its first paragraph, tight or loose, as react-markdown does', () => {
    const box = (checked: boolean): MdInline => ({ kind: 'checkbox', checked });
    const [tight] = toBlocks('- [x] done\n- [ ] todo');
    expect((tight as List).items.map((i) => i.children)).toEqual([
      [{ kind: 'text', children: [box(true), text(' done')] }],
      [{ kind: 'text', children: [box(false), text(' todo')] }],
    ]);
    const [loose] = toBlocks('- [x] a\n\n- [ ] **b** tail\n\n  more');
    expect(loose).toMatchObject({ kind: 'list', tasks: true });
    expect((loose as List).items.map((i) => i.children)).toEqual([
      [{ kind: 'paragraph', children: [box(true), text(' a')] }],
      [
        { kind: 'paragraph', children: [box(false), text(' '), { kind: 'strong', children: [text('b')] }, text(' tail')] },
        { kind: 'paragraph', children: [text('more')] },
      ],
    ]);
  });

  it('drops raw HTML, block and inline, and keeps the text around it', () => {
    expect(toBlocks('<script>alert(1)</script><img src=x onerror=alert(1)>\n\n![chart](https://evil.test/p.png)\n\n<b>bold?</b>')).toEqual([
      { kind: 'paragraph', children: [{ kind: 'image', src: 'https://evil.test/p.png', alt: 'chart' }] },
      { kind: 'paragraph', children: [text('bold?')] },
    ]);
  });

  it('keeps link targets as written, for ExternalLink to judge', () => {
    expect(toBlocks('See [the doc](https://example.com/doc), <https://desk.dev> and hi@desk.dev')).toEqual([
      {
        kind: 'paragraph',
        children: [
          text('See '),
          { kind: 'link', href: 'https://example.com/doc', children: [text('the doc')] },
          text(', '),
          { kind: 'link', href: 'https://desk.dev', children: [text('https://desk.dev')] },
          text(' and '),
          { kind: 'link', href: 'mailto:hi@desk.dev', children: [text('hi@desk.dev')] },
        ],
      },
    ]);
  });

  it('decodes entities in link and image destinations but not in autolinks, and reads an image alt as plain text', () => {
    expect(toBlocks('[c](https://x.test/?a=1&amp;b=2) https://x.test/?a=1&amp;b=2 ![a *b* \\* `c` [d](u) &#38;amp;](https://x.test/i.png?w=1&amp;h=2)')).toEqual([
      {
        kind: 'paragraph',
        children: [
          { kind: 'link', href: 'https://x.test/?a=1&b=2', children: [text('c')] },
          text(' '),
          { kind: 'link', href: 'https://x.test/?a=1&amp;b=2', children: [text('https://x.test/?a=1&amp;b=2')] },
          text(' '),
          { kind: 'image', src: 'https://x.test/i.png?w=1&h=2', alt: 'a b * c d &amp;' },
        ],
      },
    ]);
  });

  it('drops an image source whose scheme react-markdown drops, and keeps relative ones', () => {
    const image = (src: string, alt: string): MdInline => ({ kind: 'image', src, alt });
    expect(toBlocks('![a](javascript:alert(1)) ![b](data:image/png;base64,AAA) ![c](javascript&#58;alert(1)) ![d](/p.png) ![e](mailto:hi@desk.dev)')).toEqual([
      { kind: 'paragraph', children: [image('', 'a'), text(' '), image('', 'b'), text(' '), image('', 'c'), text(' '), image('/p.png', 'd'), text(' '), image('mailto:hi@desk.dev', 'e')] },
    ]);
  });

  it('keeps code whole, naming its language by the first word of the info string', () => {
    expect(toBlocks('```bash title=install\ncurl -fsSL https://bun.sh/install | bash\n```\n\n    indented')).toEqual([
      { kind: 'code', code: 'curl -fsSL https://bun.sh/install | bash', language: 'bash' },
      { kind: 'code', code: 'indented' },
    ]);
  });

  it('reads headings, ordered and loose lists, quotes, rules and line breaks', () => {
    const blocks = toBlocks('# Title\n\n3. three\n4. four\n\n- a\n\n- b\n\n> quoted *em* ~~gone~~\n\n---\n\nline one  \nline two');
    expect(blocks[0]).toEqual({ kind: 'heading', depth: 1, children: [text('Title')] });
    expect(blocks[1]).toMatchObject({ kind: 'list', ordered: true, start: 3, tasks: false });
    expect((blocks[1] as List).items.map((i) => i.children)).toEqual([[{ kind: 'text', children: [text('three')] }], [{ kind: 'text', children: [text('four')] }]]);
    expect((blocks[2] as List).items.map((i) => i.children)).toEqual([[{ kind: 'paragraph', children: [text('a')] }], [{ kind: 'paragraph', children: [text('b')] }]]);
    expect(blocks[3]).toEqual({
      kind: 'blockquote',
      children: [{ kind: 'paragraph', children: [text('quoted '), { kind: 'em', children: [text('em')] }, text(' '), { kind: 'del', children: [text('gone')] }] }],
    });
    expect(blocks[4]).toEqual({ kind: 'hr' });
    expect(blocks[5]).toEqual({ kind: 'paragraph', children: [text('line one'), { kind: 'br' }, text('line two')] });
  });

  it('decodes entities in text once, never in code', () => {
    expect(toBlocks('Tom &amp; Jerry &lt;b&gt; &#169; &#x1F600; &#38;amp; &bogus; &constructor; &toString; `&amp;`')).toEqual([
      { kind: 'paragraph', children: [text('Tom & Jerry <b> © 😀 &amp; &bogus; &constructor; &toString; '), { kind: 'code', text: '&amp;' }] },
    ]);
    expect(decodeEntities('&valueOf;&hasOwnProperty;&isPrototypeOf;')).toBe('&valueOf;&hasOwnProperty;&isPrototypeOf;');
    expect(decodeEntities('&#0;&quot;&apos;&nbsp;')).toBe('�"\' ');
  });
});
