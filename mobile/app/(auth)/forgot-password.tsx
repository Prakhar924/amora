/**
 * Password reset request.
 *
 * The server answers identically whether or not the address has an account,
 * so this screen shows the same confirmation either way. Saying "no such
 * user" here would turn the form into an account-existence oracle.
 */

import { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ApiError, auth } from '@/lib/api';
import { Banner, Button, Field, Subtitle, Title } from '@/components/ui';

export default function ForgotPassword() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [email, setEmail] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setSubmitting(true);
    setError(null);
    try {
      await auth.forgotPassword(email.trim().toLowerCase());
      setSent(true);
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : 'Something went wrong.',
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <View
      className="flex-1 bg-ink-50 dark:bg-ink-900 px-7"
      style={{ paddingTop: insets.top + 48 }}
    >
      {sent ? (
        <>
          <Text className="text-5xl mb-4">📮</Text>
          <Title>Check your email</Title>
          <Subtitle>
            If an account exists for that address, a reset link is on its way.
            The link is valid for one hour.
          </Subtitle>
          <Button
            label="Back to sign in"
            onPress={() => router.replace('/(auth)/login')}
          />
        </>
      ) : (
        <>
          <Title>Reset your password</Title>
          <Subtitle>
            Enter your email address and we will send you a link to choose a new
            password.
          </Subtitle>

          {error ? <Banner message={error} /> : null}

          <Field
            label="Email"
            value={email}
            onChangeText={setEmail}
            placeholder="you@example.com"
            keyboardType="email-address"
            autoCapitalize="none"
            autoCorrect={false}
          />
          <Button
            label="Send reset link"
            onPress={submit}
            loading={submitting}
            disabled={!email.includes('@') || submitting}
          />
          <Pressable onPress={() => router.back()} className="mt-5 py-2">
            <Text className="text-center text-ink-500">Back</Text>
          </Pressable>
        </>
      )}
    </View>
  );
}
