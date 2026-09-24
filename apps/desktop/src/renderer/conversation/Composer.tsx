import { useRef, useState, type KeyboardEvent, type RefObject } from 'react';
import { call } from '../bridge';
import { Button } from '../components/Button';
import { toastError } from '../components/Toast';
import { fileToBase64, MAX_UPLOAD } from '../files';

/** Message Desk: ⏎ sends, ⇧⏎ adds a line; attachments go to the Library and are referenced in the message. */
export function Composer(o: { projectId: string; draft: string; setDraft(v: string | ((d: string) => string)): void; textareaRef: RefObject<HTMLTextAreaElement | null>; onSent(text: string): void }) {
  const [sending, setSending] = useState(false);
  const [uploading, setUploading] = useState(0);
  const fileRef = useRef<HTMLInputElement>(null);
  const id = `composer-${o.projectId}`;

  const send = async () => {
    const text = o.draft.trim();
    if (!text || sending) return;
    setSending(true);
    try {
      await call('projects.send', { id: o.projectId, text });
      o.setDraft('');
      o.onSent(text);
    } catch (err) {
      toastError(err);
    } finally {
      setSending(false);
    }
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void send();
    }
  };

  const attach = async (files: FileList | null) => {
    for (const f of Array.from(files ?? [])) {
      if (f.size > MAX_UPLOAD) {
        toastError(new Error(`${f.name} is larger than 25 MB.`));
        continue;
      }
      setUploading((n) => n + 1);
      try {
        const a = await call('library.upload', { projectId: o.projectId, file: { name: f.name, content_base64: await fileToBase64(f) } });
        o.setDraft((d) => `${d}${d && !d.endsWith('\n') ? '\n' : ''}Attached: ${a.path}\n`);
      } catch (err) {
        toastError(err);
      } finally {
        setUploading((n) => n - 1);
      }
    }
    if (fileRef.current) fileRef.current.value = '';
  };

  return (
    <div className="composer">
      <label htmlFor={id}>Message Desk</label>
      <textarea
        id={id}
        ref={o.textareaRef}
        rows={2}
        value={o.draft}
        onChange={(e) => o.setDraft(e.target.value)}
        onKeyDown={onKey}
        placeholder="Brief a new piece of work, answer a question, or change direction"
      />
      <div className="composer-bar">
        <button type="button" className="icon-btn" aria-label="Attach a file" disabled={uploading > 0} onClick={() => fileRef.current?.click()}>
          <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
            <path d="M11.5 6.5L7 11a3 3 0 0 1-4.2-4.2l4.6-4.6a2 2 0 0 1 2.8 2.8L5.6 9.6a1 1 0 0 1-1.4-1.4L8.4 4" fill="none" stroke="#3D3A34" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
        <input ref={fileRef} type="file" multiple hidden data-testid="attach-input" onChange={(e) => void attach(e.target.files)} />
        <button
          type="button"
          className="link small"
          onClick={() => {
            o.setDraft((d) => d || 'Turn what we just did into a reusable skill: ');
            o.textareaRef.current?.focus();
          }}
        >
          Turn this into a skill
        </button>
        <span className="grow" />
        <span className="muted small">{uploading ? 'Uploading…' : '⏎ send · ⇧⏎ new line'}</span>
        <Button variant="primary" size="sm" pending={sending} disabled={!o.draft.trim()} onClick={() => void send()}>
          Send
        </Button>
      </div>
    </div>
  );
}
