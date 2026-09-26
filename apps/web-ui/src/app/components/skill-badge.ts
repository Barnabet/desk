import { ChangeDetectionStrategy, Component, ViewEncapsulation, input } from '@angular/core';

/** A skill's name as a small badge; with `scope`, its tooltip says whose skill it is. */
@Component({
  selector: 'span[deskSkillBadge]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'skill-badge', '[attr.title]': "scope() ? scope() + ' skill' : null" },
  template: '{{ name() }}',
})
export class SkillBadge {
  readonly name = input.required<string>();
  readonly scope = input<'global' | 'project'>();
}
