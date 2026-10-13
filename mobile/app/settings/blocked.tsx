/**
 * Blocked people, with the option to unblock.
 *
 * The copy is explicit that unblocking does not put the person back into your
 * deck, because blocking also recorded a mutual pass. Users would otherwise
 * reasonably expect unblocking to be a full undo.
 */

import { useCallback, useEffect, useState } from 'react';
import { FlatList, Pressable, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ApiError, safety } from '@/lib/api';
import { Banner, Empty, ErrorState, Loading } from '@/components/ui';

interface BlockedUser {
  user_id: string;
  display_name: string | null;
  blocked_at: string;
}

export default function BlockedList() {
  const router = useRouter();
  const insets = useSafeAreaInsets();

  const [items, setItems] = useState<BlockedUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setItems(await safety.blocks());
      setError(null);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : 'Could not load your block list.',
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function unblock(user: BlockedUser) {
    try {
      await safety.unblock(user.user_id);
      setItems((current) => current.filter((u) => u.user_id !== user.user_id));
      setNotice(
        `${user.display_name ?? 'They'} can see your profile again, but will not reappear in your deck.`,
      );
    } catch (caught) {
      setNotice(
        caught instanceof ApiError ? caught.message : 'Could not unblock.',
      );
    }
  }

  if (loading) return <Loading />;
  if (error) return <ErrorState message={error} onRetry={() => void load()} />;

  return (
    <View
      className="flex-1 bg-ink-50 dark:bg-ink-900"
      style={{ paddingTop: insets.top + 16 }}
    >
      <View className="flex-row items-center gap-2 px-6 mb-4">
        <Pressable
          onPress={() => router.back()}
          accessibilityLabel="Back"
          className="w-9 h-9 items-center justify-center -ml-2"
        >
          <Text className="text-2xl text-ink-600 dark:text-ink-300">‹</Text>
        </Pressable>
        <Text className="font-display text-3xl text-ink-900 dark:text-ink-50">
          Blocked
        </Text>
      </View>

      {notice ? (
        <View className="px-6">
          <Banner message={notice} tone="info" />
        </View>
      ) : null}

      {items.length === 0 ? (
        <Empty
          emoji="🛡️"
          title="Nobody is blocked"
          body="If someone makes you uncomfortable, you can block them from their profile or from your chat with them. They will never be told."
        />
      ) : (
        <FlatList
          data={items}
          keyExtractor={(item) => item.user_id}
          contentContainerClassName="px-6 pb-10"
          renderItem={({ item }) => (
            <View className="flex-row items-center justify-between py-4 border-b border-ink-200 dark:border-ink-800">
              <View className="flex-1">
                <Text className="text-base text-ink-900 dark:text-ink-50">
                  {item.display_name ?? 'Deleted account'}
                </Text>
                <Text className="text-xs text-ink-400 mt-0.5">
                  Blocked {new Date(item.blocked_at).toLocaleDateString()}
                </Text>
              </View>
              <Pressable
                onPress={() => void unblock(item)}
                className="rounded-pill border border-ink-300 dark:border-ink-600 px-4 py-2 active:opacity-60"
              >
                <Text className="text-sm font-medium text-ink-700 dark:text-ink-200">
                  Unblock
                </Text>
              </Pressable>
            </View>
          )}
        />
      )}
    </View>
  );
}
