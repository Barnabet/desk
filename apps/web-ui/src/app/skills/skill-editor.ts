import { ChangeDetectionStrategy, Component, ViewEncapsulation, computed, inject, input, output, signal, type OnInit } from '@angular/core';
import type { SkillDetail } from '@desk/client';
import { SkillName } from '@desk/protocol';
import { bytes, fileToBase64, MAX_UPLOAD, textToBase64, type SkillRef } from '@desk/ui-core';
import { Button } from '../components/button';
import { Field } from '../components/field';
import { Sheet } from '../components/sheet';
import { ToastService } from '../components/toast';
import { DeskBridge, DeskCallError } from '../core/desk-bridge';
import { scopeArg } from './data';

type NewFile = { path: string; content: string | File };
type Errors = Partial<Record<'name' | 'description' | 'instructions' | 'form', string>>;

/** Create a skill, or refine one by hand: description, instructions (SKILL.md), files to add or remove, and a change note (SkillEditor.tsx). */
@Component({
  selector: 'div[deskSkillEditor]',
  imports: [Button, Field, Sheet],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    <div deskSheet [title]="sheetTitle()" [width]="720" (close)="dismiss()">
      @if (!skill()) {
        <div class="skill-editor-row">
          <div deskField id="skill-name" label="Name" [error]="errors().name ?? null" hint="Lowercase words joined by hyphens, like weekly-report.">
            <input id="skill-name" class="input mono" [value]="name()" (input)="name.set(val($event))" />
          </div>
          <div deskField id="skill-scope" label="Scope">
            <select id="skill-scope" class="select" (change)="scope.set(val($event))">
              <option value="global" [selected]="scope() === 'global'">Global (every project)</option>
              @for (p of projects(); track p.id) {
                <option [value]="p.id" [selected]="scope() === p.id">{{ p.name }} only</option>
              }
            </select>
          </div>
        </div>
      }
      <div deskField id="skill-description" label="Description" [error]="errors().description ?? null">
        <input id="skill-description" class="input" maxlength="1024" [value]="description()" (input)="description.set(val($event))" />
      </div>
      <div deskField id="skill-instructions" label="Instructions (SKILL.md)" [error]="errors().instructions ?? null" hint="Markdown. Scripts in the skill run with skill_run, in the sandbox.">
        <textarea id="skill-instructions" class="textarea mono" rows="12" [value]="instructions()" (input)="instructions.set(val($event))"></textarea>
      </div>
      <div class="field">
        <span class="label">Files</span>
        @if (existing().length || added().length) {
          <ul class="skill-files-edit">
            @for (f of existing(); track f.path) {
              <li [class.removing]="remove().has(f.path)">
                <span class="mono grow">{{ f.path }}</span>
                <span class="muted small">{{ bytes(f.size) }}</span>
                <label class="small"><input type="checkbox" [checked]="remove().has(f.path)" (change)="toggleRemove(f.path, $event)" /> Remove</label>
              </li>
            }
            @for (f of added(); track f.path) {
              <li class="adding">
                <span class="mono grow">{{ f.path }}</span>
                <span class="muted small">{{ sizeOf(f) }}</span>
                <button deskButton size="sm" variant="ghost" [attr.aria-label]="dropLabel(f.path)" (click)="drop(f.path)">✕</button>
              </li>
            }
          </ul>
        } @else {
          <p class="field-hint">No files besides SKILL.md.</p>
        }
        <div class="skill-add-files">
          <input class="input mono" aria-label="Folder for uploads" [value]="folder()" (input)="folder.set(val($event))" />
          <button deskButton size="sm" (click)="pick.click()">Upload files…</button>
          <input #pick type="file" multiple hidden data-testid="skill-upload" (change)="addUploads(pick)" />
        </div>
        <details class="skill-new-text">
          <summary>New text file</summary>
          <div class="skill-add-files">
            <input class="input mono" aria-label="New file path" placeholder="scripts/check.py" [value]="textPath()" (input)="textPath.set(val($event))" />
            <button deskButton size="sm" [disabled]="!textPath().trim()" (click)="addText()">Add file</button>
          </div>
          <textarea class="textarea mono" aria-label="New file content" rows="6" [value]="textBody()" (input)="textBody.set(val($event))"></textarea>
        </details>
      </div>
      <div deskField id="skill-note" label="Change note (optional)" hint="Shown in the history next to this version.">
        <input id="skill-note" class="input" [value]="note()" (input)="note.set(val($event))" />
      </div>
      @if (errors().form; as form) {
        <p class="field-error" role="alert">{{ form }}</p>
      }
      <div class="sheet-footer">
        <button deskButton [disabled]="pending()" (click)="dismiss()">Cancel</button>
        <button deskButton variant="primary" [pending]="pending()" (click)="save()">{{ skill() ? 'Save new version' : 'Create skill' }}</button>
      </div>
    </div>
  `,
})
export class SkillEditor implements OnInit {
  /** The skill being refined; absent for a new one. */
  readonly skill = input<{ ref: SkillRef; detail: SkillDetail }>();
  readonly projects = input.required<Array<{ id: string; name: string }>>();
  readonly defaultProjectId = input<string>();
  readonly saved = output<SkillRef>();
  readonly close = output<void>();

  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly bytes = bytes;
  protected readonly name = signal('');
  protected readonly scope = signal('global');
  protected readonly description = signal('');
  protected readonly instructions = signal('');
  protected readonly remove = signal<ReadonlySet<string>>(new Set());
  protected readonly added = signal<NewFile[]>([]);
  protected readonly folder = signal('scripts/');
  protected readonly textPath = signal('');
  protected readonly textBody = signal('');
  protected readonly note = signal('');
  protected readonly errors = signal<Errors>({});
  protected readonly pending = signal(false);
  protected readonly existing = computed(() => (this.skill()?.detail.files ?? []).filter((f) => f.path !== 'SKILL.md'));
  protected readonly sheetTitle = computed(() => {
    const editing = this.skill();
    return editing ? `Edit ${editing.ref.name}` : 'New skill';
  });

  /** React's useState initial values: taken from the inputs once. */
  ngOnInit(): void {
    const editing = this.skill();
    this.name.set(editing?.ref.name ?? '');
    this.scope.set(editing ? (editing.ref.scope === 'global' ? 'global' : (editing.ref.projectId ?? 'global')) : (this.defaultProjectId() ?? 'global'));
    this.description.set(editing?.detail.description ?? '');
    this.instructions.set(editing?.detail.instructions ?? '');
  }

  protected val(e: Event): string {
    return (e.target as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement).value;
  }

  /** Cancel, Escape and the backdrop do nothing while the save runs, so its `saved` still reaches the screen. */
  protected dismiss(): void {
    if (!this.pending()) this.close.emit();
  }

  protected sizeOf(f: NewFile): string {
    return typeof f.content === 'string' ? 'new text file' : bytes(f.content.size);
  }

  protected dropLabel(path: string): string {
    return `Don't add ${path}`;
  }

  protected drop(path: string): void {
    this.added.update((a) => a.filter((x) => x.path !== path));
  }

  protected toggleRemove(path: string, e: Event): void {
    const on = (e.target as HTMLInputElement).checked;
    this.remove.update((r) => {
      const next = new Set(r);
      if (on) next.add(path);
      else next.delete(path);
      return next;
    });
  }

  protected addUploads(picker: HTMLInputElement): void {
    const prefix = this.folder().trim().replace(/^\/+/, '');
    const next: NewFile[] = [];
    for (const f of Array.from(picker.files ?? [])) {
      if (f.size > MAX_UPLOAD) {
        this.toasts.error(new Error(`${f.name} is larger than 25 MB.`));
        continue;
      }
      next.push({ path: `${prefix && !prefix.endsWith('/') ? `${prefix}/` : prefix}${f.name}`, content: f });
    }
    this.added.update((a) => [...a.filter((x) => !next.some((n) => n.path === x.path)), ...next]);
    picker.value = '';
  }

  protected addText(): void {
    const path = this.textPath().trim().replace(/^\/+/, '');
    const content = this.textBody();
    this.added.update((a) => [...a.filter((x) => x.path !== path), { path, content }]);
    this.textPath.set('');
    this.textBody.set('');
  }

  protected async save(): Promise<void> {
    const editing = this.skill();
    const e: Errors = {};
    const parsedName = SkillName.safeParse(this.name().trim());
    if (!editing && !parsedName.success) e.name = parsedName.error.issues[0]?.message ?? 'Invalid name';
    if (!this.description().trim()) e.description = 'Say in one line what the skill is for; Desk uses it to pick skills.';
    if (!this.instructions().trim()) e.instructions = 'Write the instructions agents follow.';
    this.errors.set(e);
    if (Object.keys(e).length) return;
    const scope = this.scope();
    const name = this.name().trim();
    const ref: SkillRef = editing ? editing.ref : scope === 'global' ? { scope: 'global', name } : { scope: 'project', projectId: scope, name };
    this.pending.set(true);
    try {
      const files = await Promise.all(this.added().map(async (f) => ({ path: f.path, content_base64: typeof f.content === 'string' ? await textToBase64(f.content) : await fileToBase64(f.content) })));
      const description = this.description().trim();
      const instructions = this.instructions();
      const note = this.note().trim();
      await this.bridge.call('skills.save', {
        ...scopeArg(ref),
        name: ref.name,
        skill: {
          ...(!editing || description !== editing.detail.description ? { description } : {}),
          ...(!editing || instructions !== editing.detail.instructions ? { instructions } : {}),
          files,
          remove_files: [...this.remove()],
          ...(note ? { change_note: note } : {}),
        },
      });
      this.saved.emit(ref);
    } catch (err) {
      if (err instanceof DeskCallError && err.status === 400) this.errors.set({ form: err.message });
      else this.toasts.error(err);
    } finally {
      this.pending.set(false);
    }
  }
}
