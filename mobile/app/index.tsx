/**
 * Entry route.
 *
 * The routing gate in `_layout.tsx` owns every redirect decision, so this
 * screen only has to send the user into the gate's jurisdiction. Rendering a
 * spinner avoids a flash of the welcome screen for an already-signed-in user.
 */
import { Redirect } from 'expo-router';

import { useAuth } from '@/lib/auth-context';
import { Loading } from '@/components/ui';

export default function Index() {
  const { initializing, isAuthenticated } = useAuth();

  if (initializing) return <Loading label="Getting things ready…" />;

  return (
    <Redirect href={isAuthenticated ? '/(tabs)/discover' : '/(auth)/welcome'} />
  );
}
