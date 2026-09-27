import { useState } from 'react';
import type { AutomationDefinition, AutomationDetail } from '@desk/protocol';
import { diffDefinitions } from '@desk/ui-core';
import { Button } from '../../components/Button';
import { Sheet } from '../../components/Sheet';
import { DiffView } from '../versions/DiffView';

/** A save refused because another version landed first: that version, and who saved it ("Desk", "You (CLI)"…). */
export type Conflict = { theirs: AutomationDetail; by: string };

/** Spec §8.2: "Desk saved v8 while you were editing", with Review changes, Save mine anyway and Discard mine. */
export function ConflictDialog(o: { conflict: Conflict; mine: AutomationDefinition; saving: boolean; onSaveMine(): void; onDiscardMine(): void; onClose(): void }) {
  const [review, setReview] = useState(false);
  const { theirs, by } = o.conflict;
  const v = theirs.version;
  return (
    <Sheet
      title={by === 'Desk' ? `Desk saved v${v} while you were editing` : `v${v} was saved while you were editing`}
      onClose={o.onClose}
      width={640}
      footer={
        <>
          <Button onClick={() => setReview((r) => !r)}>{review ? 'Hide changes' : 'Review changes'}</Button>
          <Button onClick={o.onDiscardMine}>Discard mine</Button>
          <Button variant="primary" pending={o.saving} onClick={o.onSaveMine}>
            Save mine anyway
          </Button>
        </>
      }
    >
      <p>{`Saved by ${by}. Your edits started from an earlier version.`}</p>
      <p className="muted small">{`Save mine anyway makes your version v${v + 1}; v${v} stays in Versions. Discard mine loads v${v} into the editor.`}</p>
      {review ? (
        <section aria-label="What saving yours changes">
          <DiffView diff={diffDefinitions(theirs.definition, o.mine)} labels={{ before: `v${v}`, after: 'yours' }} />
        </section>
      ) : null}
    </Sheet>
  );
}
