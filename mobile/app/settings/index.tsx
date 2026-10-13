/**
 * Settings hub, including account deletion.
 *
 * Deletion asks for the password in-screen because the backend requires it:
 * holding a valid token is not sufficient to destroy an account.
 */

import { useState } from 'react';
import { Alert, Modal, Pressable, ScrollView, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ApiError, auth } from '@/lib/api';
import { useAuth } from '@/lib/auth-context';
import { APP_NAME } from '@/lib/config';
import { Banner, Button, Card, Divider, Field, Row } from '@/components/ui';

export default function Settings() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { user, signOut } = useAuth();

  const [deleteOpen, setDeleteOpen] = useState(false);
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function deleteAccount() {
    setBusy(true);
    setError(null);
    try {
      await auth.deleteAccount(password);
      setDeleteOpen(false);
      // Clearing the user sends the routing gate back to the welcome screen.
      await signOut();
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : 'Could not delete your account.',
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <ScrollView
        className="flex-1 bg-ink-50 dark:bg-ink-900"
        contentContainerClassName="px-6 pb-16"
        contentContainerStyle={{ paddingTop: insets.top + 16 }}
      >
        <View className="flex-row items-center gap-2 mb-6">
          <Pressable
            onPress={() => router.back()}
            accessibilityLabel="Back"
            className="w-9 h-9 items-center justify-center -ml-2"
          >
            <Text className="text-2xl text-ink-600 dark:text-ink-300">‹</Text>
          </Pressable>
          <Text className="font-display text-3xl text-ink-900 dark:text-ink-50">
            Settings
          </Text>
        </View>

        <Card className="mb-4">
          <Text className="text-xs uppercase tracking-wide text-ink-400 mb-1">
            Signed in as
          </Text>
          <Text className="text-base text-ink-900 dark:text-ink-50">
            {user?.email}
          </Text>
          {user?.is_admin ? (
            <View className="mt-2 self-start rounded-pill bg-plum-500 px-3 py-1">
              <Text className="text-white text-xs font-semibold">
                MODERATOR
              </Text>
            </View>
          ) : null}
        </Card>

        <Card className="mb-4">
          <Text className="text-xs uppercase tracking-wide text-ink-400 mb-1">
            Discovery
          </Text>
          <Row
            label="Who you see"
            onPress={() => router.push('/settings/preferences')}
          />
          <Divider />
          <Row
            label="Privacy and visibility"
            onPress={() => router.push('/settings/privacy')}
          />
          <Divider />
          <Row
            label="Blocked people"
            onPress={() => router.push('/settings/blocked')}
          />
        </Card>

        <Card className="mb-4">
          <Text className="text-xs uppercase tracking-wide text-ink-400 mb-1">
            Account
          </Text>
          <Row
            label="Change password"
            onPress={() => router.push('/settings/password')}
          />
          <Divider />
          <Row
            label="Terms of Service"
            onPress={() => router.push('/(auth)/terms')}
          />
          <Divider />
          <Row
            label="Privacy Policy"
            onPress={() => router.push('/(auth)/privacy')}
          />
        </Card>

        {user?.is_admin ? (
          <Card className="mb-4">
            <Text className="text-xs uppercase tracking-wide text-ink-400 mb-1">
              Moderation
            </Text>
            <Row
              label="Moderation dashboard"
              onPress={() => router.push('/admin')}
            />
          </Card>
        ) : null}

        <View className="gap-3 mt-2">
          <Button
            label="Sign out"
            variant="secondary"
            onPress={() => void signOut()}
          />
          <Button
            label="Sign out on all devices"
            variant="ghost"
            onPress={() =>
              Alert.alert(
                'Sign out everywhere?',
                'Every device signed in to this account will be signed out.',
                [
                  { text: 'Cancel', style: 'cancel' },
                  {
                    text: 'Sign out everywhere',
                    style: 'destructive',
                    onPress: () => void signOut(true),
                  },
                ],
              )
            }
          />
        </View>

        <Divider />

        <Text className="text-xs uppercase tracking-wide text-danger mb-2 mt-2">
          Danger zone
        </Text>
        <Text className="text-sm text-ink-500 dark:text-ink-400 leading-6 mb-4">
          Deleting your account is permanent. Your profile, photos, matches and
          messages are removed and cannot be restored.
        </Text>
        <Button
          label={`Delete my ${APP_NAME} account`}
          variant="danger"
          onPress={() => setDeleteOpen(true)}
        />
      </ScrollView>

      <Modal
        visible={deleteOpen}
        transparent
        animationType="slide"
        onRequestClose={() => setDeleteOpen(false)}
      >
        <View className="flex-1 justify-end bg-black/50">
          <View
            className="bg-ink-50 dark:bg-ink-900 rounded-t-card px-6 pt-6"
            style={{ paddingBottom: insets.bottom + 20 }}
          >
            <Text className="font-display text-2xl text-ink-900 dark:text-ink-50 mb-2">
              Delete your account
            </Text>
            <Text className="text-sm text-ink-500 dark:text-ink-400 leading-6 mb-5">
              This cannot be undone. Enter your password to confirm.
            </Text>

            {error ? <Banner message={error} /> : null}

            <Field
              label="Password"
              value={password}
              onChangeText={setPassword}
              secureTextEntry
              placeholder="Your current password"
            />

            <Button
              label="Permanently delete"
              variant="danger"
              onPress={() => void deleteAccount()}
              loading={busy}
              disabled={password.length === 0 || busy}
            />
            <Pressable
              onPress={() => {
                setDeleteOpen(false);
                setPassword('');
                setError(null);
              }}
              className="py-3 mt-1"
            >
              <Text className="text-center text-ink-500">Keep my account</Text>
            </Pressable>
          </View>
        </View>
      </Modal>
    </>
  );
}
