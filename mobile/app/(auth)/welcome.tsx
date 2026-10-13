/**
 * Welcome screen — the first thing a new user sees.
 *
 * Carries the 18+ statement and links to the legal documents up front, rather
 * than burying consent in the registration form alone.
 */

import { Text, View } from 'react-native';
import { Link, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Button } from '@/components/ui';
import { DemoNotice } from '@/components/demo-notice';
import { APP_NAME, MINIMUM_AGE } from '@/lib/config';

export default function Welcome() {
  const router = useRouter();
  const insets = useSafeAreaInsets();

  return (
    <View className="flex-1 bg-ink-900">
      {/* A layered wash standing in for a gradient, which keeps the screen
          dependency-free while still feeling designed. */}
      <View className="absolute inset-0 bg-rose-900 opacity-40" />
      <View className="absolute -top-24 -right-16 w-80 h-80 rounded-full bg-rose-500 opacity-25" />
      <View className="absolute top-40 -left-24 w-72 h-72 rounded-full bg-plum-500 opacity-25" />

      <View
        className="flex-1 justify-between px-7"
        style={{ paddingTop: insets.top + 56, paddingBottom: insets.bottom + 28 }}
      >
        <View>
          <Text className="text-6xl mb-5">💐</Text>
          <Text className="font-display text-5xl text-white leading-[56px] mb-4">
            Something{'\n'}real starts{'\n'}here.
          </Text>
          <Text className="text-lg text-ink-200 leading-7 max-w-[300px]">
            {APP_NAME} is for grown-ups looking for a genuine connection — not
            another endless feed.
          </Text>
        </View>

        <View className="gap-3">
          <DemoNotice />
          <Button
            label="Create an account"
            onPress={() => router.push('/(auth)/register')}
          />
          <Button
            label="I already have an account"
            variant="secondary"
            onPress={() => router.push('/(auth)/login')}
          />

          <Text className="text-xs text-ink-400 text-center leading-5 mt-3">
            You must be {MINIMUM_AGE} or older to use {APP_NAME}. By continuing
            you agree to our{' '}
            <Link href="/(auth)/terms" className="text-rose-300 underline">
              Terms
            </Link>{' '}
            and{' '}
            <Link href="/(auth)/privacy" className="text-rose-300 underline">
              Privacy Policy
            </Link>
            .
          </Text>
        </View>
      </View>
    </View>
  );
}
