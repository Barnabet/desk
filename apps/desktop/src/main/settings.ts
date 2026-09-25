import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { AppSettings, type AppSettingsPatch } from '@desk/bff/contract';

const DEFAULTS: AppSettings = { notifications: true };

function load(file: string): AppSettings {
  try {
    return AppSettings.parse({ ...DEFAULTS, ...(JSON.parse(readFileSync(file, 'utf8')) as object) });
  } catch {
    return { ...DEFAULTS };
  }
}

/** The app's own preferences (not the daemon's), in `userData/settings.json`. */
export class AppSettingsStore {
  private value: AppSettings;

  constructor(private readonly file: string) {
    this.value = load(file);
  }

  get(): AppSettings {
    return { ...this.value };
  }

  update(patch: AppSettingsPatch): AppSettings {
    const defined = Object.fromEntries(Object.entries(patch).filter(([, v]) => v !== undefined));
    this.value = AppSettings.parse({ ...this.value, ...defined });
    mkdirSync(dirname(this.file), { recursive: true });
    writeFileSync(this.file, `${JSON.stringify(this.value, null, 2)}\n`);
    return this.get();
  }
}
