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

/** The WebSocket endpoint, derived from the API URL. */
export const WS_URL = API_BASE_URL.replace(/^http/, 'ws').replace(
  /\/api\/v1$/,
  '/ws/chat',
);

export const APP_NAME = 'Amora';
export const MINIMUM_AGE = 18;
export const MAX_PHOTOS = 6;
