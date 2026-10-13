/**
 * Change password.
 *
 * The backend bumps `token_version` and revokes every session on success, so
 * this screen signs the user out afterwards rather than leaving them holding
 * a token the API will now refuse.
 */

import { useState } from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ApiError, auth } from '@/lib/api';
import { useAuth } from '@/lib/auth-context';
import { Banner, Button, Field, Subtitle, Title } from '@/components/ui';

export default function ChangePassword() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { signOut } = useAuth();

  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const mismatch = confirm.length > 0 && confirm !== next;
  const tooShort = next.length > 0 && next.length < 10;
  const canSubmit =
    current.length > 0 && next.length >= 10 && !mismatch && !busy;

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await auth.changePassword(current, next);
      setDone(true);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : 'Could not change your password.',
      );
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <View
        className="flex-1 bg-ink-50 dark:bg-ink-900 px-7 justify-center"
        style={{ paddingTop: insets.top }}
      >
        <Text className="text-5xl mb-4">🔐</Text>
        <Title>Password changed</Title>
        <Subtitle>
          For your security, every device has been signed out. Please sign in
          again with your new password.
        </Subtitle>
        <Button label="Sign in" onPress={() => void signOut()} />
      </View>
    );
  }

  return (
    <ScrollView
      className="flex-1 bg-ink-50 dark:bg-ink-900"
      contentContainerClassName="px-7 pb-16"
      contentContainerStyle={{ paddingTop: insets.top + 16 }}
      keyboardShouldPersistTaps="handled"
    >
      <View className="flex-row items-center gap-2 mb-6 -ml-2">
        <Pressable
          onPress={() => router.back()}
          accessibilityLabel="Back"
          className="w-9 h-9 items-center justify-center"
        >
          <Text className="text-2xl text-ink-600 dark:text-ink-300">‹</Text>
        </Pressable>
      </View>

      <Title>Change password</Title>
      <Subtitle>
        You will be signed out everywhere once your password changes.
      </Subtitle>

      {error ? <Banner message={error} /> : null}

      <Field
        label="Current password"
        value={current}
        onChangeText={setCurrent}
        secureTextEntry
        autoComplete="current-password"
        placeholder="Your current password"
      />
      <Field
        label="New password"
        value={next}
        onChangeText={setNext}
        secureTextEntry
        autoComplete="new-password"
        placeholder="At least 10 characters"
        error={tooShort ? 'At least 10 characters' : null}
        hint="Use at least 10 characters, with a letter and a number."
      />
      <Field
        label="Confirm new password"
        value={confirm}
        onChangeText={setConfirm}
        secureTextEntry
        placeholder="Repeat your new password"
        error={mismatch ? 'Passwords do not match' : null}
      />

      <Button
        label="Change password"
        onPress={() => void submit()}
        loading={busy}
        disabled={!canSubmit}
      />
    </ScrollView>
  );
}
