import { useState } from 'react';
import type { CatalogEntry, CatalogInstall } from '@desk/protocol';
import { call } from '../../bridge';
import { Button } from '../../components/Button';
import { toast, toastError } from '../../components/Toast';
import { useGlobal } from '../../state/global';
import { runtimeKey } from '../../../shared/state';
import { scopeArg } from '../data';
import { installRef, runtimeWords } from './data';

/** An installed catalog skill's environment: Ready, Setting up… (with live progress), or Failed with Retry. */
export function RuntimeLine({ entry, install, onRetried }: { entry: Pick<CatalogEntry, 'id' | 'runtime'>; install: CatalogInstall; onRetried?(): void }) {
  const progress = useGlobal((g) => g.runtimes.progress[runtimeKey(install.scope, install.project_id, entry.id)]);
  const [pending, setPending] = useState(false);
  if (install.runtime === 'none') return null;
  const retry = async () => {
    setPending(true);
    try {
      await call('skills.runtimeRetry', { ...scopeArg(installRef(entry.id, install)), name: entry.id });
      toast({ tone: 'info', message: `Setting up ${entry.id} again.` });
      onRetried?.();
    } catch (err) {
      toastError(err);
    } finally {
      setPending(false);
    }
  };
  if (install.runtime === 'ready') {
    return (
      <p className="runtime-line ready" role="status">
        <span className="runtime-dot" aria-hidden="true" />
        Ready · {runtimeWords(entry)}
      </p>
    );
  }
  if (install.runtime === 'preparing') {
    return (
      <p className="runtime-line preparing" role="status">
        <span className="runtime-dot" aria-hidden="true" />
        Setting up…{progress ? ` ${progress.step}${progress.total ? ` (${(progress.done ?? 0) + 1} of ${progress.total})` : ''}` : ''}
      </p>
    );
  }
  return (
    <div className="runtime-line failed" role="alert">
      <span className="runtime-dot" aria-hidden="true" />
      <span className="grow">
        Setup failed{install.runtime_reason ? `: ${install.runtime_reason}` : '.'} Agents can't run this skill's scripts until it's set up.
      </span>
      <Button size="sm" pending={pending} onClick={() => void retry()}>
        Retry
      </Button>
    </div>
  );
}
