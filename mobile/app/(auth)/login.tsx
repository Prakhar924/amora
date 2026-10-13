/**
 * Sign in.
 *
 * On success the auth context refreshes `/auth/me`, and the routing gate in
 * the root layout decides where the user lands — verification, onboarding, or
 * the main tabs. This screen deliberately makes no routing decision itself.
 */

import { useState } from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ApiError } from '@/lib/api';
import { useAuth } from '@/lib/auth-context';
import { APP_NAME } from '@/lib/config';
import { Banner, Button, Field, Subtitle, Title } from '@/components/ui';
import { WakingNotice } from '@/components/waking-notice';

export default function Login() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { signIn } = useAuth();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = email.includes('@') && password.length > 0 && !submitting;

  async function submit() {
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      await signIn(email.trim().toLowerCase(), password);
      // No navigation here: the routing gate reacts to the new auth state.
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : 'Could not sign you in. Please try again.',
      );
      setSubmitting(false);
    }
  }

  return (
    <ScrollView
      className="flex-1 bg-ink-50 dark:bg-ink-900"
      contentContainerClassName="px-7 pb-12"
      contentContainerStyle={{ paddingTop: insets.top + 48 }}
      keyboardShouldPersistTaps="handled"
    >
      <Text className="text-5xl mb-4">💐</Text>
      <Title>Welcome back</Title>
      <Subtitle>Sign in to pick up where you left off.</Subtitle>

      {error ? <Banner message={error} /> : null}
      <WakingNotice />

      <Field
        label="Email"
        value={email}
        onChangeText={setEmail}
        placeholder="you@example.com"
        keyboardType="email-address"
        autoCapitalize="none"
        autoComplete="email"
        autoCorrect={false}
      />
      <Field
        label="Password"
        value={password}
        onChangeText={setPassword}
        placeholder="Your password"
        secureTextEntry
        autoComplete="current-password"
        onSubmitEditing={submit}
        returnKeyType="go"
      />

      <Button
        label="Sign in"
        onPress={submit}
        loading={submitting}
        disabled={!canSubmit}
      />

      <Pressable
        onPress={() => router.push('/(auth)/forgot-password')}
        className="mt-5 py-2"
      >
        <Text className="text-center text-rose-500 font-medium">
          Forgot your password?
        </Text>
      </Pressable>

      <Pressable onPress={() => router.push('/(auth)/register')} className="py-2">
        <Text className="text-center text-ink-500 dark:text-ink-400">
          New to {APP_NAME}?{' '}
          <Text className="text-rose-500 font-semibold">Create an account</Text>
        </Text>
      </Pressable>
    </ScrollView>
  );
}
