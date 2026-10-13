/**
 * Choose a new password from an emailed link.
 *
 * The token arrives as a deep-link query parameter
 * (`amora://reset-password?token=...`, or the web equivalent).
 */

import { useState } from 'react';
import { Text, View } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ApiError, auth } from '@/lib/api';
import { Banner, Button, Field, Subtitle, Title } from '@/components/ui';

export default function ResetPassword() {
  const { token } = useLocalSearchParams<{ token?: string }>();
  const router = useRouter();
  const insets = useSafeAreaInsets();

  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const mismatch = confirm.length > 0 && confirm !== password;
  const canSubmit =
    Boolean(token) && password.length >= 10 && !mismatch && !submitting;

  async function submit() {
    if (!canSubmit || !token) return;
    setSubmitting(true);
    setError(null);
    try {
      await auth.resetPassword(token, password);
      setDone(true);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : 'Could not reset your password.',
      );
    } finally {
      setSubmitting(false);
    }
  }

  if (!token) {
    return (
      <View
        className="flex-1 bg-ink-50 dark:bg-ink-900 px-7 justify-center"
        style={{ paddingTop: insets.top }}
      >
        <Title>Link not valid</Title>
        <Subtitle>
          This reset link is missing or incomplete. Request a new one from the
          sign-in screen.
        </Subtitle>
        <Button
          label="Back to sign in"
          onPress={() => router.replace('/(auth)/login')}
        />
      </View>
    );
  }

  return (
    <View
      className="flex-1 bg-ink-50 dark:bg-ink-900 px-7"
      style={{ paddingTop: insets.top + 48 }}
    >
      {done ? (
        <>
          <Text className="text-5xl mb-4">✅</Text>
          <Title>Password updated</Title>
          <Subtitle>
            Your password has been changed, and every other device was signed
            out.
          </Subtitle>
          <Button
            label="Sign in"
            onPress={() => router.replace('/(auth)/login')}
          />
        </>
      ) : (
        <>
          <Title>Choose a new password</Title>
          <Subtitle>Pick something you have not used before.</Subtitle>
          {error ? <Banner message={error} /> : null}
          <Field
            label="New password"
            value={password}
            onChangeText={setPassword}
            secureTextEntry
            placeholder="At least 10 characters"
            hint="Use at least 10 characters, with a letter and a number."
          />
          <Field
            label="Confirm new password"
            value={confirm}
            onChangeText={setConfirm}
            secureTextEntry
            placeholder="Repeat it"
            error={mismatch ? 'Passwords do not match' : null}
          />
          <Button
            label="Update password"
            onPress={submit}
            loading={submitting}
            disabled={!canSubmit}
          />
        </>
      )}
    </View>
  );
}
