/**
 * The swipe deck.
 *
 * Behaviour worth knowing:
 *
 * * Cards are consumed from a local queue and the next page is prefetched
 *   when the queue runs low, so swiping never stalls on a network round trip.
 * * A swipe is applied optimistically: the card leaves immediately and the
 *   API call happens behind it. If the call fails the card is put back, so a
 *   dropped connection cannot silently discard someone.
 * * A new match routes to the celebration screen.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as Haptics from 'expo-haptics';

import { ApiError, discovery } from '@/lib/api';
import { ProfileCard } from '@/components/profile-card';
import { Button, Empty, ErrorState, Loading } from '@/components/ui';
import { APP_NAME } from '@/lib/config';
import type { PublicProfile, SwipeAction } from '@/lib/types';

const PREFETCH_THRESHOLD = 3;

export default function Discover() {
  const router = useRouter();
  const insets = useSafeAreaInsets();

  const [queue, setQueue] = useState<PublicProfile[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [exhausted, setExhausted] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  // Guards against two prefetches racing when several swipes land together.
  const fetching = useRef(false);

  const loadPage = useCallback(
    async (nextCursor: string | null, replace = false) => {
      if (fetching.current) return;
      fetching.current = true;
      try {
        const feed = await discovery.feed({
          cursor: nextCursor ?? undefined,
          limit: 10,
        });
        setQueue((current) =>
          replace ? feed.profiles : [...current, ...feed.profiles],
        );
        setCursor(feed.next_cursor);
        setExhausted(feed.exhausted);
        setError(null);
      } catch (caught) {
        setError(
          caught instanceof ApiError
            ? caught.message
            : 'Could not load profiles.',
        );
      } finally {
        fetching.current = false;
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    void loadPage(null, true);
  }, [loadPage]);

  // Top up the queue before it empties.
  useEffect(() => {
    if (!loading && queue.length <= PREFETCH_THRESHOLD && cursor && !exhausted) {
      void loadPage(cursor);
    }
  }, [queue.length, cursor, exhausted, loading, loadPage]);

  const decide = useCallback(
    async (profile: PublicProfile, action: SwipeAction) => {
      // Optimistic: drop the card now, reconcile afterwards.
      setQueue((current) => current.filter((p) => p.user_id !== profile.user_id));

      if (action === 'like' || action === 'superlike') {
        void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
      }

      try {
        const result = await discovery.swipe(profile.user_id, action);
        if (result.is_match && result.match) {
          void Haptics.notificationAsync(
            Haptics.NotificationFeedbackType.Success,
          );
          router.push({
            pathname: '/match/[id]',
            params: {
              id: result.match.id,
              name: result.match.profile.display_name,
              photo: result.match.profile.photos[0]?.url ?? '',
              conversationId: result.match.conversation_id ?? '',
            },
          });
        }
      } catch (caught) {
        // Put the profile back rather than losing it to a failed request.
        setQueue((current) => [profile, ...current]);
        setToast(
          caught instanceof ApiError
            ? caught.message
            : 'That did not go through. Try again.',
        );
        setTimeout(() => setToast(null), 3200);
      }
    },
    [router],
  );

  if (loading && queue.length === 0) {
    return <Loading label="Finding people near you…" />;
  }

  if (error && queue.length === 0) {
    return (
      <ErrorState message={error} onRetry={() => void loadPage(null, true)} />
    );
  }

  const top = queue[0];
  const next = queue[1];

  return (
    <View
      className="flex-1 bg-ink-50 dark:bg-ink-900"
      style={{ paddingTop: insets.top + 8 }}
    >
      <View className="flex-row items-center justify-between px-6 pb-3">
        <Text className="font-display text-2xl text-ink-900 dark:text-ink-50">
          {APP_NAME}
        </Text>
        <Pressable
          onPress={() => router.push('/settings/preferences')}
          accessibilityLabel="Discovery preferences"
          className="w-10 h-10 items-center justify-center rounded-pill bg-white dark:bg-ink-800"
        >
          <Text className="text-lg">⚙️</Text>
        </Pressable>
      </View>

      {toast ? (
        <View className="mx-6 mb-2 rounded-2xl bg-danger/10 border border-danger/30 px-4 py-3">
          <Text className="text-danger text-sm">{toast}</Text>
        </View>
      ) : null}

      {top === undefined ? (
        <Empty
          emoji="🌱"
          title="That is everyone for now"
          body="You have seen all the profiles that match your filters. Widen your age range or distance to see more people."
          action={
            <View className="gap-3">
              <Button
                label="Adjust preferences"
                onPress={() => router.push('/settings/preferences')}
              />
              <Button
                label="Refresh"
                variant="secondary"
                onPress={() => {
                  setLoading(true);
                  void loadPage(null, true);
                }}
              />
            </View>
          }
        />
      ) : (
        <>
          <View className="flex-1 mx-5 mb-4">
            {/* The card beneath gives the deck depth; it is not interactive. */}
            {next ? (
              <View className="absolute inset-0 scale-95 opacity-60">
                <ProfileCard
                  key={next.user_id}
                  profile={next}
                  interactive={false}
                  onDecision={() => {}}
                />
              </View>
            ) : null}

            <ProfileCard
              key={top.user_id}
              profile={top}
              onDecision={(action) => void decide(top, action)}
              onOpenProfile={() =>
                router.push({
                  pathname: '/profile/[id]',
                  params: { id: top.user_id },
                })
              }
            />
          </View>

          <View
            className="flex-row items-center justify-center gap-5 px-6"
            style={{ paddingBottom: insets.bottom + 8 }}
          >
            <ActionButton
              emoji="✕"
              label="Pass"
              tone="pass"
              onPress={() => void decide(top, 'pass')}
            />
            <ActionButton
              emoji="★"
              label="Super like"
              tone="super"
              onPress={() => void decide(top, 'superlike')}
            />
            <ActionButton
              emoji="♥"
              label="Like"
              tone="like"
              onPress={() => void decide(top, 'like')}
            />
          </View>
        </>
      )}
    </View>
  );
}

function ActionButton({
  emoji,
  label,
  tone,
  onPress,
}: {
  emoji: string;
  label: string;
  tone: 'pass' | 'super' | 'like';
  onPress: () => void;
}) {
  const styles = {
    pass: 'bg-white dark:bg-ink-800 border-2 border-ink-200 dark:border-ink-700',
    super: 'bg-plum-500',
    like: 'bg-rose-500',
  } as const;
  const text = {
    pass: 'text-ink-500',
    super: 'text-white',
    like: 'text-white',
  } as const;
  const size = tone === 'super' ? 'w-14 h-14' : 'w-16 h-16';

  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={label}
      className={`${size} ${styles[tone]} rounded-pill items-center justify-center active:scale-95`}
    >
      <Text className={`text-2xl ${text[tone]}`}>{emoji}</Text>
    </Pressable>
  );
}
