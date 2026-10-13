/**
 * A notice shown only on a hosted web build (e.g. GitHub Pages).
 *
 * The published demo is a static bundle with no backend behind it: the API
 * client points at `http://localhost:8000` because that is where the stack
 * runs. Without this, a visitor would tap "Sign in", get a generic "cannot
 * reach the server" error, and have no idea why.
 *
 * It renders nothing on native, on localhost, and once an API URL has been
 * configured explicitly via `EXPO_PUBLIC_API_URL`.
 */

import { Linking, Platform, Pressable, Text, View } from 'react-native';

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '::1', '']);
const REPO_URL = 'https://github.com/Prakhar924/amora';

function isHostedDemo(): boolean {
  if (Platform.OS !== 'web') return false;
  // An explicitly configured API means someone has pointed this build at a
  // real backend, so the warning would be wrong.
  if (process.env.EXPO_PUBLIC_API_URL) return false;
  if (typeof window === 'undefined') return false;
  return !LOCAL_HOSTS.has(window.location.hostname);
}

export function DemoNotice() {
  if (!isHostedDemo()) return null;

  return (
    <View className="rounded-2xl border border-white/25 bg-black/35 px-4 py-3 mb-4">
      <Text className="text-white font-semibold text-sm mb-1">
        Interface demo — no backend attached
      </Text>
      <Text className="text-ink-200 text-xs leading-5">
        This is the real app bundle, but it is served as a static page with no
        server behind it, so signing in will not work here. To use it for real,
        clone the repo and run{' '}
        <Text className="text-rose-300">docker compose up --build</Text>, then
        open the app at localhost:8081.
      </Text>
      <Pressable
        onPress={() => void Linking.openURL(REPO_URL)}
        className="mt-2 self-start"
        accessibilityRole="link"
      >
        <Text className="text-rose-300 text-xs font-semibold underline">
          View the source and setup guide →
        </Text>
      </Pressable>
    </View>
  );
}
