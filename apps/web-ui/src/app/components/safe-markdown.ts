import { NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, input, ViewEncapsulation } from '@angular/core';
import { CodeBlock } from './code-block';
import { ExternalLink } from './external-link';
import { toBlocks } from './markdown';

/**
 * Markdown from agents (spec §4.10): GFM; raw HTML dropped; remote images become links; links confirmed before opening.
 * The text is never parsed as HTML: `toBlocks` tokenises it and these templates interpolate every piece.
 */
@Component({
  selector: 'div[deskSafeMarkdown]',
  imports: [NgTemplateOutlet, CodeBlock, ExternalLink],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { '[class]': "'md' + (className() ? ' ' + className() : '')" },
  template: `
    <ng-template #blockList let-blocks>
      @for (b of blocks; track $index) {
        @switch (b.kind) {
          @case ('paragraph') {
            <p><ng-container *ngTemplateOutlet="inlineList; context: { $implicit: b.children }" /></p>
          }
          @case ('text') {
            <ng-container *ngTemplateOutlet="inlineList; context: { $implicit: b.children }" />
          }
          @case ('heading') {
            @switch (b.depth) {
              @case (1) {
                <h1><ng-container *ngTemplateOutlet="inlineList; context: { $implicit: b.children }" /></h1>
              }
              @case (2) {
                <h2><ng-container *ngTemplateOutlet="inlineList; context: { $implicit: b.children }" /></h2>
              }
              @case (3) {
                <h3><ng-container *ngTemplateOutlet="inlineList; context: { $implicit: b.children }" /></h3>
              }
              @case (4) {
                <h4><ng-container *ngTemplateOutlet="inlineList; context: { $implicit: b.children }" /></h4>
              }
              @case (5) {
                <h5><ng-container *ngTemplateOutlet="inlineList; context: { $implicit: b.children }" /></h5>
              }
              @default {
                <h6><ng-container *ngTemplateOutlet="inlineList; context: { $implicit: b.children }" /></h6>
              }
            }
          }
          @case ('code') {
            <div deskCodeBlock [code]="b.code" [language]="b.language"></div>
          }
          @case ('blockquote') {
            <blockquote><ng-container *ngTemplateOutlet="blockList; context: { $implicit: b.children }" /></blockquote>
          }
          @case ('list') {
            @if (b.ordered) {
              <ol [attr.start]="b.start" [class.contains-task-list]="b.tasks"><ng-container *ngTemplateOutlet="listItems; context: { $implicit: b.items }" /></ol>
            } @else {
              <ul [class.contains-task-list]="b.tasks"><ng-container *ngTemplateOutlet="listItems; context: { $implicit: b.items }" /></ul>
            }
          }
          @case ('table') {
            <table>
              <thead>
                <tr>
                  @for (cell of b.header; track $index) {
                    <th [style.text-align]="b.align[$index]"><ng-container *ngTemplateOutlet="inlineList; context: { $implicit: cell }" /></th>
                  }
                </tr>
              </thead>
              @if (b.rows.length) {
                <tbody>
                  @for (row of b.rows; track $index) {
                    <tr>
                      @for (cell of row; track $index) {
                        <td [style.text-align]="b.align[$index]"><ng-container *ngTemplateOutlet="inlineList; context: { $implicit: cell }" /></td>
                      }
                    </tr>
                  }
                </tbody>
              }
            </table>
          }
          @case ('hr') {
            <hr />
          }
        }
      }
    </ng-template>

    <ng-template #listItems let-items>
      @for (item of items; track $index) {
        <li [class.task-list-item]="item.task">
          <ng-container *ngTemplateOutlet="blockList; context: { $implicit: item.children }" />
        </li>
      }
    </ng-template>

    <ng-template #inlineList let-items>
      @for (i of items; track $index) {
        @switch (i.kind) {
          @case ('text') {<ng-container>{{ i.text }}</ng-container>}
          @case ('strong') {<strong><ng-container *ngTemplateOutlet="inlineList; context: { $implicit: i.children }" /></strong>}
          @case ('em') {<em><ng-container *ngTemplateOutlet="inlineList; context: { $implicit: i.children }" /></em>}
          @case ('del') {<del><ng-container *ngTemplateOutlet="inlineList; context: { $implicit: i.children }" /></del>}
          @case ('code') {<code class="md-code">{{ i.text }}</code>}
          @case ('br') {<br />}
          @case ('checkbox') {<input type="checkbox" disabled [checked]="i.checked" />}
          @case ('link') {<span deskExternalLink [href]="i.href"><ng-container *ngTemplateOutlet="inlineList; context: { $implicit: i.children }" /></span>}
          @case ('image') {
            @if (i.src) {
              <span deskExternalLink [href]="i.src">Image: {{ i.alt || i.src }}</span>
            } @else {
              <span>{{ i.alt }}</span>
            }
          }
        }
      }
    </ng-template>

    <ng-container *ngTemplateOutlet="blockList; context: { $implicit: blocks() }" />
  `,
})
export class SafeMarkdown {
  readonly text = input.required<string>();
  /** Extra classes, like the React prop (`md-voice`, …). Classes written on the host element are kept too. */
  readonly className = input<string>();
  protected readonly blocks = computed(() => toBlocks(this.text()));
}

/** The name spec §4.10 uses. */
export { SafeMarkdown as SafeMarkdownComponent };
