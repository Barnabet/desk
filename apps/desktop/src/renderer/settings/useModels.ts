import { useEffect, useState } from 'react';
import type { ModelInfo } from '@desk/protocol';
import { call } from '../bridge';

/** The model registry, fetched once per mount; null while loading or unavailable. */
export function useModels(): ModelInfo[] | null {
  const [models, setModels] = useState<ModelInfo[] | null>(null);
  useEffect(() => {
    let live = true;
    call('models.list', {})
      .then((m) => live && setModels(m))
      .catch(() => live && setModels(null));
    return () => {
      live = false;
    };
  }, []);
  return models;
}
