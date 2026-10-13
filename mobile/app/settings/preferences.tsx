/**
 * Discovery preferences: who to show, what ages, how far.
 *
 * Age and distance use stepper controls rather than a slider component, which
 * keeps the screen dependency-free and is more precise on a small screen.
 */

import { useCallback, useEffect, useState } from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ApiError, preferences as prefsApi } from '@/lib/api';
import { MINIMUM_AGE } from '@/lib/config';
import {
  Banner,
  Button,
  Card,
  Chip,
  Divider,
  ErrorState,
  Loading,
} from '@/components/ui';
import type { Gender, Preferences } from '@/lib/types';

const GENDER_OPTIONS: { value: Gender; label: string }[] = [
  { value: 'woman', label: 'Women' },
  { value: 'man', label: 'Men' },
  { value: 'nonbinary', label: 'Non-binary people' },
  { value: 'other', label: 'Everyone else' },
];

const DISTANCES = [5, 10, 25, 50, 100, 200, 500];

export default function PreferencesScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();

  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setPrefs(await prefsApi.get());
      setError(null);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : 'Could not load your preferences.',
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function patch(changes: Partial<Preferences>) {
    setPrefs((current) => (current ? { ...current, ...changes } : current));
  }

  function toggleGender(value: Gender) {
    if (!prefs) return;
    const set = new Set(prefs.interested_in);
    if (set.has(value)) set.delete(value);
    else set.add(value);
    patch({ interested_in: Array.from(set) });
  }

  async function save() {
    if (!prefs) return;
    setSaving(true);
    setNotice(null);
    try {
      const saved = await prefsApi.update({
        interested_in: prefs.interested_in,
        min_age: prefs.min_age,
        max_age: prefs.max_age,
        max_distance_km: prefs.max_distance_km,
        notify_new_match: prefs.notify_new_match,
        notify_new_message: prefs.notify_new_message,
        notify_new_like: prefs.notify_new_like,
      });
      setPrefs(saved);
      setNotice('Preferences saved. Your feed has been updated.');
    } catch (caught) {
      setNotice(
        caught instanceof ApiError ? caught.message : 'Could not save.',
      );
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <Loading />;
  if (error || !prefs) {
    return (
      <ErrorState
        message={error ?? 'Preferences unavailable.'}
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
          Who you see
        </Text>
      </View>

      {notice ? <Banner message={notice} tone="success" /> : null}

      <Card className="mb-4">
        <Text className="font-semibold text-ink-800 dark:text-ink-100 mb-1">
          Show me
        </Text>
        <Text className="text-xs text-ink-400 mb-3">
          Leave all unselected to see everyone.
        </Text>
        <View className="flex-row flex-wrap gap-2">
          {GENDER_OPTIONS.map((option) => (
            <Chip
              key={option.value}
              label={option.label}
              selected={prefs.interested_in.includes(option.value)}
              onPress={() => toggleGender(option.value)}
            />
          ))}
        </View>
      </Card>

      <Card className="mb-4">
        <Text className="font-semibold text-ink-800 dark:text-ink-100 mb-3">
          Age range
        </Text>
        <Stepper
          label="Youngest"
          value={prefs.min_age}
          min={MINIMUM_AGE}
          max={prefs.max_age}
          onChange={(value) => patch({ min_age: value })}
        />
        <Divider />
        <Stepper
          label="Oldest"
          value={prefs.max_age}
          min={prefs.min_age}
          max={99}
          onChange={(value) => patch({ max_age: value })}
        />
        <Text className="text-xs text-ink-400 mt-3">
          Nobody under {MINIMUM_AGE} can use Amora, so this cannot go lower.
        </Text>
      </Card>

      <Card className="mb-4">
        <Text className="font-semibold text-ink-800 dark:text-ink-100 mb-1">
          Maximum distance
        </Text>
        <Text className="text-xs text-ink-400 mb-3">
          Currently {prefs.max_distance_km} km
        </Text>
        <View className="flex-row flex-wrap gap-2">
          {DISTANCES.map((km) => (
            <Chip
              key={km}
              label={`${km} km`}
              selected={prefs.max_distance_km === km}
              onPress={() => patch({ max_distance_km: km })}
            />
          ))}
        </View>
      </Card>

      <Card className="mb-6">
        <Text className="font-semibold text-ink-800 dark:text-ink-100 mb-2">
          Notifications
        </Text>
        <Toggle
          label="New matches"
          value={prefs.notify_new_match}
          onChange={(value) => patch({ notify_new_match: value })}
        />
        <Divider />
        <Toggle
          label="New messages"
          value={prefs.notify_new_message}
          onChange={(value) => patch({ notify_new_message: value })}
        />
        <Divider />
        <Toggle
          label="Someone liked you"
          value={prefs.notify_new_like}
          onChange={(value) => patch({ notify_new_like: value })}
        />
      </Card>

      <Button label="Save preferences" onPress={() => void save()} loading={saving} />
    </ScrollView>
  );
}

function Stepper({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (value: number) => void;
}) {
  return (
    <View className="flex-row items-center justify-between py-2">
      <Text className="text-ink-700 dark:text-ink-200">{label}</Text>
      <View className="flex-row items-center gap-4">
        <Pressable
          onPress={() => onChange(Math.max(min, value - 1))}
          disabled={value <= min}
          accessibilityLabel={`Decrease ${label}`}
          className={`w-9 h-9 rounded-pill items-center justify-center bg-ink-100 dark:bg-ink-700 ${
            value <= min ? 'opacity-40' : ''
          }`}
        >
          <Text className="text-lg text-ink-700 dark:text-ink-200">−</Text>
        </Pressable>
        <Text className="text-lg font-semibold text-ink-900 dark:text-ink-50 w-8 text-center">
          {value}
        </Text>
        <Pressable
          onPress={() => onChange(Math.min(max, value + 1))}
          disabled={value >= max}
          accessibilityLabel={`Increase ${label}`}
          className={`w-9 h-9 rounded-pill items-center justify-center bg-ink-100 dark:bg-ink-700 ${
            value >= max ? 'opacity-40' : ''
          }`}
        >
          <Text className="text-lg text-ink-700 dark:text-ink-200">+</Text>
        </Pressable>
      </View>
    </View>
  );
}

function Toggle({
  label,
  value,
  onChange,
}: {
  label: string;
  value: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <Pressable
      onPress={() => onChange(!value)}
      accessibilityRole="switch"
      accessibilityState={{ checked: value }}
      className="flex-row items-center justify-between py-3"
    >
      <Text className="text-ink-700 dark:text-ink-200">{label}</Text>
      <View
        className={`w-12 h-7 rounded-pill p-0.5 ${
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
