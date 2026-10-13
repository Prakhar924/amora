/**
 * Email confirmation.
 *
 * Serves two cases: arriving from the emailed link with a token (verify it
 * immediately), and reaching this screen while signed in but unverified
 * (offer to resend).
 */

import { useCallback, useEffect, useState } from 'react';
import { Text, View } from 'react-native';
import { useLocalSearchParams } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ApiError, auth } from '@/lib/api';
import { useAuth } from '@/lib/auth-context';
import { Banner, Button, Subtitle, Title } from '@/components/ui';

export default function VerifyEmail() {
  const { token } = useLocalSearchParams<{ token?: string }>();
  const insets = useSafeAreaInsets();
  const { user, refresh, signOut } = useAuth();

  const [status, setStatus] = useState<'idle' | 'verifying' | 'done' | 'failed'>(
    token ? 'verifying' : 'idle',
  );
  const [message, setMessage] = useState<string | null>(null);
  const [resending, setResending] = useState(false);

  const verify = useCallback(
    async (value: string) => {
      setStatus('verifying');
      try {
        await auth.verifyEmail(value);
        setStatus('done');
        // Pick up the now-verified status so the routing gate moves on.
        await refresh();
      } catch (caught) {
        setStatus('failed');
        setMessage(
          caught instanceof ApiError
            ? caught.message
            : 'That link could not be used.',
        );
      }
    },
    [refresh],
  );

  useEffect(() => {
    if (token) void verify(token);
  }, [token, verify]);

  async function resend() {
    setResending(true);
    setMessage(null);
    try {
      const response = await auth.resendVerification();
      setMessage(response.message);
    } catch (caught) {
      setMessage(
        caught instanceof ApiError
          ? caught.message
          : 'Could not send another email right now.',
      );
    } finally {
      setResending(false);
    }
  }

  return (
    <View
      className="flex-1 bg-ink-50 dark:bg-ink-900 px-7 justify-center"
      style={{ paddingTop: insets.top }}
    >
      <Text className="text-5xl mb-4 text-center">
        {status === 'done' ? '🎉' : status === 'failed' ? '⚠️' : '✉️'}
      </Text>

      {status === 'done' ? (
        <>
          <Title>You are all set</Title>
          <Subtitle>
            Your email is confirmed. Next, add a photo and a few details so
            people can get to know you.
          </Subtitle>
        </>
      ) : (
        <>
          <Title>Confirm your email</Title>
          <Subtitle>
            {status === 'verifying'
              ? 'Checking your link…'
              : `We sent a confirmation link to ${
                  user?.email ?? 'your inbox'
                }. Open it to activate your account.`}
          </Subtitle>

          {message ? (
            <Banner
              message={message}
              tone={status === 'failed' ? 'error' : 'info'}
            />
          ) : null}

          {status !== 'verifying' ? (
            <View className="gap-3">
              <Button
                label="Resend the email"
                onPress={resend}
                loading={resending}
                variant="secondary"
              />
              <Button
                label="I have confirmed it"
                onPress={() => void refresh()}
              />
              <Button
                label="Sign out"
                variant="ghost"
                onPress={() => void signOut()}
              />
            </View>
          ) : null}
        </>
      )}
    </View>
  );
}
