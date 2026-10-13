/**
 * Moderation dashboard.
 *
 * Reachable only by an admin: the backend answers 404 to everyone else, so a
 * non-admin who navigates here sees the error state rather than a usable
 * screen. The client check is convenience, not the control.
 */

import { useCallback, useEffect, useState } from 'react';
import { Pressable, RefreshControl, ScrollView, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ApiError, admin } from '@/lib/api';
import { Card, ErrorState, Loading } from '@/components/ui';
import type { AdminStats } from '@/lib/types';

export default function AdminDashboard() {
  const router = useRouter();
  const insets = useSafeAreaInsets();

  const [stats, setStats] = useState<AdminStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setStats(await admin.stats());
      setError(null);
    } catch (caught) {
      setError(
        caught instanceof ApiError && caught.status === 404
          ? 'This area is for moderators only.'
          : caught instanceof ApiError
            ? caught.message
            : 'Could not load the dashboard.',
      );
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) return <Loading />;
  if (error || !stats) {
    return (
      <ErrorState
        message={error ?? 'Dashboard unavailable.'}
        onRetry={() => void load()}
      />
    );
  }

  return (
    <ScrollView
      className="flex-1 bg-ink-50 dark:bg-ink-900"
      contentContainerClassName="px-6 pb-16"
      contentContainerStyle={{ paddingTop: insets.top + 16 }}
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
    >
      <View className="flex-row items-center gap-2 mb-6">
        <Pressable
          onPress={() => router.back()}
          accessibilityLabel="Back"
          className="w-9 h-9 items-center justify-center -ml-2"
        >
          <Text className="text-2xl text-ink-600 dark:text-ink-300">‹</Text>
        </Pressable>
        <Text className="font-display text-3xl text-ink-900 dark:text-ink-50">
          Moderation
        </Text>
      </View>

      {/* Queues first: these are the things that need a human. */}
      <Text className="text-xs uppercase tracking-wide text-ink-400 mb-2">
        Needs attention
      </Text>
      <View className="gap-3 mb-6">
        <Queue
          emoji="🖼️"
          label="Photo review queue"
          count={stats.pending_photo_moderation}
          onPress={() => router.push('/admin/photos')}
        />
        <Queue
          emoji="🚩"
          label="Open reports"
          count={stats.open_reports}
          onPress={() => router.push('/admin/reports')}
        />
      </View>

      <Text className="text-xs uppercase tracking-wide text-ink-400 mb-2">
        Platform
      </Text>
      <View className="flex-row flex-wrap gap-3">
        <Stat label="Total users" value={stats.total_users} />
        <Stat label="Active users" value={stats.active_users} />
        <Stat label="Matches made" value={stats.matches_created} />
        <Stat label="Messages sent" value={stats.messages_sent} />
      </View>
    </ScrollView>
  );
}

function Queue({
  emoji,
  label,
  count,
  onPress,
}: {
  emoji: string;
  label: string;
  count: number;
  onPress: () => void;
}) {
  return (
    <Pressable onPress={onPress} className="active:opacity-70">
      <Card className="flex-row items-center gap-4">
        <Text className="text-3xl">{emoji}</Text>
        <View className="flex-1">
          <Text className="text-base font-medium text-ink-900 dark:text-ink-50">
            {label}
          </Text>
          <Text className="text-sm text-ink-400 mt-0.5">
            {count === 0 ? 'Nothing waiting' : `${count} waiting`}
          </Text>
        </View>
        {count > 0 ? (
          <View className="min-w-7 h-7 px-2 rounded-pill bg-rose-500 items-center justify-center">
            <Text className="text-white text-xs font-bold">{count}</Text>
          </View>
        ) : null}
        <Text className="text-ink-300 text-lg">›</Text>
      </Card>
    </Pressable>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <Card className="flex-1 min-w-[45%]">
      <Text className="font-display text-2xl text-ink-900 dark:text-ink-50">
        {value.toLocaleString()}
      </Text>
      <Text className="text-xs text-ink-400 mt-1">{label}</Text>
    </Card>
  );
}
