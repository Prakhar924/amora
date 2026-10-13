/**
 * Profile setup, also used as the edit-profile screen.
 *
 * Serves both because the fields are identical; the only difference is the
 * copy and where "save" leads. Sharing it avoids two forms drifting apart.
 */

import { useCallback, useEffect, useState } from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import * as ImagePicker from 'expo-image-picker';
import * as Location from 'expo-location';
import { Image } from 'expo-image';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ApiError, profiles } from '@/lib/api';
import { useAuth } from '@/lib/auth-context';
import { MAX_PHOTOS } from '@/lib/config';
import {
  Banner,
  Button,
  Chip,
  Field,
  Loading,
  Subtitle,
  Title,
} from '@/components/ui';
import type { Interest, MyProfile } from '@/lib/types';

export default function ProfileSetup() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { needsOnboarding, refresh: refreshAuth } = useAuth();

  const [profile, setProfile] = useState<MyProfile | null>(null);
  const [vocabulary, setVocabulary] = useState<Interest[]>([]);
  const [loading, setLoading] = useState(true);

  const [bio, setBio] = useState('');
  const [jobTitle, setJobTitle] = useState('');
  const [education, setEducation] = useState('');
  const [height, setHeight] = useState('');
  const [pronouns, setPronouns] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [locationLabel, setLocationLabel] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [mine, interests] = await Promise.all([
        profiles.mine(),
        profiles.interests(),
      ]);
      setProfile(mine);
      setVocabulary(interests);
      setBio(mine.bio ?? '');
      setJobTitle(mine.job_title ?? '');
      setEducation(mine.education ?? '');
      setHeight(mine.height_cm ? String(mine.height_cm) : '');
      setPronouns(mine.pronouns ?? '');
      setSelected(new Set(mine.interests.map((i) => i.slug)));
      setLocationLabel(mine.city ?? null);
    } catch (caught) {
      setNotice(
        caught instanceof ApiError ? caught.message : 'Could not load profile.',
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function toggleInterest(slug: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(slug)) {
        next.delete(slug);
      } else if (next.size < 20) {
        next.add(slug);
      }
      return next;
    });
  }

  async function addPhoto() {
    const permission = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!permission.granted) {
      setNotice('Photo access is needed to add a picture.');
      return;
    }
    const picked = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ['images'],
      allowsEditing: true,
      aspect: [3, 4],
      quality: 0.85,
    });
    if (picked.canceled || !picked.assets[0]) return;

    setBusy(true);
    try {
      const updated = await profiles.uploadPhoto(
        picked.assets[0].uri,
        picked.assets[0].mimeType ?? 'image/jpeg',
      );
      setProfile(updated);
      setNotice('Photo uploaded — a moderator will review it shortly.');
    } catch (caught) {
      setNotice(
        caught instanceof ApiError ? caught.message : 'Upload failed.',
      );
    } finally {
      setBusy(false);
    }
  }

  async function useMyLocation() {
    setBusy(true);
    setNotice(null);
    try {
      const permission = await Location.requestForegroundPermissionsAsync();
      if (!permission.granted) {
        setNotice(
          'Without location we cannot show you people nearby. You can still browse by age.',
        );
        return;
      }
      const position = await Location.getCurrentPositionAsync({
        accuracy: Location.Accuracy.Balanced,
      });

      // Reverse-geocode to a city name so the profile shows something
      // human-readable; only the city is ever displayed to others.
      let city: string | undefined;
      let country: string | undefined;
      try {
        const [place] = await Location.reverseGeocodeAsync({
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
        });
        city = place?.city ?? place?.subregion ?? undefined;
        country = place?.isoCountryCode ?? undefined;
      } catch {
        // Reverse geocoding is a nicety; the coordinates are what matter.
      }

      const response = await profiles.setLocation({
        latitude: position.coords.latitude,
        longitude: position.coords.longitude,
        city,
        country,
      });
      setLocationLabel(response.city ?? 'Location saved');
      setNotice(
        'Location saved. Others only ever see your city and an approximate distance.',
      );
    } catch (caught) {
      setNotice(
        caught instanceof ApiError
          ? caught.message
          : 'Could not read your location.',
      );
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    setSaving(true);
    setNotice(null);
    try {
      await profiles.update({
        bio: bio.trim() || null,
        job_title: jobTitle.trim() || null,
        education: education.trim() || null,
        pronouns: pronouns.trim() || null,
        height_cm: height.trim() ? Number(height.trim()) : null,
        interests: Array.from(selected),
      });
      const me = await refreshAuth();

      if (me?.profile_complete) {
        router.replace('/(tabs)/discover');
      } else {
        setNotice(
          'Saved. Add a photo and wait for approval to appear in discovery.',
        );
      }
    } catch (caught) {
      setNotice(
        caught instanceof ApiError ? caught.message : 'Could not save changes.',
      );
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <Loading />;

  const byCategory = vocabulary.reduce<Record<string, Interest[]>>(
    (groups, interest) => {
      const bucket = groups[interest.category] ?? [];
      bucket.push(interest);
      groups[interest.category] = bucket;
      return groups;
    },
    {},
  );

  return (
    <ScrollView
      className="flex-1 bg-ink-50 dark:bg-ink-900"
      contentContainerClassName="px-7 pb-16"
      contentContainerStyle={{ paddingTop: insets.top + 24 }}
      keyboardShouldPersistTaps="handled"
    >
      <Title>{needsOnboarding ? 'Set up your profile' : 'Edit profile'}</Title>
      <Subtitle>
        {needsOnboarding
          ? 'A photo and a sentence or two is all it takes to get started.'
          : 'Update anything you like. Changes are live straight away.'}
      </Subtitle>

      {notice ? <Banner message={notice} tone="info" /> : null}

      {/* --- Photos --- */}
      <Text className="font-semibold text-base text-ink-800 dark:text-ink-100 mb-1">
        Photos
      </Text>
      <Text className="text-xs text-ink-400 mb-3">
        At least one approved photo is required to appear in discovery.
      </Text>
      <View className="flex-row flex-wrap gap-3 mb-6">
        {(profile?.photos ?? []).map((photo) => (
          <View
            key={photo.id}
            className="w-[30%] aspect-[3/4] rounded-2xl overflow-hidden bg-ink-200 dark:bg-ink-800"
          >
            <Image
              source={{ uri: photo.url }}
              style={{ width: '100%', height: '100%' }}
              contentFit="cover"
              cachePolicy="memory"
            />
            {photo.moderation_status !== 'approved' ? (
              <View className="absolute inset-0 bg-black/55 items-center justify-center">
                <Text className="text-white text-[10px] font-semibold">
                  {photo.moderation_status === 'pending'
                    ? 'In review'
                    : 'Rejected'}
                </Text>
              </View>
            ) : null}
          </View>
        ))}
        {(profile?.photos.length ?? 0) < MAX_PHOTOS ? (
          <Pressable
            onPress={() => void addPhoto()}
            disabled={busy}
            className="w-[30%] aspect-[3/4] rounded-2xl border-2 border-dashed border-ink-300 dark:border-ink-600 items-center justify-center"
          >
            <Text className="text-3xl text-ink-400">+</Text>
          </Pressable>
        ) : null}
      </View>

      {/* --- Location --- */}
      <Text className="font-semibold text-base text-ink-800 dark:text-ink-100 mb-1">
        Location
      </Text>
      <Text className="text-xs text-ink-400 mb-3">
        Used only to work out distance. Your coordinates are never shown to
        anyone.
      </Text>
      <Button
        label={locationLabel ? `Update location (${locationLabel})` : 'Use my location'}
        variant="secondary"
        onPress={() => void useMyLocation()}
        loading={busy}
      />

      <View className="h-6" />

      {/* --- Text fields --- */}
      <Field
        label="Bio"
        value={bio}
        onChangeText={setBio}
        placeholder="What are you into? What would make a good first date?"
        multiline
        numberOfLines={4}
        maxLength={1000}
        style={{ minHeight: 110, textAlignVertical: 'top' }}
        hint={`${bio.length}/1000`}
      />
      <Field
        label="Work"
        value={jobTitle}
        onChangeText={setJobTitle}
        placeholder="What do you do?"
        maxLength={100}
      />
      <Field
        label="Education"
        value={education}
        onChangeText={setEducation}
        placeholder="Where did you study?"
        maxLength={100}
      />
      <Field
        label="Height (cm)"
        value={height}
        onChangeText={setHeight}
        placeholder="e.g. 173"
        keyboardType="number-pad"
        maxLength={3}
      />
      <Field
        label="Pronouns"
        value={pronouns}
        onChangeText={setPronouns}
        placeholder="e.g. she/her"
        maxLength={40}
      />

      {/* --- Interests --- */}
      <Text className="font-semibold text-base text-ink-800 dark:text-ink-100 mb-1">
        Interests
      </Text>
      <Text className="text-xs text-ink-400 mb-3">
        Pick up to 20. Shared interests give people something to open with.
        ({selected.size} selected)
      </Text>

      {Object.entries(byCategory).map(([category, group]) => (
        <View key={category} className="mb-4">
          <Text className="text-xs uppercase tracking-wide text-ink-400 mb-2">
            {category}
          </Text>
          <View className="flex-row flex-wrap gap-2">
            {group.map((interest) => (
              <Chip
                key={interest.slug}
                label={interest.label}
                selected={selected.has(interest.slug)}
                onPress={() => toggleInterest(interest.slug)}
              />
            ))}
          </View>
        </View>
      ))}

      <View className="mt-4">
        <Button
          label={needsOnboarding ? 'Save and start swiping' : 'Save changes'}
          onPress={() => void save()}
          loading={saving}
        />
      </View>

      {!needsOnboarding ? (
        <Pressable onPress={() => router.back()} className="mt-4 py-3">
          <Text className="text-center text-ink-500">Cancel</Text>
        </Pressable>
      ) : null}
    </ScrollView>
  );
}
