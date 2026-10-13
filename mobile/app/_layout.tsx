/**
 * Root layout.
 *
 * Owns three things: global providers, font loading, and the routing gate
 * that keeps a user in the right stack for their account state. Centralising
 * the gate here means no individual screen has to defend itself against being
 * reached by an unverified or half-onboarded user.
 */

import '../global.css';

import { useEffect } from 'react';
import { ActivityIndicator, View } from 'react-native';
import { Stack, usePathname, useRouter, useSegments } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import * as SplashScreen from 'expo-splash-screen';
import { useFonts } from 'expo-font';
import {
  Inter_400Regular,
  Inter_500Medium,
  Inter_600SemiBold,
  Inter_700Bold,
} from '@expo-google-fonts/inter';
import { Fraunces_600SemiBold } from '@expo-google-fonts/fraunces';

import { AuthProvider, useAuth } from '@/lib/auth-context';
import { SocketProvider } from '@/lib/socket';
import { ResponsiveShell } from '@/components/shell';

void SplashScreen.preventAutoHideAsync();

/**
 * Redirects the user to the stack that matches their account state.
 *
 * The order of these checks mirrors the backend's dependency ladder
 * (authenticated -> verified -> onboarded), so the client never navigates
 * somewhere the API would reject.
 */
function RoutingGate({ children }: { children: React.ReactNode }) {
  const { initializing, isAuthenticated, needsVerification, needsOnboarding } =
    useAuth();
  const segments = useSegments();
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    if (initializing) return;

    const group = segments[0] as string | undefined;
    const inAuthFlow = group === '(auth)';
    const inOnboarding = group === 'onboarding';

    if (!isAuthenticated) {
      // Let the public screens (welcome, sign-in, reset links) through.
      if (!inAuthFlow && group !== undefined) {
        router.replace('/(auth)/welcome');
      }
      return;
    }

    if (needsVerification) {
      // Compare the pathname rather than indexing into `segments`, whose
      // typed-routes tuple type does not admit an index beyond the first.
      if (!pathname.includes('verify-email')) {
        router.replace('/(auth)/verify-email');
      }
      return;
    }

    if (needsOnboarding) {
      if (!inOnboarding) router.replace('/onboarding/profile');
      return;
    }

    // Fully set up: push them out of the auth/onboarding stacks.
    if (inAuthFlow || inOnboarding) {
      router.replace('/(tabs)/discover');
    }
  }, [
    initializing,
    isAuthenticated,
    needsVerification,
    needsOnboarding,
    segments,
    pathname,
    router,
  ]);

  if (initializing) {
    return (
      <View className="flex-1 items-center justify-center bg-ink-900">
        <ActivityIndicator size="large" color="#E0366B" />
      </View>
    );
  }

  return <>{children}</>;
}

export default function RootLayout() {
  const [fontsLoaded, fontError] = useFonts({
    Inter_400Regular,
    Inter_500Medium,
    Inter_600SemiBold,
    Inter_700Bold,
    Fraunces_600SemiBold,
  });

  useEffect(() => {
    // Hide the splash on font error too, or a font CDN failure would leave
    // the user staring at the splash screen forever.
    if (fontsLoaded || fontError) {
      void SplashScreen.hideAsync();
    }
  }, [fontsLoaded, fontError]);

  if (!fontsLoaded && !fontError) return null;

  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaProvider>
        <AuthProvider>
          <SocketProvider>
            <StatusBar style="auto" />
            <RoutingGate>
              <ResponsiveShell>
              <Stack
                screenOptions={{
                  headerShown: false,
                  contentStyle: { backgroundColor: 'transparent' },
                  animation: 'slide_from_right',
                }}
              >
                <Stack.Screen name="index" />
                <Stack.Screen name="(auth)" />
                <Stack.Screen name="onboarding" />
                <Stack.Screen name="(tabs)" />
                <Stack.Screen
                  name="chat/[id]"
                  options={{ animation: 'slide_from_right' }}
                />
                <Stack.Screen
                  name="match/[id]"
                  options={{ presentation: 'modal', animation: 'fade' }}
                />
                <Stack.Screen name="profile/[id]" />
                <Stack.Screen name="settings/index" />
                <Stack.Screen name="settings/preferences" />
                <Stack.Screen name="settings/blocked" />
                <Stack.Screen name="settings/privacy" />
                <Stack.Screen name="settings/password" />
                <Stack.Screen name="admin/index" />
                <Stack.Screen name="admin/photos" />
                <Stack.Screen name="admin/reports" />
              </Stack>
              </ResponsiveShell>
            </RoutingGate>
          </SocketProvider>
        </AuthProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}
