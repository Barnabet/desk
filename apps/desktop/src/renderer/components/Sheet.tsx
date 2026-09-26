import { useEffect, useId, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

/**
 * A modal dialog: focus moves in, Escape or a backdrop click closes, focus returns on close. Escape closes only the
 * topmost sheet (a link's confirmation, not the PairSheet under it).
 */
export function Sheet({ title, onClose, children, footer, width = 520 }: { title: string; onClose(): void; children: ReactNode; footer?: ReactNode; width?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const backdrop = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  const titleId = useId();
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>('input, textarea, select, button')?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isTopmost(backdrop.current)) close.current();
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
      previous?.focus?.();
    };
  }, []);
  return createPortal(
    <div className="sheet-backdrop" ref={backdrop} onMouseDown={(e) => e.target === e.currentTarget && close.current()}>
      <div className="sheet" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={ref} style={{ width }}>
        <h2 id={titleId} className="sheet-title">
          {title}
        </h2>
        <div className="sheet-body">{children}</div>
        {footer ? <div className="sheet-footer">{footer}</div> : null}
      </div>
    </div>,
    document.body,
  );
}

/** Each sheet portals its backdrop into the body when it opens, so the one on top is the body's last backdrop. */
function isTopmost(backdrop: HTMLElement | null): boolean {
  const backdrops = [...document.body.children].filter((el) => el.classList.contains('sheet-backdrop'));
  return backdrop !== null && backdrops.at(-1) === backdrop;
}
