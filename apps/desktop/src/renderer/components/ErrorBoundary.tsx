import { Component, type ReactNode } from 'react';
import { Button } from './Button';
import { EmptyState } from './EmptyState';

type Props = { children: ReactNode; /** `whole` covers the entire window (no title bar to navigate away with). */ scope?: 'screen' | 'whole' };

/**
 * Catches a render error so one broken screen never blanks the window. Key it on the route so
 * navigating elsewhere starts clean; "Try again" re-renders in place, "Reload Desk" reloads the window.
 */
export class ErrorBoundary extends Component<Props, { error: Error | null }> {
  override state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: unknown) {
    return { error: error instanceof Error ? error : new Error(String(error)) };
  }

  override componentDidCatch(error: unknown) {
    console.error('Desk screen error', error);
  }

  override render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const whole = this.props.scope === 'whole';
    return (
      <div className={whole ? 'error-boundary error-boundary-whole' : 'error-boundary'} role="alert">
        <EmptyState
          title={whole ? 'Desk hit an error' : 'This screen hit an error'}
          action={
            <div className="error-boundary-actions">
              {whole ? null : (
                <Button variant="primary" onClick={() => this.setState({ error: null })}>
                  Try again
                </Button>
              )}
              <Button variant={whole ? 'primary' : 'secondary'} onClick={() => window.location.reload()}>
                Reload Desk
              </Button>
            </div>
          }
        >
          {whole ? 'Reload to continue. Your projects and threads are safe; they live in deskd.' : 'Other screens still work. Your projects and threads are safe; they live in deskd.'}
        </EmptyState>
        <pre className="error-boundary-detail">{error.message}</pre>
      </div>
    );
  }
}
