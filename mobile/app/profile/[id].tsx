/**
 * Another user's full profile, plus the safety actions.
 *
 * Report and block live here because this is the screen a user reaches when
 * something feels wrong — from a card, or from the chat header. Blocking
 * navigates away immediately, since the profile is no longer viewable.
 */

import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Modal,
  Pressable,
  ScrollView,
  Text,
  TextInput,
  View,
} from 'react-native';
import { Image } from 'expo-image';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ApiError, matches as matchesApi, profiles, safety } from '@/lib/api';
import {
  Banner,
  Button,
  Card,
  Chip,
  ErrorState,
  Loading,
} from '@/components/ui';
import type { PublicProfile, ReportReason } from '@/lib/types';

const REASONS: { value: ReportReason; label: string }[] = [
  { value: 'harassment', label: 'Harassment or abuse' },
  { value: 'inappropriate_content', label: 'Inappropriate content' },
  { value: 'fake_profile', label: 'Fake profile' },
  { value: 'spam', label: 'Spam' },
  { value: 'scam', label: 'Scam or fraud' },
  { value: 'underage', label: 'They appear to be under 18' },
  { value: 'other', label: 'Something else' },
];

export default function PublicProfileScreen() {
  const { id, matchId } = useLocalSearchParams<{
    id: string;
    matchId?: string;
  }>();
  const router = useRouter();
  const insets = useSafeAreaInsets();

  const [profile, setProfile] = useState<PublicProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [reportOpen, setReportOpen] = useState(false);
  const [reason, setReason] = useState<ReportReason | null>(null);
  const [details, setDetails] = useState('');

  const load = useCallback(async () => {
    try {
      setProfile(await profiles.view(id));
      setError(null);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : 'This profile is not available.',
      );
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  function confirmBlock() {
    Alert.alert(
      `Block ${profile?.display_name ?? 'this person'}?`,
      'They will not be able to see your profile or message you, and any match between you will be removed.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Block',
          style: 'destructive',
          onPress: async () => {
            setBusy(true);
            try {
              await safety.block(id);
              // The profile is unviewable now, so leave rather than showing
              // a stale screen.
              router.replace('/(tabs)/discover');
            } catch (caught) {
              setNotice(
                caught instanceof ApiError
                  ? caught.message
                  : 'Could not block this person.',
              );
            } finally {
              setBusy(false);
            }
          },
        },
      ],
    );
  }

  function confirmUnmatch() {
    if (!matchId) return;
    Alert.alert(
      'Unmatch?',
      'This removes the match and your conversation. It cannot be undone.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Unmatch',
          style: 'destructive',
          onPress: async () => {
            setBusy(true);
            try {
              await matchesApi.unmatch(matchId);
              router.replace('/(tabs)/matches');
            } catch (caught) {
              setNotice(
                caught instanceof ApiError
                  ? caught.message
                  : 'Could not unmatch.',
              );
            } finally {
              setBusy(false);
            }
          },
        },
      ],
    );
  }

  async function submitReport() {
    if (!reason) return;
    setBusy(true);
    try {
      await safety.report({
        user_id: id,
        reason,
        details: details.trim() || undefined,
      });
      setReportOpen(false);
      setReason(null);
      setDetails('');
      setNotice(
        'Thank you. Our moderators will review this report. Consider blocking them too.',
      );
    } catch (caught) {
      setNotice(
        caught instanceof ApiError
          ? caught.message
          : 'Could not submit that report.',
      );
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <Loading />;
  if (error || !profile) {
    return (
      <ErrorState
        message={error ?? 'Profile unavailable.'}
        onRetry={() => void load()}
      />
    );
  }

  return (
    <>
      <ScrollView
        className="flex-1 bg-ink-50 dark:bg-ink-900"
        contentContainerClassName="pb-12"
      >
        {/* Photo gallery */}
        <View className="relative">
          {profile.photos[0] ? (
            <Image
              source={{ uri: profile.photos[0].url }}
              style={{ width: '100%', aspectRatio: 3 / 4 }}
              contentFit="cover"
              cachePolicy="memory"
            />
          ) : (
            <View
              className="w-full items-center justify-center bg-ink-200 dark:bg-ink-800"
              style={{ aspectRatio: 3 / 4 }}
            >
              <Text className="text-6xl">🌸</Text>
            </View>
          )}

          <Pressable
            onPress={() => router.back()}
            accessibilityLabel="Back"
            className="absolute left-4 w-10 h-10 rounded-pill bg-black/45 items-center justify-center"
            style={{ top: insets.top + 8 }}
          >
            <Text className="text-white text-2xl">‹</Text>
          </Pressable>
        </View>

        <View className="px-6 pt-5">
          {notice ? <Banner message={notice} tone="info" /> : null}

          <View className="flex-row items-end gap-2 mb-1">
            <Text className="font-display text-3xl text-ink-900 dark:text-ink-50">
              {profile.display_name}
            </Text>
            <Text className="text-2xl text-ink-500 mb-0.5">{profile.age}</Text>
          </View>

          <View className="flex-row flex-wrap items-center gap-x-2 mb-5">
            {profile.pronouns ? (
              <Text className="text-sm text-ink-400">{profile.pronouns}</Text>
            ) : null}
            {profile.distance_km !== null ? (
              <Text className="text-sm text-ink-400">
                · {profile.distance_km} km away
              </Text>
            ) : null}
            {profile.city ? (
              <Text className="text-sm text-ink-400">· {profile.city}</Text>
            ) : null}
          </View>

          {profile.bio ? (
            <Card className="mb-4">
              <Text className="text-ink-700 dark:text-ink-200 leading-7">
                {profile.bio}
              </Text>
            </Card>
          ) : null}

          {(profile.job_title || profile.education || profile.height_cm) ? (
            <Card className="mb-4 gap-2">
              {profile.job_title ? (
                <Text className="text-ink-600 dark:text-ink-300">
                  💼 {profile.job_title}
                </Text>
              ) : null}
              {profile.education ? (
                <Text className="text-ink-600 dark:text-ink-300">
                  🎓 {profile.education}
                </Text>
              ) : null}
              {profile.height_cm ? (
                <Text className="text-ink-600 dark:text-ink-300">
                  📏 {profile.height_cm} cm
                </Text>
              ) : null}
            </Card>
          ) : null}

          {profile.interests.length > 0 ? (
            <Card className="mb-4">
              <Text className="font-semibold text-ink-800 dark:text-ink-100 mb-3">
                Interests
              </Text>
              <View className="flex-row flex-wrap gap-2">
                {profile.interests.map((interest) => (
                  <Chip key={interest.slug} label={interest.label} />
                ))}
              </View>
            </Card>
          ) : null}

          {/* Remaining photos */}
          {profile.photos.slice(1).map((photo) => (
            <Image
              key={photo.id}
              source={{ uri: photo.url }}
              style={{
                width: '100%',
                aspectRatio: 3 / 4,
                borderRadius: 24,
                marginBottom: 16,
              }}
              contentFit="cover"
              cachePolicy="memory"
            />
          ))}

          {/* Safety actions */}
          <View className="gap-3 mt-2">
            {matchId ? (
              <Button
                label="Unmatch"
                variant="secondary"
                onPress={confirmUnmatch}
                disabled={busy}
              />
            ) : null}
            <Button
              label="Report this profile"
              variant="secondary"
              onPress={() => setReportOpen(true)}
              disabled={busy}
            />
            <Button
              label="Block"
              variant="danger"
              onPress={confirmBlock}
              disabled={busy}
            />
          </View>
        </View>
      </ScrollView>

      {/* Report sheet */}
      <Modal
        visible={reportOpen}
        animationType="slide"
        transparent
        onRequestClose={() => setReportOpen(false)}
      >
        <View className="flex-1 justify-end bg-black/50">
          <View
            className="bg-ink-50 dark:bg-ink-900 rounded-t-card px-6 pt-6"
            style={{ paddingBottom: insets.bottom + 20 }}
          >
            <Text className="font-display text-2xl text-ink-900 dark:text-ink-50 mb-1">
              Report {profile.display_name}
            </Text>
            <Text className="text-sm text-ink-400 mb-5">
              Reports are confidential. They are reviewed by a moderator and
              the person is never told who reported them.
            </Text>

            <View className="gap-2 mb-4">
              {REASONS.map((option) => (
                <Pressable
                  key={option.value}
                  onPress={() => setReason(option.value)}
                  className={`flex-row items-center gap-3 rounded-2xl px-4 py-3 border ${
                    reason === option.value
                      ? 'border-rose-500 bg-rose-50 dark:bg-rose-900/20'
                      : 'border-ink-200 dark:border-ink-700'
                  }`}
                >
                  <View
                    className={`w-5 h-5 rounded-pill border-2 items-center justify-center ${
                      reason === option.value
                        ? 'border-rose-500'
                        : 'border-ink-300'
                    }`}
                  >
                    {reason === option.value ? (
                      <View className="w-2.5 h-2.5 rounded-pill bg-rose-500" />
                    ) : null}
                  </View>
                  <Text className="text-ink-800 dark:text-ink-100">
                    {option.label}
                  </Text>
                </Pressable>
              ))}
            </View>

            <TextInput
              value={details}
              onChangeText={setDetails}
              placeholder="Anything else we should know? (optional)"
              placeholderTextColor="#9B8C94"
              multiline
              maxLength={2000}
              className="rounded-2xl px-4 py-3 bg-white dark:bg-ink-800 text-ink-900 dark:text-ink-50 border border-ink-200 dark:border-ink-700 mb-4"
              style={{ minHeight: 90, textAlignVertical: 'top' }}
            />

            <Button
              label="Submit report"
              onPress={() => void submitReport()}
              disabled={!reason || busy}
              loading={busy}
            />
            <Pressable onPress={() => setReportOpen(false)} className="py-3 mt-1">
              <Text className="text-center text-ink-500">Cancel</Text>
            </Pressable>
          </View>
        </View>
      </Modal>
    </>
  );
}
