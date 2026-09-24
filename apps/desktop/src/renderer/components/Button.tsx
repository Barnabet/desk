import type { ButtonHTMLAttributes } from 'react';

type Props = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'ghost' | 'danger'; size?: 'sm' | 'md'; pending?: boolean };

/** A real button; `pending` disables it and marks it busy until the write completes. */
export function Button({ variant = 'secondary', size = 'md', pending = false, className, disabled, type = 'button', ...rest }: Props) {
  const cls = ['btn', `btn-${variant}`, size === 'sm' ? 'btn-sm' : '', className ?? ''].filter(Boolean).join(' ');
  return <button type={type} className={cls} disabled={disabled || pending} aria-busy={pending || undefined} {...rest} />;
}
