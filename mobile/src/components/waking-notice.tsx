/**
 * "The server is waking up" banner.
 *
 * Free hosting tiers suspend a service after a period of inactivity, and the
 * first request afterwards takes roughly a minute while the container starts.
 * Without this the app simply appears frozen, so people tap the button again,
 * queue more requests, and conclude it is broken.
 *
 * It shows only when a request has genuinely been slow — it is driven by the
 * API client, not by a timer — and disappears as soon as one completes.
 */

import { useEffect, useState } from 'react';
import { ActivityIndicator, Text, View } from 'react-native';

import { subscribeToSlowRequests } from '@/lib/api';

export function WakingNotice() {
  const [isSlow, setIsSlow] = useState(false);

  useEffect(() => subscribeToSlowRequests(setIsSlow), []);

  if (!isSlow) return null;

  return (
    // Themed rather than translucent-on-dark: this appears on the light
    // sign-in screen as well as the dark welcome screen, and a single
    // semi-transparent treatment was unreadable on one of them.
    <View className="flex-row items-center gap-3 rounded-2xl border border-rose-200 bg-rose-50 dark:border-ink-600 dark:bg-ink-800 px-4 py-3 mb-4">
      <ActivityIndicator size="small" color="#E0366B" />
      <Text className="flex-1 text-ink-600 dark:text-ink-200 text-xs leading-5">
        Waking the server up — this can take up to a minute on the free hosting
        tier. It stays fast afterwards.
      </Text>
    </View>
  );
}
