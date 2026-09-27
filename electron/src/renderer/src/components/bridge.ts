import type { VoiceStudioBridge } from '@/lib/bridge-types';

export type { VoiceStudioBridge };

/** The optional host bridge; null in the web app (and in vitest unless a test stubs it). */
export function getBridge(): VoiceStudioBridge | null {
  if (typeof window === 'undefined') return null;
  return 'voicestudio' in window && window.voicestudio ? window.voicestudio : null;
}

export function appVersion(): string {
  return getBridge()?.app.version ?? __APP_VERSION__;
}

export function isMac(): boolean {
  return getBridge()?.app.platform === 'darwin';
}
