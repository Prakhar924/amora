/**
 * Photo moderation queue.
 *
 * One photo at a time rather than a grid: a moderator making a judgement
 * about someone's photo should see it at a reasonable size, and a grid of
 * thumbnails encourages rubber-stamping.
 */

import { useCallback, useEffect, useState } from 'react';
import { Pressable, Text, TextInput, View } from 'react-native';
import { Image } from 'expo-image';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ApiError, admin } from '@/lib/api';
import { Banner, Button, Empty, ErrorState, Loading } from '@/components/ui';
import type { AdminPhoto } from '@/lib/types';

export default function PhotoQueue() {
  const router = useRouter();
  const insets = useSafeAreaInsets();

  const [queue, setQueue] = useState<AdminPhoto[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [note, setNote] = useState('');

  const load = useCallback(async () => {
    try {
      setQueue(await admin.photoQueue('pending'));
      setError(null);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : 'Could not load the queue.',
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function decide(photo: AdminPhoto, status: 'approved' | 'rejected') {
    setBusy(true);
    try {
      await admin.moderatePhoto(photo.id, status, note.trim() || undefined);
      setQueue((current) => current.filter((p) => p.id !== photo.id));
      setNote('');
      setNotice(
        status === 'approved'
          ? `Approved ${photo.display_name}'s photo.`
          : `Rejected ${photo.display_name}'s photo.`,
      );
    } catch (caught) {
      setNotice(
        caught instanceof ApiError
          ? caught.message
          : 'Could not record that decision.',
      );
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <Loading label="Loading the queue…" />;
  if (error) return <ErrorState message={error} onRetry={() => void load()} />;

  const current = queue[0];

  return (
    <View
      className="flex-1 bg-ink-50 dark:bg-ink-900"
      style={{ paddingTop: insets.top + 16 }}
    >
      <View className="flex-row items-center gap-2 px-6 mb-3">
        <Pressable
          onPress={() => router.back()}
          accessibilityLabel="Back"
          className="w-9 h-9 items-center justify-center -ml-2"
        >
          <Text className="text-2xl text-ink-600 dark:text-ink-300">‹</Text>
        </Pressable>
        <View className="flex-1">
          <Text className="font-display text-2xl text-ink-900 dark:text-ink-50">
            Photo review
          </Text>
          <Text className="text-xs text-ink-400">
            {queue.length} waiting
          </Text>
        </View>
      </View>

      {notice ? (
        <View className="px-6">
          <Banner message={notice} tone="info" />
        </View>
      ) : null}

      {!current ? (
        <Empty
          emoji="✨"
          title="Queue is clear"
          body="Every submitted photo has been reviewed. New uploads will appear here."
        />
      ) : (
        <View className="flex-1 px-6">
          <View className="flex-1 rounded-card overflow-hidden bg-ink-200 dark:bg-ink-800 mb-4">
            <Image
              source={{ uri: current.url }}
              style={{ width: '100%', height: '100%' }}
              contentFit="contain"
              cachePolicy="memory"
            />
          </View>

          <Text className="text-base font-medium text-ink-900 dark:text-ink-50">
            {current.display_name}
          </Text>
          <Text className="text-xs text-ink-400 mb-3">
            Uploaded {new Date(current.created_at).toLocaleString()}
          </Text>

          <TextInput
            value={note}
            onChangeText={setNote}
            placeholder="Reason, if rejecting (shown to the user)"
            placeholderTextColor="#9B8C94"
            maxLength={500}
            className="rounded-2xl px-4 py-3 bg-white dark:bg-ink-800 border border-ink-200 dark:border-ink-700 text-ink-900 dark:text-ink-50 mb-3"
          />

          <View
            className="flex-row gap-3"
            style={{ paddingBottom: insets.bottom + 12 }}
          >
            <View className="flex-1">
              <Button
                label="Reject"
                variant="danger"
                onPress={() => void decide(current, 'rejected')}
                disabled={busy}
              />
            </View>
            <View className="flex-1">
              <Button
                label="Approve"
                onPress={() => void decide(current, 'approved')}
                disabled={busy}
              />
            </View>
          </View>
        </View>
      )}
    </View>
  );
}
