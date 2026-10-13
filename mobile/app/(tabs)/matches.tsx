/**
 * The matches grid.
 *
 * Subscribes to the socket so a match made while this screen is open appears
 * without a pull-to-refresh, and a removed match disappears.
 */

import { useCallback, useEffect, useState } from 'react';
import { FlatList, Pressable, RefreshControl, Text, View } from 'react-native';
import { Image } from 'expo-image';
import { useFocusEffect, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ApiError, matches as matchesApi } from '@/lib/api';
import { useSocketEvent } from '@/lib/socket';
import { Button, Empty, ErrorState, Loading } from '@/components/ui';
import type { Match } from '@/lib/types';

export default function Matches() {
  const router = useRouter();
  const insets = useSafeAreaInsets();

  const [items, setItems] = useState<Match[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const response = await matchesApi.list();
      setItems(response.matches);
      setError(null);
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : 'Could not load matches.',
      );
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  // Refetch whenever the tab regains focus, so a match made on the discover
  // tab is already here when the user switches over.
  useFocusEffect(
    useCallback(() => {
      void load();
    }, [load]),
  );

  useSocketEvent((frame) => {
    if (frame.type === 'match.created') {
      setItems((current) =>
        current.some((m) => m.id === frame.match.id)
          ? current
          : [frame.match, ...current],
      );
    } else if (frame.type === 'match.removed') {
      setItems((current) => current.filter((m) => m.id !== frame.match_id));
    }
  });

  if (loading) return <Loading label="Loading your matches…" />;
  if (error && items.length === 0) {
    return <ErrorState message={error} onRetry={() => void load()} />;
  }

  return (
    <View
      className="flex-1 bg-ink-50 dark:bg-ink-900"
      style={{ paddingTop: insets.top + 8 }}
    >
      <View className="px-6 pb-3">
        <Text className="font-display text-3xl text-ink-900 dark:text-ink-50">
          Matches
        </Text>
        <Text className="text-ink-400 text-sm mt-1">
          {items.length === 0
            ? 'No matches yet'
            : `${items.length} ${items.length === 1 ? 'person' : 'people'} liked you back`}
        </Text>
      </View>

      {items.length === 0 ? (
        <Empty
          emoji="💞"
          title="No matches yet"
          body="When you and someone else both swipe right, they will show up here and you can start a conversation."
          action={
            <Button
              label="Start swiping"
              onPress={() => router.replace('/(tabs)/discover')}
            />
          }
        />
      ) : (
        <FlatList
          data={items}
          keyExtractor={(item) => item.id}
          numColumns={2}
          columnWrapperClassName="gap-3 px-5 justify-start"
          contentContainerClassName="gap-3 pb-6"
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
            <MatchTile
              match={item}
              onPress={() => {
                if (item.conversation_id) {
                  router.push({
                    pathname: '/chat/[id]',
                    params: {
                      id: item.conversation_id,
                      name: item.profile.display_name,
                      userId: item.profile.user_id,
                      matchId: item.id,
                    },
                  });
                } else {
                  router.push({
                    pathname: '/profile/[id]',
                    params: { id: item.profile.user_id },
                  });
                }
              }}
            />
          )}
        />
      )}
    </View>
  );
}

function MatchTile({
  match,
  onPress,
}: {
  match: Match;
  onPress: () => void;
}) {
  const photo = match.profile.photos[0];
  return (
    <Pressable
      onPress={onPress}
      // A percentage width rather than flex-1: with an odd number of
      // matches, flex-1 would stretch the last tile across the whole row.
      className="w-[48%] aspect-[3/4] rounded-card overflow-hidden bg-ink-200 dark:bg-ink-800 active:opacity-80"
    >
      {photo ? (
        <Image
          source={{ uri: photo.url }}
          style={{ width: '100%', height: '100%' }}
          contentFit="cover"
          transition={150}
          cachePolicy="memory"
        />
      ) : (
        <View className="flex-1 items-center justify-center">
          <Text className="text-4xl">🌸</Text>
        </View>
      )}

      <View className="absolute bottom-0 left-0 right-0 h-1/2 bg-black/50" />

      {match.unread_count > 0 ? (
        <View className="absolute top-2.5 right-2.5 min-w-6 h-6 px-1.5 rounded-pill bg-rose-500 items-center justify-center">
          <Text className="text-white text-xs font-bold">
            {match.unread_count > 9 ? '9+' : match.unread_count}
          </Text>
        </View>
      ) : null}

      <View className="absolute bottom-0 left-0 right-0 p-3">
        <Text className="text-white font-semibold text-base" numberOfLines={1}>
          {match.profile.display_name}, {match.profile.age}
        </Text>
        <Text className="text-white/75 text-xs mt-0.5" numberOfLines={1}>
          {match.last_message_preview ?? 'Say hello 👋'}
        </Text>
      </View>
    </Pressable>
  );
}
