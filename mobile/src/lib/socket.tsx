/**
 * The chat WebSocket client.
 *
 * Responsibilities beyond "open a socket":
 *
 * * **Reconnection** with exponential backoff and jitter, so a server restart
 *   does not produce a thundering herd of reconnects from every client.
 * * **Heartbeats** — a `ping` every 25s. Mobile networks and proxies drop idle
 *   sockets silently, and without this the app would look connected while
 *   messages went nowhere.
 * * **Re-auth on close 1008** — the server rejects an expired token with a
 *   policy-violation close. We refresh the access token and reconnect instead
 *   of retrying with the same dead credential.
 * * **Fan-out to subscribers** so several screens can listen at once.
 */

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

import { WS_URL } from './config';
import * as storage from './storage';
import { useAuth } from './auth-context';
import type { ServerFrame } from './types';

type Listener = (frame: ServerFrame) => void;
export type ConnectionState = 'connecting' | 'open' | 'closed';

interface SocketApi {
  state: ConnectionState;
  /** Subscribe to server frames. Returns an unsubscribe function. */
  subscribe: (listener: Listener) => () => void;
  sendMessage: (
    conversationId: string,
    body: string,
    clientNonce: string,
  ) => boolean;
  sendTyping: (conversationId: string, isTyping: boolean) => void;
  markRead: (conversationId: string, upToMessageId?: string) => void;
}

const SocketContext = createContext<SocketApi | undefined>(undefined);

const HEARTBEAT_MS = 25_000;
const MAX_BACKOFF_MS = 30_000;

export function SocketProvider({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, user } = useAuth();
  const [state, setState] = useState<ConnectionState>('closed');

  const socketRef = useRef<WebSocket | null>(null);
  const listenersRef = useRef<Set<Listener>>(new Set());
  const attemptRef = useRef(0);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const heartbeatTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  // Set on unmount/sign-out so an in-flight reconnect does not resurrect it.
  const closedByUsRef = useRef(false);

  const clearTimers = useCallback(() => {
    if (reconnectTimer.current) {
      clearTimeout(reconnectTimer.current);
      reconnectTimer.current = null;
    }
    if (heartbeatTimer.current) {
      clearInterval(heartbeatTimer.current);
      heartbeatTimer.current = null;
    }
  }, []);

  const emit = useCallback((frame: ServerFrame) => {
    for (const listener of listenersRef.current) {
      try {
        listener(frame);
      } catch (error) {
        // One bad subscriber must not stop the others from being notified.
        console.warn('WebSocket listener threw', error);
      }
    }
  }, []);

  const connect = useCallback(async () => {
    if (!isAuthenticated) return;
    if (
      socketRef.current &&
      (socketRef.current.readyState === WebSocket.OPEN ||
        socketRef.current.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }

    const token = await storage.getAccessToken();
    if (!token) return;

    setState('connecting');
    // The token goes in the query string because a browser cannot set
    // headers on a WebSocket handshake.
    const socket = new WebSocket(`${WS_URL}?token=${encodeURIComponent(token)}`);
    socketRef.current = socket;

    socket.onopen = () => {
      attemptRef.current = 0;
      setState('open');
      heartbeatTimer.current = setInterval(() => {
        if (socket.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ type: 'ping' }));
        }
      }, HEARTBEAT_MS);
    };

    socket.onmessage = (event) => {
      try {
        emit(JSON.parse(event.data as string) as ServerFrame);
      } catch {
        // Ignore frames we cannot parse rather than tearing down the socket.
      }
    };

    socket.onerror = () => {
      // `onclose` always follows, which is where reconnection is handled.
    };

    socket.onclose = async (event) => {
      clearTimers();
      socketRef.current = null;
      setState('closed');

      if (closedByUsRef.current || !isAuthenticated) return;

      // 1008 = the server rejected our token. Refresh before retrying, or we
      // would reconnect with the same expired credential forever.
      if (event.code === 1008) {
        try {
          // A cheap authenticated call triggers the client's refresh-and-retry.
          const { auth: authApi } = await import('./api');
          await authApi.me();
        } catch {
          // Refresh failed; the auth context will route to sign-in.
          return;
        }
      }

      const attempt = (attemptRef.current += 1);
      // Exponential backoff with jitter, so many clients reconnecting after a
      // deploy do not arrive in lockstep.
      const base = Math.min(MAX_BACKOFF_MS, 1000 * 2 ** (attempt - 1));
      const delay = base / 2 + Math.random() * (base / 2);
      reconnectTimer.current = setTimeout(() => void connect(), delay);
    };
  }, [isAuthenticated, clearTimers, emit]);

  useEffect(() => {
    closedByUsRef.current = false;

    if (isAuthenticated) {
      void connect();
    }

    return () => {
      closedByUsRef.current = true;
      clearTimers();
      socketRef.current?.close();
      socketRef.current = null;
      setState('closed');
    };
    // Reconnect when the signed-in identity changes, not on every render.
  }, [isAuthenticated, user?.id, connect, clearTimers]);

  const send = useCallback((payload: Record<string, unknown>): boolean => {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) return false;
    socket.send(JSON.stringify(payload));
    return true;
  }, []);

  const api = useMemo<SocketApi>(
    () => ({
      state,
      subscribe: (listener: Listener) => {
        listenersRef.current.add(listener);
        return () => listenersRef.current.delete(listener);
      },
      sendMessage: (conversationId, body, clientNonce) =>
        send({
          type: 'message.send',
          conversation_id: conversationId,
          body,
          client_nonce: clientNonce,
        }),
      sendTyping: (conversationId, isTyping) => {
        send({
          type: 'typing',
          conversation_id: conversationId,
          is_typing: isTyping,
        });
      },
      markRead: (conversationId, upToMessageId) => {
        send({
          type: 'message.read',
          conversation_id: conversationId,
          up_to_message_id: upToMessageId,
        });
      },
    }),
    [state, send],
  );

  return <SocketContext.Provider value={api}>{children}</SocketContext.Provider>;
}

export function useSocket(): SocketApi {
  const context = useContext(SocketContext);
  if (context === undefined) {
    throw new Error('useSocket must be used inside a SocketProvider');
  }
  return context;
}

/** Subscribe to socket frames for the lifetime of a component. */
export function useSocketEvent(listener: Listener): void {
  const { subscribe } = useSocket();
  const ref = useRef(listener);
  ref.current = listener;

  useEffect(() => subscribe((frame) => ref.current(frame)), [subscribe]);
}
