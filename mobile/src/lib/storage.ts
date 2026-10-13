/**
 * Token storage.
 *
 * On a device, tokens go into `expo-secure-store`, which is backed by the iOS
 * keychain and the Android keystore. `expo-secure-store` is not available on
 * web, so there we fall back to `localStorage`.
 *
 * That fallback is a real security difference, not just an implementation
 * detail: `localStorage` is readable by any script on the origin, so a web
 * deployment should be served with a strict CSP. It is called out in the
 * README's production checklist.
 */

import { Platform } from 'react-native';
import * as SecureStore from 'expo-secure-store';

const ACCESS_KEY = 'amora.access_token';
const REFRESH_KEY = 'amora.refresh_token';

const isWeb = Platform.OS === 'web';

async function setItem(key: string, value: string): Promise<void> {
  if (isWeb) {
    try {
      window.localStorage.setItem(key, value);
    } catch {
      // Private browsing can reject writes; the session then lasts only
      // as long as the page is open, which is acceptable.
    }
    return;
  }
  await SecureStore.setItemAsync(key, value);
}

async function getItem(key: string): Promise<string | null> {
  if (isWeb) {
    try {
      return window.localStorage.getItem(key);
    } catch {
      return null;
    }
  }
  return SecureStore.getItemAsync(key);
}

async function removeItem(key: string): Promise<void> {
  if (isWeb) {
    try {
      window.localStorage.removeItem(key);
    } catch {
      /* ignore */
    }
    return;
  }
  await SecureStore.deleteItemAsync(key);
}

export async function setTokens(
  accessToken: string,
  refreshToken: string,
): Promise<void> {
  await Promise.all([
    setItem(ACCESS_KEY, accessToken),
    setItem(REFRESH_KEY, refreshToken),
  ]);
}

export const getAccessToken = () => getItem(ACCESS_KEY);
export const getRefreshToken = () => getItem(REFRESH_KEY);

export async function clearTokens(): Promise<void> {
  await Promise.all([removeItem(ACCESS_KEY), removeItem(REFRESH_KEY)]);
}

export async function hasSession(): Promise<boolean> {
  return (await getRefreshToken()) !== null;
}
