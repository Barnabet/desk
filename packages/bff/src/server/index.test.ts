import { describe, expect, it } from 'vitest';
import * as server from './index';

describe('@desk/bff/server', () => {
  it('gives a host the broker, the operation handlers and the daemon manager', () => {
    for (const name of ['Broker', 'dispatch', 'handlers', 'toIpcError', 'MAX_ATTACHMENT_BYTES', 'UserFacingError', 'DaemonManager', 'compareVersions', 'isOutdated', 'LAUNCHD_LABEL', 'launchdPlist', 'plistPath', 'notificationFor', 'attentionRoute']) {
      expect(server, name).toHaveProperty(name);
    }
  });
});
