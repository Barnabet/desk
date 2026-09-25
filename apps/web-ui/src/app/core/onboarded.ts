const FLAG = 'desk.onboarded';

/** Whether this browser has been through onboarding (the desktop app's flag and key). */
export function isOnboarded(): boolean {
  try {
    return localStorage.getItem(FLAG) === '1';
  } catch {
    return false;
  }
}

export function markOnboarded(): void {
  try {
    localStorage.setItem(FLAG, '1');
  } catch {
    // Onboarding shows again next visit; harmless.
  }
}
