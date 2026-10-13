/**
 * Runtime configuration.
 *
 * The API host is resolved at startup rather than hardcoded, because the
 * correct value differs per target:
 *
 * * Web / iOS simulator — `localhost` works.
 * * Android emulator — `localhost` is the emulator itself; the host machine
 *   is `10.0.2.2`.
 * * A physical device — needs the host's LAN address, which Expo already
 *   knows (it is serving the bundle), so we reuse it.
 *
 * Set `EXPO_PUBLIC_API_URL` to override all of this.
 */

import Constants from 'expo-constants';
import { Platform } from 'react-native';

const DEFAULT_PORT = 8000;

function inferHost(): string {
  // The address Expo is serving the bundle from, e.g. "192.168.1.5:8081".
  // `hostUri` lives in different places depending on how the app was
  // launched (dev client, Expo Go, a bare build), so probe both.
  const constants = Constants as unknown as {
    expoConfig?: { hostUri?: string };
    manifest2?: { extra?: { expoClient?: { hostUri?: string } } };
  };
  const hostUri =
    constants.expoConfig?.hostUri ??
    constants.manifest2?.extra?.expoClient?.hostUri;

  if (typeof hostUri === 'string' && hostUri.length > 0) {
    const host = hostUri.split(':')[0];
    // A LAN address works for a real device; localhost does not.
    if (host && host !== 'localhost' && host !== '127.0.0.1') {
      return host;
    }
  }

  if (Platform.OS === 'android') {
    // The Android emulator's alias for the host machine's loopback.
    return '10.0.2.2';
  }
  return 'localhost';
}

function resolveBaseUrl(): string {
  const override = process.env.EXPO_PUBLIC_API_URL;
  if (override) return override.replace(/\/$/, '');
  return `http://${inferHost()}:${DEFAULT_PORT}/api/v1`;
}

export const API_BASE_URL = resolveBaseUrl();

/**
 * Derive the WebSocket endpoint from the API URL.
 *
 * Two things this must get right:
 *
 * * **Scheme.** `https` becomes `wss`, so the socket inherits TLS. A browser
 *   refuses a plain `ws://` connection from an `https://` page (mixed
 *   content), so getting this wrong silently breaks chat in production only.
 * * **Path.** The socket is mounted at the server root (`/ws/chat`), not
 *   under the versioned API prefix, so any `/api/vN` suffix is stripped
 *   rather than assumed. Writing this as a plain `.replace(/\/api\/v1$/)`
 *   silently produced a wrong URL whenever the configured value had no
 *   version suffix.
 */
function resolveWebSocketUrl(apiBaseUrl: string): string {
  const scheme = apiBaseUrl.startsWith('https://') ? 'wss://' : 'ws://';
  const withoutScheme = apiBaseUrl.replace(/^https?:\/\//, '');
  // Drop a trailing API prefix of any version, e.g. "/api/v1" or "/api/v2".
  const root = withoutScheme.replace(/\/api\/v\d+\/?$/, '').replace(/\/$/, '');
  return `${scheme}${root}/ws/chat`;
}

export const WS_URL = resolveWebSocketUrl(API_BASE_URL);

/**
 * True when the page is served over HTTPS but the API is plain HTTP.
 *
 * Browsers block these requests as mixed content, and the resulting failure
 * looks like a server outage rather than a configuration mistake, so it is
 * surfaced explicitly at startup.
 */
export const HAS_MIXED_CONTENT_PROBLEM: boolean =
  Platform.OS === 'web' &&
  typeof window !== 'undefined' &&
  window.location.protocol === 'https:' &&
  API_BASE_URL.startsWith('http://') &&
  !API_BASE_URL.includes('localhost') &&
  !API_BASE_URL.includes('127.0.0.1');

if (HAS_MIXED_CONTENT_PROBLEM) {
  console.error(
    `[amora] The app is served over HTTPS but EXPO_PUBLIC_API_URL is ` +
      `${API_BASE_URL}. The browser will block every request as mixed ` +
      `content. Rebuild with an https:// API URL.`,
  );
}

export const APP_NAME = 'Amora';
export const MINIMUM_AGE = 18;
export const MAX_PHOTOS = 6;
