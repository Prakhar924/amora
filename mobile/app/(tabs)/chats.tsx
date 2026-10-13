/**
 * The chat list.
 *
 * Live-updates from the socket: an incoming message moves its conversation to
 * the top and bumps the unread badge, without refetching the list.
 */

import { useCallback, useState } from 'react';
import { FlatList, Pressable, RefreshControl, Text, View } from 'react-native';
import { Image } from 'expo-image';
import { useFocusEffect, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ApiError, chat } from '@/lib/api';
import { useAuth } from '@/lib/auth-context';
import { useSocket, useSocketEvent } from '@/lib/socket';
import { Button, Empty, ErrorState, Loading } from '@/components/ui';
import type { Conversation } from '@/lib/types';

function relativeTime(iso: string | null): string {
  if (!iso) return '';
  const diffMs = Date.now() - new Date(iso).getTime();
  const minutes = Math.floor(diffMs / 60_000);
  if (minutes < 1) return 'now';
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d`;
  return new Date(iso).toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
  });
}

export default function Chats() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { user } = useAuth();
  const { state: socketState } = useSocket();

  const [items, setItems] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const response = await chat.conversations();
      setItems(response.conversations);
      setError(null);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : 'Could not load your chats.',
      );
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      void load();
    }, [load]),
  );

  useSocketEvent((frame) => {
    if (frame.type === 'message.created') {
      const { message } = frame;
      setItems((current) => {
        const index = current.findIndex((c) => c.id === message.conversation_id);
        if (index === -1) {
          // A conversation we have not loaded yet (a brand-new match).
          void load();
          return current;
        }
        const existing = current[index];
        if (!existing) return current;

        const updated: Conversation = {
          ...existing,
          last_message_preview: message.body.slice(0, 160),
          last_message_at: message.created_at,
          unread_count:
            message.sender_id === user?.id
              ? existing.unread_count
              : existing.unread_count + 1,
        };
        // Move it to the top: most recent activity first.
        const rest = current.filter((c) => c.id !== message.conversation_id);
        return [updated, ...rest];
      });
    } else if (frame.type === 'match.removed') {
      void load();
    }
  });

  if (loading) return <Loading label="Loading your chats…" />;
  if (error && items.length === 0) {
    return <ErrorState message={error} onRetry={() => void load()} />;
  }

  const totalUnread = items.reduce((sum, c) => sum + c.unread_count, 0);

  return (
    <View
      className="flex-1 bg-ink-50 dark:bg-ink-900"
      style={{ paddingTop: insets.top + 8 }}
    >
      <View className="px-6 pb-3 flex-row items-center justify-between">
        <View>
          <Text className="font-display text-3xl text-ink-900 dark:text-ink-50">
            Chats
          </Text>
          <Text className="text-ink-400 text-sm mt-1">
            {totalUnread > 0
              ? `${totalUnread} unread`
              : items.length > 0
                ? 'All caught up'
                : 'No conversations yet'}
          </Text>
        </View>

        {/* Honest connection indicator rather than pretending to be live. */}
        {socketState !== 'open' ? (
          <View className="flex-row items-center gap-1.5 rounded-pill bg-warning/10 px-3 py-1.5">
            <View className="w-2 h-2 rounded-pill bg-warning" />
            <Text className="text-warning text-xs font-medium">
              {socketState === 'connecting' ? 'Connecting' : 'Offline'}
            </Text>
          </View>
        ) : null}
      </View>

      {items.length === 0 ? (
        <Empty
          emoji="💬"
          title="No conversations yet"
          body="Once you match with someone, your chat will appear here. Matches never expire, so there is no rush."
          action={
            <Button
              label="Find people"
              onPress={() => router.replace('/(tabs)/discover')}
            />
          }
        />
      ) : (
        <FlatList
          data={items}
          keyExtractor={(item) => item.id}
          contentContainerClassName="pb-6"
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => {
                setRefreshing(true);
                void load();
              }}
              tintColor="#E0366B"
            />
          }
          renderItem={({ item }) => (
            <Pressable
              onPress={() =>
                router.push({
                  pathname: '/chat/[id]',
                  params: {
                    id: item.id,
                    name: item.other_user.display_name,
                    userId: item.other_user.user_id,
                    matchId: item.match_id,
                  },
                })
              }
              className="flex-row items-center gap-3 px-6 py-3 active:bg-ink-100 dark:active:bg-ink-800"
            >
              <View className="w-14 h-14 rounded-pill overflow-hidden bg-ink-200 dark:bg-ink-700">
                {item.other_user.photos[0] ? (
                  <Image
                    source={{ uri: item.other_user.photos[0].url }}
                    style={{ width: '100%', height: '100%' }}
                    contentFit="cover"
                    cachePolicy="memory"
                  />
                ) : (
                  <View className="flex-1 items-center justify-center">
                    <Text className="text-xl">🌸</Text>
                  </View>
                )}
              </View>

              <View className="flex-1">
                <View className="flex-row items-center justify-between">
                  <Text
                    className={`text-base ${
                      item.unread_count > 0
                        ? 'font-bold text-ink-900 dark:text-ink-50'
                        : 'font-medium text-ink-800 dark:text-ink-100'
                    }`}
                    numberOfLines={1}
                  >
                    {item.other_user.display_name}
                  </Text>
                  <Text className="text-xs text-ink-400">
                    {relativeTime(item.last_message_at)}
                  </Text>
                </View>

                <View className="flex-row items-center justify-between mt-0.5">
                  <Text
                    className={`flex-1 text-sm ${
                      item.unread_count > 0
                        ? 'text-ink-700 dark:text-ink-200 font-medium'
                        : 'text-ink-400'
                    }`}
                    numberOfLines={1}
                  >
                    {item.last_message_preview ?? 'You matched — say hello 👋'}
                  </Text>
                  {item.unread_count > 0 ? (
                    <View className="ml-2 min-w-5 h-5 px-1.5 rounded-pill bg-rose-500 items-center justify-center">
                      <Text className="text-white text-xs font-bold">
                        {item.unread_count > 9 ? '9+' : item.unread_count}
                      </Text>
                    </View>
                  ) : null}
                </View>
              </View>
            </Pressable>
          )}
        />
      )}
    </View>
  );
}
