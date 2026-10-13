/**
 * Session state for the whole app.
 *
 * Holds the signed-in user and the routing-relevant derived flags
 * (`needsVerification`, `needsOnboarding`) that the root layout uses to decide
 * which stack to show. Screens read this rather than calling `/auth/me`
 * themselves, so there is one source of truth for "who is signed in".
 */

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';

import { ApiError, auth, setUnauthorizedHandler, storage } from './api';
import type { Me } from './types';

interface AuthState {
  user: Me | null;
  /** True until the stored session has been checked on startup. */
  initializing: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: (everywhere?: boolean) => Promise<void>;
  /** Re-fetch `/auth/me`, e.g. after finishing onboarding. */
  refresh: () => Promise<Me | null>;
  isAuthenticated: boolean;
  needsVerification: boolean;
  needsOnboarding: boolean;
}

const AuthContext = createContext<AuthState | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<Me | null>(null);
  const [initializing, setInitializing] = useState(true);

  const refresh = useCallback(async (): Promise<Me | null> => {
    try {
      const me = await auth.me();
      setUser(me);
      return me;
    } catch (error) {
      // A 401 means the session is genuinely gone. Any other failure (the
      // server being briefly unreachable) must not sign the user out.
      if (error instanceof ApiError && error.isAuthError) {
        setUser(null);
      }
      return null;
    }
  }, []);

  // Restore the session on cold start.
  useEffect(() => {
    let cancelled = false;

    (async () => {
      if (await storage.hasSession()) {
        await refresh();
      }
      if (!cancelled) setInitializing(false);
    })();

    return () => {
      cancelled = true;
    };
  }, [refresh]);

  // The API client calls this when a refresh attempt fails for good.
  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
  }, []);

  const signIn = useCallback(
    async (email: string, password: string) => {
      await auth.login(email, password);
      await refresh();
    },
    [refresh],
  );

  const signOut = useCallback(async (everywhere = false) => {
    try {
      await auth.logout(everywhere);
    } finally {
      // Clear local state regardless, so the UI never shows a stale user.
      setUser(null);
    }
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      user,
      initializing,
      signIn,
      signOut,
      refresh,
      isAuthenticated: user !== null,
      needsVerification: user !== null && !user.email_verified,
      // Swiping and chatting are gated on a complete profile by the backend,
      // so the client routes to onboarding rather than letting users hit 403s.
      needsOnboarding:
        user !== null && user.email_verified && !user.profile_complete,
    }),
    [user, initializing, signIn, signOut, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used inside an AuthProvider');
  }
  return context;
}
