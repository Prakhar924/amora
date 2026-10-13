/**
 * Visibility controls.
 *
 * Both switches map to real backend behaviour, enforced in the discovery
 * query rather than by hiding things in the UI:
 *
 * * `is_discoverable: false` removes the profile from everyone's feed while
 *   leaving existing matches and chats working.
 * * `show_only_to_matches: true` additionally hides the profile from anyone
 *   who is not already a match.
 */

import { useCallback, useEffect, useState } from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ApiError, profiles } from '@/lib/api';
import {
  Banner,
  Card,
  Divider,
  ErrorState,
  Loading,
} from '@/components/ui';
import type { MyProfile } from '@/lib/types';

export default function PrivacySettings() {
  const router = useRouter();
  const insets = useSafeAreaInsets();

  const [profile, setProfile] = useState<MyProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setProfile(await profiles.mine());
      setError(null);
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : 'Could not load settings.',
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function update(changes: Partial<MyProfile>) {
    if (!profile) return;
    // Optimistic, then reconciled from the server's response.
    setProfile({ ...profile, ...changes });
    try {
      const saved = await profiles.update({
        is_discoverable: changes.is_discoverable ?? profile.is_discoverable,
        show_only_to_matches:
          changes.show_only_to_matches ?? profile.show_only_to_matches,
      });
      setProfile(saved);
      setNotice('Saved.');
      setTimeout(() => setNotice(null), 2000);
    } catch (caught) {
      setProfile(profile); // roll back
      setNotice(
        caught instanceof ApiError ? caught.message : 'Could not save that.',
      );
    }
  }

  if (loading) return <Loading />;
  if (error || !profile) {
    return (
      <ErrorState
        message={error ?? 'Settings unavailable.'}
        onRetry={() => void load()}
      />
    );
  }

  return (
    <ScrollView
      className="flex-1 bg-ink-50 dark:bg-ink-900"
      contentContainerClassName="px-6 pb-16"
      contentContainerStyle={{ paddingTop: insets.top + 16 }}
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
          Privacy
        </Text>
      </View>

      {notice ? <Banner message={notice} tone="info" /> : null}

      <Card className="mb-4">
        <Switch
          label="Show me in discovery"
          description="Turn this off to take a break. Your existing matches and chats keep working."
          value={profile.is_discoverable}
          onChange={(value) => void update({ is_discoverable: value })}
        />
        <Divider />
        <Switch
          label="Only visible to matches"
          description="Your profile is hidden from anyone you have not already matched with."
          value={profile.show_only_to_matches}
          onChange={(value) => void update({ show_only_to_matches: value })}
        />
      </Card>

      <Card>
        <Text className="font-semibold text-ink-800 dark:text-ink-100 mb-2">
          What others can see
        </Text>
        <Bullet text="Your first name, age, photos, bio and interests." />
        <Bullet text="Your city, and a distance rounded to the nearest kilometre." />
        <Divider />
        <Text className="font-semibold text-ink-800 dark:text-ink-100 mb-2">
          What they can never see
        </Text>
        <Bullet text="Your exact location. Coordinates are never sent to another user — not even to a match." />
        <Bullet text="Your email address or date of birth." />
        <Bullet text="Whether you have liked someone, unless they like you back." />
      </Card>
    </ScrollView>
  );
}

function Bullet({ text }: { text: string }) {
  return (
    <View className="flex-row gap-2 mb-2">
      <Text className="text-ink-400">•</Text>
      <Text className="flex-1 text-sm text-ink-600 dark:text-ink-300 leading-6">
        {text}
      </Text>
    </View>
  );
}

function Switch({
  label,
  description,
  value,
  onChange,
}: {
  label: string;
  description: string;
  value: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <Pressable
      onPress={() => onChange(!value)}
      accessibilityRole="switch"
      accessibilityState={{ checked: value }}
      className="flex-row items-start justify-between gap-4 py-3"
    >
      <View className="flex-1">
        <Text className="text-base text-ink-800 dark:text-ink-100">{label}</Text>
        <Text className="text-xs text-ink-400 mt-1 leading-5">{description}</Text>
      </View>
      <View
        className={`w-12 h-7 rounded-pill p-0.5 mt-0.5 ${
          value ? 'bg-rose-500' : 'bg-ink-300 dark:bg-ink-600'
        }`}
      >
        <View
          className={`w-6 h-6 rounded-pill bg-white ${value ? 'ml-auto' : ''}`}
        />
      </View>
    </Pressable>
  );
}
