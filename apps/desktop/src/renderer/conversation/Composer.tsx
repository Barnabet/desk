import { useRef, useState, type ClipboardEvent, type DragEvent, type KeyboardEvent, type RefObject } from 'react';
import { call } from '../bridge';
import { Button } from '../components/Button';
import { toastError } from '../components/Toast';
import { fileToBase64, MAX_UPLOAD } from '../files';

const pad = (n: number) => String(n).padStart(2, '0');

/**
 * A pasted screenshot arrives as "image.png" every time: it gets a name of its own, "pasted-2026-09-26-011207.png"
 * ("-2" and on for more in the same paste). Files copied in Finder keep theirs.
 */
export function pastedName(file: File, at: Date, index: number): string {
  if (!/^image\.\w+$/.test(file.name) && file.name) return file.name;
  const ext = /\.(\w+)$/.exec(file.name)?.[1] ?? file.type.split('/')[1] ?? 'png';
  const stamp = `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}-${pad(at.getHours())}${pad(at.getMinutes())}${pad(at.getSeconds())}`;
  return `pasted-${stamp}${index ? `-${index + 1}` : ''}.${ext}`;
}

const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes('Files');

/** Uploads files to the Library and adds an "Attached: <path>" line per file to the draft, in order. */
export function useAttachments(projectId: string, setDraft: (v: (d: string) => string) => void) {
  const [uploading, setUploading] = useState(0);
  const attach = async (files: Iterable<File> | ArrayLike<File> | null) => {
    for (const f of Array.from(files ?? [])) {
      if (f.size > MAX_UPLOAD) {
        toastError(new Error(`${f.name} is larger than 25 MB.`));
        continue;
      }
      setUploading((n) => n + 1);
      try {
        const a = await call('library.upload', { projectId, file: { name: f.name, content_base64: await fileToBase64(f) } });
        setDraft((d) => `${d}${d && !d.endsWith('\n') ? '\n' : ''}Attached: ${a.path}\n`);
      } catch (err) {
        toastError(err);
      } finally {
        setUploading((n) => n - 1);
      }
    }
  };
  return { attach, uploading };
}
export type Attachments = ReturnType<typeof useAttachments>;

/** Makes an element a drop target for files: spread `handlers` on it; `dropping` while files are dragged over it. */
export function useFileDrop(onFiles: (files: FileList) => void) {
  /** dragenter/dragleave fire for every child crossed: files are over the target while this is above zero. */
  const drags = useRef(0);
  const [dropping, setDropping] = useState(false);
  const handlers = {
    onDragEnter: (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      drags.current++;
      setDropping(true);
    },
    onDragOver: (e: DragEvent) => {
      if (hasFiles(e)) e.preventDefault();
    },
    onDragLeave: (e: DragEvent) => {
      if (!hasFiles(e)) return;
      drags.current = Math.max(0, drags.current - 1);
      if (!drags.current) setDropping(false);
    },
    onDrop: (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      drags.current = 0;
      setDropping(false);
      onFiles(e.dataTransfer.files);
    },
  };
  return { handlers, dropping };
}

/** Message Desk: ⏎ sends, ⇧⏎ adds a line; attachments (picked, pasted, or dropped on the chat) go to the Library and are referenced in the message. */
export function Composer(o: {
  projectId: string;
  draft: string;
  setDraft(v: string | ((d: string) => string)): void;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  onSent(text: string): void;
  attachments: Attachments;
}) {
  const [sending, setSending] = useState(false);
  const { attach, uploading } = o.attachments;
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

  const onPaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    const files = Array.from(e.clipboardData?.files ?? []);
    if (!files.length) return;
    e.preventDefault();
    const at = new Date();
    void attach(files.map((f, i) => new File([f], pastedName(f, at, i), { type: f.type })));
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
        onPaste={onPaste}
        placeholder="Brief a new piece of work, answer a question, or change direction"
      />
      <div className="composer-bar">
        <button type="button" className="icon-btn" aria-label="Attach a file" disabled={uploading > 0} onClick={() => fileRef.current?.click()}>
          <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
            <path d="M11.5 6.5L7 11a3 3 0 0 1-4.2-4.2l4.6-4.6a2 2 0 0 1 2.8 2.8L5.6 9.6a1 1 0 0 1-1.4-1.4L8.4 4" fill="none" stroke="var(--text)" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
        <input
          ref={fileRef}
          type="file"
          multiple
          hidden
          data-testid="attach-input"
          onChange={(e) => {
            const input = e.currentTarget;
            void attach(Array.from(input.files ?? [])).then(() => (input.value = ''));
          }}
        />
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
