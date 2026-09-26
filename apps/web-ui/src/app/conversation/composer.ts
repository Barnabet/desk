import { ChangeDetectionStrategy, Component, ElementRef, ViewEncapsulation, computed, inject, input, model, output, signal, viewChild } from '@angular/core';
import { fileToBase64, MAX_UPLOAD, pastedName } from '@desk/ui-core';
import { Button } from '../components/button';
import { ToastService } from '../components/toast';
import { DeskBridge } from '../core/desk-bridge';

/**
 * Message Desk: ⏎ sends, ⇧⏎ adds a line; attachments (picked, pasted, or dropped on the chat, which hands them to `attach`)
 * go to the Library and are referenced in the message.
 */
@Component({
  selector: 'div[deskComposer]',
  imports: [Button],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'composer' },
  template: `
    <label [attr.for]="id()">Message Desk</label>
    <textarea
      #box
      [id]="id()"
      rows="2"
      [value]="draft()"
      (input)="draft.set(box.value)"
      (keydown)="onKey($event)"
      (paste)="onPaste($event)"
      placeholder="Brief a new piece of work, answer a question, or change direction"
    ></textarea>
    <div class="composer-bar">
      <button type="button" class="icon-btn" aria-label="Attach a file" [disabled]="uploading() > 0" (click)="attachInput.click()">
        <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
          <path d="M11.5 6.5L7 11a3 3 0 0 1-4.2-4.2l4.6-4.6a2 2 0 0 1 2.8 2.8L5.6 9.6a1 1 0 0 1-1.4-1.4L8.4 4" fill="none" stroke="var(--text)" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </button>
      <input #attachInput type="file" multiple hidden data-testid="attach-input" (change)="picked(attachInput)" />
      <button type="button" class="link small" (click)="turnIntoSkill()">Turn this into a skill</button>
      <span class="grow"></span>
      <span class="muted small">{{ uploading() ? 'Uploading…' : '⏎ send · ⇧⏎ new line' }}</span>
      <button deskButton variant="primary" size="sm" [pending]="sending()" [disabled]="!draft().trim()" (click)="send()">Send</button>
    </div>
  `,
})
export class Composer {
  readonly projectId = input.required<string>();
  /** The screen's draft (saved per project): the React `draft` and `setDraft`. */
  readonly draft = model.required<string>();
  /** A message went to Desk: the screen shows it as sending until its event arrives. */
  readonly sent = output<string>();
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  private readonly box = viewChild.required<ElementRef<HTMLTextAreaElement>>('box');
  protected readonly sending = signal(false);
  protected readonly uploading = signal(0);
  protected readonly id = computed(() => `composer-${this.projectId()}`);

  /** Puts the cursor in the box ("Answer in your own words…"). */
  focus(): void {
    this.box().nativeElement.focus();
  }

  protected async send(): Promise<void> {
    const text = this.draft().trim();
    if (!text || this.sending()) return;
    this.sending.set(true);
    try {
      await this.bridge.call('projects.send', { id: this.projectId(), text });
      this.draft.set('');
      this.sent.emit(text);
    } catch (err) {
      this.toasts.error(err);
    } finally {
      this.sending.set(false);
    }
  }

  protected onKey(e: KeyboardEvent): void {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      void this.send();
    }
  }

  /**
   * Uploads files to the Library and adds an "Attached: <path>" line per file to the draft, in order (React's
   * `useAttachments`, which the chat's drop zone shares with the composer; here the screen hands dropped files to this).
   */
  async attach(files: Iterable<File> | ArrayLike<File> | null): Promise<void> {
    for (const f of Array.from(files ?? [])) {
      if (f.size > MAX_UPLOAD) {
        this.toasts.error(new Error(`${f.name} is larger than 25 MB.`));
        continue;
      }
      this.uploading.update((n) => n + 1);
      try {
        const a = await this.bridge.call('library.upload', { projectId: this.projectId(), file: { name: f.name, content_base64: await fileToBase64(f) } });
        this.draft.update((d) => `${d}${d && !d.endsWith('\n') ? '\n' : ''}Attached: ${a.path}\n`);
      } catch (err) {
        this.toasts.error(err);
      } finally {
        this.uploading.update((n) => n - 1);
      }
    }
  }

  protected async picked(input: HTMLInputElement): Promise<void> {
    await this.attach(Array.from(input.files ?? []));
    input.value = '';
  }

  /** Pasted files (a screenshot, files copied in Finder) are attached; a paste of text is the textarea's. */
  protected onPaste(e: ClipboardEvent): void {
    const files = Array.from(e.clipboardData?.files ?? []);
    if (!files.length) return;
    e.preventDefault();
    const at = new Date();
    void this.attach(files.map((f, i) => new File([f], pastedName(f, at, i), { type: f.type })));
  }

  protected turnIntoSkill(): void {
    this.draft.update((d) => d || 'Turn what we just did into a reusable skill: ');
    this.focus();
  }
}
