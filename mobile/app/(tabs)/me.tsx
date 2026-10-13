/**
 * My profile — view and edit, including photo management.
 *
 * Photos show their moderation state here (and only here): the owner needs to
 * know a photo is pending or was rejected, while other users must never see
 * that information.
 */

import { useCallback, useState } from 'react';
import {
  Alert,
  Pressable,
  RefreshControl,
  ScrollView,
  Text,
  View,
} from 'react-native';
import { Image } from 'expo-image';
import * as ImagePicker from 'expo-image-picker';
import { useFocusEffect, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ApiError, profiles } from '@/lib/api';
import { useAuth } from '@/lib/auth-context';
import { MAX_PHOTOS } from '@/lib/config';
import {
  Banner,
  Button,
  Card,
  Chip,
  ErrorState,
  Loading,
} from '@/components/ui';
import type { MyProfile, Photo } from '@/lib/types';

export default function Me() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { user, refresh: refreshAuth } = useAuth();

  const [profile, setProfile] = useState<MyProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setProfile(await profiles.mine());
      setError(null);
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : 'Could not load profile.',
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

  async function addPhoto() {
    const permission = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!permission.granted) {
      setNotice(
        'Photo access is needed to add a picture. You can enable it in your device settings.',
      );
      return;
    }

    const picked = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ['images'],
      allowsEditing: true,
      // Portrait, matching the discovery card.
      aspect: [3, 4],
      quality: 0.85,
    });
    if (picked.canceled || !picked.assets[0]) return;

    const asset = picked.assets[0];
    setBusy(true);
    setNotice(null);
    try {
      const updated = await profiles.uploadPhoto(
        asset.uri,
        asset.mimeType ?? 'image/jpeg',
      );
      setProfile(updated);
      setNotice(
        'Photo uploaded. It will appear on your profile once a moderator has approved it.',
      );
      // Approving a first photo can complete the profile, which changes routing.
      await refreshAuth();
    } catch (caught) {
      setNotice(
        caught instanceof ApiError
          ? caught.message
          : 'That photo could not be uploaded.',
      );
    } finally {
      setBusy(false);
    }
  }

  function confirmDeletePhoto(photo: Photo) {
    Alert.alert('Delete this photo?', 'This cannot be undone.', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete',
        style: 'destructive',
        onPress: async () => {
          setBusy(true);
          try {
            setProfile(await profiles.deletePhoto(photo.id));
            await refreshAuth();
          } catch (caught) {
            setNotice(
              caught instanceof ApiError
                ? caught.message
                : 'Could not delete that photo.',
            );
          } finally {
            setBusy(false);
          }
        },
      },
    ]);
  }

  if (loading) return <Loading label="Loading your profile…" />;
  if (error || !profile) {
    return (
      <ErrorState
        message={error ?? 'Profile unavailable.'}
        onRetry={() => void load()}
      />
    );
  }

  return (
    <ScrollView
      className="flex-1 bg-ink-50 dark:bg-ink-900"
      contentContainerClassName="pb-10"
      contentContainerStyle={{ paddingTop: insets.top + 8 }}
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
      <View className="flex-row items-center justify-between px-6 pb-4">
        <Text className="font-display text-3xl text-ink-900 dark:text-ink-50">
          {profile.display_name}, {profile.age}
        </Text>
        <Pressable
          onPress={() => router.push('/settings')}
          accessibilityLabel="Settings"
          className="w-10 h-10 items-center justify-center rounded-pill bg-white dark:bg-ink-800"
        >
          <Text className="text-lg">⚙️</Text>
        </Pressable>
      </View>

      <View className="px-6">
        {notice ? <Banner message={notice} tone="info" /> : null}

        {!profile.is_complete ? (
          <Banner
            message="Your profile is not visible yet. Add an approved photo to appear in discovery."
            tone="info"
          />
        ) : null}

        {/* --- Photos --- */}
        <Text className="font-semibold text-base text-ink-800 dark:text-ink-100 mb-3">
          Photos
        </Text>
        <View className="flex-row flex-wrap gap-3 mb-6">
          {profile.photos.map((photo) => (
            <Pressable
              key={photo.id}
              onLongPress={() => confirmDeletePhoto(photo)}
              className="w-[30%] aspect-[3/4] rounded-2xl overflow-hidden bg-ink-200 dark:bg-ink-800"
            >
              <Image
                source={{ uri: photo.url }}
                style={{ width: '100%', height: '100%' }}
                contentFit="cover"
                cachePolicy="memory"
              />
              {photo.moderation_status !== 'approved' ? (
                <View className="absolute inset-0 bg-black/55 items-center justify-center px-1">
                  <Text className="text-white text-[10px] font-semibold text-center">
                    {photo.moderation_status === 'pending'
                      ? 'In review'
                      : 'Not approved'}
                  </Text>
                </View>
              ) : null}
              {photo.position === 0 ? (
                <View className="absolute top-1.5 left-1.5 rounded-pill bg-rose-500 px-2 py-0.5">
                  <Text className="text-white text-[9px] font-bold">MAIN</Text>
                </View>
              ) : null}
            </Pressable>
          ))}

          {profile.photos.length < MAX_PHOTOS ? (
            <Pressable
              onPress={() => void addPhoto()}
              disabled={busy}
              className="w-[30%] aspect-[3/4] rounded-2xl border-2 border-dashed border-ink-300 dark:border-ink-600 items-center justify-center"
            >
              <Text className="text-3xl text-ink-400">+</Text>
              <Text className="text-[10px] text-ink-400 mt-1">Add photo</Text>
            </Pressable>
          ) : null}
        </View>
        <Text className="text-xs text-ink-400 mb-6 -mt-3">
          Long-press a photo to delete it. Photos are reviewed before they go
          live.
        </Text>

        {/* --- About --- */}
        <Card className="mb-4">
          <Text className="font-semibold text-base text-ink-800 dark:text-ink-100 mb-2">
            About me
          </Text>
          <Text className="text-ink-600 dark:text-ink-300 leading-6">
            {profile.bio ?? 'No bio yet. Tell people what you are into.'}
          </Text>

          <View className="mt-4 gap-1.5">
            {profile.job_title ? (
              <Detail icon="💼" value={profile.job_title} />
            ) : null}
            {profile.education ? (
              <Detail icon="🎓" value={profile.education} />
            ) : null}
            {profile.height_cm ? (
              <Detail icon="📏" value={`${profile.height_cm} cm`} />
            ) : null}
            {profile.city ? <Detail icon="📍" value={profile.city} /> : null}
            {profile.pronouns ? (
              <Detail icon="🏷️" value={profile.pronouns} />
            ) : null}
          </View>
        </Card>

        {/* --- Interests --- */}
        <Card className="mb-6">
          <Text className="font-semibold text-base text-ink-800 dark:text-ink-100 mb-3">
            Interests
          </Text>
          {profile.interests.length === 0 ? (
            <Text className="text-ink-400">
              None yet — add a few so people have something to ask about.
            </Text>
          ) : (
            <View className="flex-row flex-wrap gap-2">
              {profile.interests.map((interest) => (
                <Chip key={interest.slug} label={interest.label} />
              ))}
            </View>
          )}
        </Card>

        <View className="gap-3">
          <Button
            label="Edit profile"
            onPress={() => router.push('/onboarding/profile')}
          />
          <Button
            label="Discovery preferences"
            variant="secondary"
            onPress={() => router.push('/settings/preferences')}
          />
          {user?.is_admin ? (
            <Button
              label="Moderation dashboard"
              variant="secondary"
              onPress={() => router.push('/admin')}
            />
          ) : null}
        </View>
      </View>
    </ScrollView>
  );
}

function Detail({ icon, value }: { icon: string; value: string }) {
  return (
    <View className="flex-row items-center gap-2">
      <Text className="text-sm">{icon}</Text>
      <Text className="text-sm text-ink-600 dark:text-ink-300">{value}</Text>
    </View>
  );
}
