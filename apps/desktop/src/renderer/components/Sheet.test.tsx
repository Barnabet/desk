// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { Sheet } from './Sheet';

afterEach(cleanup);

/** A sheet with a second one opened from inside it, as ExternalLink's confirmation opens over a PairSheet. */
function Stacked() {
  const [outer, setOuter] = useState(true);
  const [inner, setInner] = useState(false);
  if (!outer) return null;
  return (
    <Sheet title="Auth API ⇄ Frontend" onClose={() => setOuter(false)}>
      <button type="button" onClick={() => setInner(true)}>
        Open the link
      </button>
      {inner ? (
        <Sheet title="Open this link?" onClose={() => setInner(false)}>
          <button type="button">Open in browser</button>
        </Sheet>
      ) : null}
    </Sheet>
  );
}

describe('Sheet', () => {
  it('closes only the topmost sheet on Escape', () => {
    render(<Stacked />);
    fireEvent.click(screen.getByRole('button', { name: 'Open the link' }));
    expect(screen.getByRole('dialog', { name: 'Open this link?' })).toBeTruthy();
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'Open this link?' })).toBeNull();
    expect(screen.getByRole('dialog', { name: 'Auth API ⇄ Frontend' })).toBeTruthy();
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
