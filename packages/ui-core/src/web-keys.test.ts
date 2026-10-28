import { describe, expect, it } from 'vitest';
import { webKeyRows } from './web-keys';

describe('webKeyRows', () => {
  it('describes each provider, what it powers and where to get a key', () => {
    const rows = webKeyRows({ serper: { set: false, source: null }, firecrawl: { set: true, source: 'keychain' } });
    expect(rows).toEqual([
      { provider: 'serper', name: 'Serper', powers: 'Search', statusLabel: 'Not set', editable: true, removable: false, dashboardUrl: 'https://serper.dev/api-key' },
      {
        provider: 'firecrawl',
        name: 'Firecrawl',
        powers: 'Reading pages, maps, crawls and extraction',
        statusLabel: 'Set',
        editable: true,
        removable: true,
        dashboardUrl: 'https://www.firecrawl.dev/app/api-keys',
      },
    ]);
  });

  it('leaves environment keys alone', () => {
    const [serper] = webKeyRows({ serper: { set: true, source: 'env' }, firecrawl: { set: false, source: null } });
    expect(serper).toMatchObject({ statusLabel: 'Set from environment', editable: false, removable: false });
  });
});
