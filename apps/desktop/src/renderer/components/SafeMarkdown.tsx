import Markdown, { defaultUrlTransform, type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { CodeBlock } from './CodeBlock';
import { ExternalLink } from './ExternalLink';

type HastNode = { type?: string; value?: string; tagName?: string; properties?: { className?: unknown }; children?: HastNode[] };

const textOf = (n: HastNode | undefined): string => (n?.type === 'text' ? (n.value ?? '') : (n?.children ?? []).map(textOf).join(''));

const components: Components = {
  a: ({ href, children }) => <ExternalLink href={href ?? ''}>{children}</ExternalLink>,
  img: ({ src, alt }) =>
    typeof src === 'string' && src ? <ExternalLink href={src}>{`Image: ${alt || src}`}</ExternalLink> : <span>{alt}</span>,
  pre: ({ node }) => {
    const code = (node as HastNode | undefined)?.children?.find((c) => c.tagName === 'code');
    const classes = Array.isArray(code?.properties?.className) ? (code.properties.className as unknown[]).map(String) : [];
    const lang = classes.find((c) => c.startsWith('language-'))?.slice('language-'.length);
    return <CodeBlock code={textOf(code).replace(/\n$/, '')} {...(lang ? { language: lang } : {})} />;
  },
  code: ({ children }) => <code className="md-code">{children}</code>,
};

/** Markdown from agents: GFM, no raw HTML, no remote images (they become links), links confirmed before opening. */
export function SafeMarkdown({ text, className }: { text: string; className?: string }) {
  return (
    <div className={`md${className ? ` ${className}` : ''}`}>
      <Markdown remarkPlugins={[remarkGfm]} skipHtml urlTransform={defaultUrlTransform} components={components}>
        {text}
      </Markdown>
    </div>
  );
}
