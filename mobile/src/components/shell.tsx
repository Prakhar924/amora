/**
 * Responsive shell.
 *
 * The app is designed phone-first, but it also ships to the web, where a
 * browser window is far wider than any phone. Stretching a swipe deck or a
 * two-column match grid across 1600px looks broken, so on a wide viewport the
 * app is constrained to a phone-like column and centred, with the brand
 * colour filling the surround.
 *
 * Below the breakpoint — every phone, and a narrow browser window — this
 * renders its children untouched.
 */

import type { ReactNode } from 'react';
import { Platform, useWindowDimensions, View } from 'react-native';

/** Above this width we switch from "fill the screen" to "centred column". */
const WIDE_BREAKPOINT = 700;
/** Roughly a large phone, which is what the layouts are designed against. */
const APP_MAX_WIDTH = 480;

export function ResponsiveShell({ children }: { children: ReactNode }) {
  const { width } = useWindowDimensions();

  // Native is always a phone-sized viewport; no shell needed.
  if (Platform.OS !== 'web' || width <= WIDE_BREAKPOINT) {
    return <>{children}</>;
  }

  return (
    <View className="flex-1 flex-row justify-center bg-ink-900">
      <View
        className="flex-1 bg-ink-50 dark:bg-ink-900"
        style={{ maxWidth: APP_MAX_WIDTH }}
      >
        {children}
      </View>
    </View>
  );
}
