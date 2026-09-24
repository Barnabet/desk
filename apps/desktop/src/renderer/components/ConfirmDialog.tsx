import type { ReactNode } from 'react';
import { Button } from './Button';
import { Sheet } from './Sheet';

export function ConfirmDialog(o: { title: string; children: ReactNode; confirmLabel: string; danger?: boolean; onConfirm(): void; onCancel(): void }) {
  return (
    <Sheet
      title={o.title}
      onClose={o.onCancel}
      width={460}
      footer={
        <>
          <Button onClick={o.onCancel}>Cancel</Button>
          <Button variant={o.danger ? 'danger' : 'primary'} onClick={o.onConfirm}>
            {o.confirmLabel}
          </Button>
        </>
      }
    >
      {o.children}
    </Sheet>
  );
}
