import { href } from '../router';
import type { WaitHop } from '../waits';

/** One hop past a wait: a vermilion dot and "→ needs your approval", linking to that attention item (design spec §8 item 11). */
export function HopLink({ hop }: { hop: WaitHop }) {
  return (
    <a className="wait-hop" href={href({ name: 'attention', item: hop.item.id })} aria-label={hop.label}>
      <span className="hop-dot" aria-hidden="true" />
      {hop.text}
    </a>
  );
}
