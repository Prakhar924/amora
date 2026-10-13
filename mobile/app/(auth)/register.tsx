/**
 * Registration.
 *
 * The 18+ rule is enforced by the backend, but it is also checked here so a
 * user is told immediately rather than after a round trip. The client check is
 * a courtesy; the server's is the one that counts.
 */

import { useMemo, useState } from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ApiError, auth } from '@/lib/api';
import { APP_NAME, MINIMUM_AGE } from '@/lib/config';
import { Banner, Button, Chip, Field, Subtitle, Title } from '@/components/ui';
import { WakingNotice } from '@/components/waking-notice';
import type { Gender } from '@/lib/types';

const GENDERS: { value: Gender; label: string }[] = [
  { value: 'woman', label: 'Woman' },
  { value: 'man', label: 'Man' },
  { value: 'nonbinary', label: 'Non-binary' },
  { value: 'other', label: 'Prefer to self-describe' },
];

/** Whole years completed, matching the server's calculation. */
function ageFrom(iso: string): number | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso.trim());
  if (!match) return null;
  const [, y, m, d] = match;
  const year = Number(y);
  const month = Number(m);
  const day = Number(d);
  if (month < 1 || month > 12 || day < 1 || day > 31) return null;

  const dob = new Date(Date.UTC(year, month - 1, day));
  if (
    dob.getUTCFullYear() !== year ||
    dob.getUTCMonth() !== month - 1 ||
    dob.getUTCDate() !== day
  ) {
    return null; // e.g. 31 February
  }

  const today = new Date();
  let age = today.getUTCFullYear() - year;
  const hadBirthday =
    today.getUTCMonth() + 1 > month ||
    (today.getUTCMonth() + 1 === month && today.getUTCDate() >= day);
  if (!hadBirthday) age -= 1;
  return age;
}

export default function Register() {
  const router = useRouter();
  const insets = useSafeAreaInsets();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [dob, setDob] = useState('');
  const [gender, setGender] = useState<Gender | null>(null);
  const [acceptTerms, setAcceptTerms] = useState(false);
  const [acceptPrivacy, setAcceptPrivacy] = useState(false);

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const age = useMemo(() => ageFrom(dob), [dob]);

  const dobError =
    dob.length > 0 && age === null
      ? 'Use the format YYYY-MM-DD'
      : age !== null && age < MINIMUM_AGE
        ? `You must be at least ${MINIMUM_AGE} to join`
        : null;

  const passwordError =
    password.length > 0 && password.length < 10
      ? 'At least 10 characters'
      : password.length > 0 && !/\d/.test(password)
        ? 'Include at least one number'
        : password.length > 0 && !/[a-zA-Z]/.test(password)
          ? 'Include at least one letter'
          : null;

  const canSubmit =
    email.includes('@') &&
    !passwordError &&
    password.length >= 10 &&
    displayName.trim().length >= 2 &&
    age !== null &&
    age >= MINIMUM_AGE &&
    gender !== null &&
    acceptTerms &&
    acceptPrivacy &&
    !submitting;

  async function submit() {
    if (!canSubmit || gender === null) return;
    setSubmitting(true);
    setError(null);
    try {
      await auth.register({
        email: email.trim().toLowerCase(),
        password,
        display_name: displayName.trim(),
        date_of_birth: dob.trim(),
        gender,
        accept_terms: acceptTerms,
        accept_privacy: acceptPrivacy,
      });
      setDone(true);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : 'Could not create your account. Please try again.',
      );
    } finally {
      setSubmitting(false);
    }
  }

  if (done) {
    return (
      <View
        className="flex-1 bg-ink-50 dark:bg-ink-900 px-7 justify-center"
        style={{ paddingTop: insets.top }}
      >
        <Text className="text-5xl mb-4 text-center">📬</Text>
        <Title>Check your inbox</Title>
        <Subtitle>
          We have sent a confirmation link to {email.trim().toLowerCase()}.
          Confirm your address and then sign in to finish your profile.
        </Subtitle>
        <Button label="Go to sign in" onPress={() => router.replace('/(auth)/login')} />
      </View>
    );
  }

  return (
    <ScrollView
      className="flex-1 bg-ink-50 dark:bg-ink-900"
      contentContainerClassName="px-7 pb-12"
      contentContainerStyle={{ paddingTop: insets.top + 24 }}
      keyboardShouldPersistTaps="handled"
    >
      <Title>Join {APP_NAME}</Title>
      <Subtitle>A few details and you are in. You can edit all of it later.</Subtitle>

      {error ? <Banner message={error} /> : null}
      <WakingNotice />

      <Field
        label="First name"
        value={displayName}
        onChangeText={setDisplayName}
        placeholder="What should people call you?"
        autoCapitalize="words"
        autoComplete="given-name"
        maxLength={60}
      />

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
        placeholder="At least 10 characters"
        secureTextEntry
        autoComplete="new-password"
        error={passwordError}
        hint="Use at least 10 characters, with a letter and a number."
      />

      <Field
        label="Date of birth"
        value={dob}
        onChangeText={setDob}
        placeholder="YYYY-MM-DD"
        keyboardType="numbers-and-punctuation"
        maxLength={10}
        error={dobError}
        hint={
          age !== null && age >= MINIMUM_AGE
            ? `You are ${age}. Your age is shown on your profile; your date of birth is not.`
            : `${APP_NAME} is strictly for people ${MINIMUM_AGE} and over.`
        }
      />

      <Text className="text-sm font-medium text-ink-600 dark:text-ink-300 mb-2">
        I am a
      </Text>
      <View className="flex-row flex-wrap gap-2 mb-6">
        {GENDERS.map((option) => (
          <Chip
            key={option.value}
            label={option.label}
            selected={gender === option.value}
            onPress={() => setGender(option.value)}
          />
        ))}
      </View>

      <Consent
        checked={acceptTerms}
        onToggle={() => setAcceptTerms((v) => !v)}
        label="I agree to the Terms of Service."
      />
      <Consent
        checked={acceptPrivacy}
        onToggle={() => setAcceptPrivacy((v) => !v)}
        label={`I have read the Privacy Policy and understand how ${APP_NAME} handles my data.`}
      />

      <View className="mt-6">
        <Button
          label="Create account"
          onPress={submit}
          loading={submitting}
          disabled={!canSubmit}
        />
      </View>

      <Pressable
        onPress={() => router.push('/(auth)/login')}
        className="mt-5 py-2"
      >
        <Text className="text-center text-ink-500 dark:text-ink-400">
          Already have an account?{' '}
          <Text className="text-rose-500 font-semibold">Sign in</Text>
        </Text>
      </Pressable>
    </ScrollView>
  );
}

function Consent({
  checked,
  onToggle,
  label,
}: {
  checked: boolean;
  onToggle: () => void;
  label: string;
}) {
  return (
    <Pressable
      onPress={onToggle}
      accessibilityRole="checkbox"
      accessibilityState={{ checked }}
      className="flex-row items-start gap-3 mb-3"
    >
      <View
        className={`w-6 h-6 rounded-lg border-2 items-center justify-center mt-0.5 ${
          checked ? 'bg-rose-500 border-rose-500' : 'border-ink-300 dark:border-ink-600'
        }`}
      >
        {checked ? <Text className="text-white text-xs font-bold">✓</Text> : null}
      </View>
      <Text className="flex-1 text-sm text-ink-600 dark:text-ink-300 leading-5">
        {label}
      </Text>
    </Pressable>
  );
}
