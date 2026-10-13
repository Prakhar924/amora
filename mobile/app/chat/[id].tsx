/**
 * The conversation screen.
 *
 * Message sending is optimistic and reconciled by `client_nonce`:
 *
 * 1. A pending bubble is rendered immediately with a locally generated nonce.
 * 2. The frame goes out over the socket; if the socket is down we fall back
 *    to the REST endpoint, so a message is never silently lost.
 * 3. The server echoes `message.created` carrying the same nonce, and the
 *    pending bubble is replaced by the authoritative row.
 *
 * Because the backend treats `(conversation_id, client_nonce)` as unique, a
 * retry after a reconnect cannot produce a duplicate.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  KeyboardAvoidingView,
  type NativeSyntheticEvent,
  Platform,
  Pressable,
  Text,
  TextInput,
  type TextInputKeyPressEventData,
  View,
} from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ApiError, chat } from '@/lib/api';
import { useAuth } from '@/lib/auth-context';
import { useSocket, useSocketEvent } from '@/lib/socket';
import { Banner, ErrorState, Loading } from '@/components/ui';
import type { Message } from '@/lib/types';

/** A message that has not been acknowledged by the server yet. */
interface Pending {
  nonce: string;
  body: string;
  createdAt: string;
  failed: boolean;
}

function newNonce(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
  });
}

export default function ChatScreen() {
  const { id, name, userId, matchId } = useLocalSearchParams<{
    id: string;
    name?: string;
    userId?: string;
    matchId?: string;
  }>();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { user } = useAuth();
  const { sendMessage, sendTyping, markRead, state: socketState } = useSocket();

  const [messages, setMessages] = useState<Message[]>([]);
  const [pending, setPending] = useState<Pending[]>([]);
  const [draft, setDraft] = useState('');
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [theyAreTyping, setTheyAreTyping] = useState(false);

  const typingTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const typingSentAt = useRef(0);

  // --- Initial load ----------------------------------------------------
  const load = useCallback(async () => {
    try {
      const page = await chat.history(id, { limit: 40 });
      setMessages(page.messages);
      setCursor(page.next_cursor);
      setError(null);
      // Opening the thread is what marks it read.
      await chat.markRead(id);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : 'Could not load this conversation.',
      );
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  const loadOlder = useCallback(async () => {
    if (!cursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const page = await chat.history(id, { limit: 40, cursor });
      setMessages((current) => [...current, ...page.messages]);
      setCursor(page.next_cursor);
    } catch {
      // Leave the history as-is; the user can scroll again to retry.
    } finally {
      setLoadingMore(false);
    }
  }, [cursor, id, loadingMore]);

  // --- Live updates -----------------------------------------------------
  useSocketEvent((frame) => {
    if (frame.type === 'message.created') {
      const incoming = frame.message;
      if (incoming.conversation_id !== id) return;

      setMessages((current) => {
        if (current.some((m) => m.id === incoming.id)) return current;
        return [incoming, ...current];
      });

      // Drop the optimistic bubble this message corresponds to.
      if (incoming.client_nonce) {
        setPending((current) =>
          current.filter((p) => p.nonce !== incoming.client_nonce),
        );
      }

      // Their message arriving while we are looking at the thread is read.
      if (incoming.sender_id !== user?.id) {
        markRead(id, incoming.id);
        setTheyAreTyping(false);
      }
    } else if (frame.type === 'typing') {
      if (frame.conversation_id === id && frame.user_id !== user?.id) {
        setTheyAreTyping(frame.is_typing);
        if (frame.is_typing) {
          // Clear a stale indicator if the "stopped typing" frame is lost.
          if (typingTimeout.current) clearTimeout(typingTimeout.current);
          typingTimeout.current = setTimeout(
            () => setTheyAreTyping(false),
            6000,
          );
        }
      }
    } else if (frame.type === 'match.removed') {
      setNotice('This conversation has ended.');
    } else if (frame.type === 'error' && frame.code === 'rate_limited') {
      setNotice(frame.detail);
    }
  });

  useEffect(
    () => () => {
      if (typingTimeout.current) clearTimeout(typingTimeout.current);
    },
    [],
  );

  // --- Sending -----------------------------------------------------------
  function onChangeDraft(text: string) {
    setDraft(text);
    // Throttle typing frames to at most one every two seconds.
    const now = Date.now();
    if (text.length > 0 && now - typingSentAt.current > 2000) {
      typingSentAt.current = now;
      sendTyping(id, true);
    }
  }

  const send = useCallback(async () => {
    const body = draft.trim();
    if (!body) return;

    const nonce = newNonce();
    setDraft('');
    sendTyping(id, false);
    setPending((current) => [
      ...current,
      { nonce, body, createdAt: new Date().toISOString(), failed: false },
    ]);

    // Prefer the socket; fall back to REST when it is not open.
    const delivered = sendMessage(id, body, nonce);
    if (delivered) return;

    try {
      const saved = await chat.send(id, body, nonce);
      setMessages((current) =>
        current.some((m) => m.id === saved.id) ? current : [saved, ...current],
      );
      setPending((current) => current.filter((p) => p.nonce !== nonce));
    } catch (caught) {
      setPending((current) =>
        current.map((p) => (p.nonce === nonce ? { ...p, failed: true } : p)),
      );
      setNotice(
        caught instanceof ApiError
          ? caught.message
          : 'Message could not be sent.',
      );
    }
  }, [draft, id, sendMessage, sendTyping]);

  /**
   * On web, Enter sends and Shift+Enter inserts a newline, which is what a
   * desktop user expects. On a touch keyboard Enter stays a newline, since
   * there is a visible send button and no modifier key available.
   */
  function handleKeyPress(
    event: NativeSyntheticEvent<TextInputKeyPressEventData>,
  ) {
    if (Platform.OS !== 'web') return;
    const native = event.nativeEvent as TextInputKeyPressEventData & {
      shiftKey?: boolean;
      preventDefault?: () => void;
    };
    if (native.key === 'Enter' && !native.shiftKey) {
      native.preventDefault?.();
      void send();
    }
  }

  async function retry(item: Pending) {
    setPending((current) =>
      current.map((p) => (p.nonce === item.nonce ? { ...p, failed: false } : p)),
    );
    try {
      const saved = await chat.send(id, item.body, item.nonce);
      setMessages((current) =>
        current.some((m) => m.id === saved.id) ? current : [saved, ...current],
      );
      setPending((current) => current.filter((p) => p.nonce !== item.nonce));
    } catch {
      setPending((current) =>
        current.map((p) =>
          p.nonce === item.nonce ? { ...p, failed: true } : p,
        ),
      );
    }
  }

  // Newest first, matching the inverted list.
  const rows = useMemo(
    () => [
      ...[...pending]
        .reverse()
        .map((p) => ({ kind: 'pending' as const, item: p })),
      ...messages.map((m) => ({ kind: 'message' as const, item: m })),
    ],
    [messages, pending],
  );

  if (loading) return <Loading label="Opening conversation…" />;
  if (error) return <ErrorState message={error} onRetry={() => void load()} />;

  return (
    <View className="flex-1 bg-ink-50 dark:bg-ink-900">
      {/* Header */}
      <View
        className="flex-row items-center gap-3 px-4 pb-3 border-b border-ink-200 dark:border-ink-800 bg-white dark:bg-ink-900"
        style={{ paddingTop: insets.top + 8 }}
      >
        <Pressable
          onPress={() => router.back()}
          accessibilityLabel="Back"
          className="w-9 h-9 items-center justify-center"
        >
          <Text className="text-2xl text-ink-600 dark:text-ink-300">‹</Text>
        </Pressable>

        <Pressable
          className="flex-1"
          onPress={() =>
            userId
              ? router.push({ pathname: '/profile/[id]', params: { id: userId } })
              : undefined
          }
        >
          <Text className="font-semibold text-lg text-ink-900 dark:text-ink-50">
            {name ?? 'Chat'}
          </Text>
          <Text className="text-xs text-ink-400">
            {theyAreTyping
              ? 'typing…'
              : socketState === 'open'
                ? 'Active now'
                : 'Reconnecting…'}
          </Text>
        </Pressable>

        <Pressable
          onPress={() =>
            userId
              ? router.push({
                  pathname: '/profile/[id]',
                  params: { id: userId, matchId: matchId ?? '' },
                })
              : undefined
          }
          accessibilityLabel="Safety options"
          className="w-9 h-9 items-center justify-center"
        >
          <Text className="text-xl">⋯</Text>
        </Pressable>
      </View>

      {notice ? (
        <View className="px-4 pt-3">
          <Banner message={notice} tone="info" />
        </View>
      ) : null}

      <KeyboardAvoidingView
        className="flex-1"
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        keyboardVerticalOffset={Platform.OS === 'ios' ? 0 : 0}
      >
        <FlatList
          data={rows}
          // Inverted so new messages appear at the bottom and older pages
          // load as the user scrolls up.
          inverted
          keyExtractor={(row) =>
            row.kind === 'pending' ? `p-${row.item.nonce}` : row.item.id
          }
          contentContainerClassName="px-4 py-4 gap-2"
          onEndReached={() => void loadOlder()}
          onEndReachedThreshold={0.4}
          ListFooterComponent={
            loadingMore ? (
              <View className="py-4">
                <ActivityIndicator color="#E0366B" />
              </View>
            ) : null
          }
          ListEmptyComponent={
            <View className="items-center justify-center py-16 px-8">
              <Text className="text-4xl mb-3">👋</Text>
              <Text className="text-center text-ink-500 dark:text-ink-400 leading-6">
                You matched with {name ?? 'them'}. Say something — a question
                works better than &quot;hey&quot;.
              </Text>
            </View>
          }
          renderItem={({ item: row }) => {
            if (row.kind === 'pending') {
              return (
                <Bubble
                  body={row.item.body}
                  mine
                  time={formatTime(row.item.createdAt)}
                  state={row.item.failed ? 'failed' : 'sending'}
                  onRetry={() => void retry(row.item)}
                />
              );
            }
            return (
              <Bubble
                body={row.item.body}
                mine={row.item.sender_id === user?.id}
                time={formatTime(row.item.created_at)}
                state="sent"
              />
            );
          }}
        />

        {/* Composer */}
        <View
          className="flex-row items-end gap-2 px-4 pt-2 border-t border-ink-200 dark:border-ink-800 bg-white dark:bg-ink-900"
          style={{ paddingBottom: insets.bottom + 8 }}
        >
          <TextInput
            value={draft}
            onChangeText={onChangeDraft}
            onKeyPress={handleKeyPress}
            placeholder="Write a message…"
            placeholderTextColor="#9B8C94"
            multiline
            maxLength={4000}
            // `blurOnSubmit={false}` keeps the keyboard up after Enter-to-send
            // on web, so a reply can be typed straight away.
            blurOnSubmit={false}
            className="flex-1 max-h-32 rounded-3xl px-4 py-3 bg-ink-100 dark:bg-ink-800 text-base text-ink-900 dark:text-ink-50"
          />
          <Pressable
            onPress={() => void send()}
            disabled={draft.trim().length === 0}
            accessibilityLabel="Send message"
            className={`w-11 h-11 rounded-pill items-center justify-center ${
              draft.trim().length === 0 ? 'bg-ink-200 dark:bg-ink-700' : 'bg-rose-500'
            }`}
          >
            <Text className="text-white text-lg">↑</Text>
          </Pressable>
        </View>
      </KeyboardAvoidingView>
    </View>
  );
}

function Bubble({
  body,
  mine,
  time,
  state,
  onRetry,
}: {
  body: string;
  mine: boolean;
  time: string;
  state: 'sending' | 'sent' | 'failed';
  onRetry?: () => void;
}) {
  return (
    <View className={`flex-row ${mine ? 'justify-end' : 'justify-start'}`}>
      <View className="max-w-[80%]">
        <View
          className={`rounded-3xl px-4 py-2.5 ${
            mine
              ? 'bg-rose-500 rounded-br-lg'
              : 'bg-white dark:bg-ink-800 rounded-bl-lg border border-ink-100 dark:border-ink-700'
          } ${state === 'failed' ? 'opacity-70' : ''}`}
        >
          <Text
            className={`text-base leading-6 ${
              mine ? 'text-white' : 'text-ink-900 dark:text-ink-50'
            }`}
          >
            {body}
          </Text>
        </View>

        <View
          className={`flex-row items-center gap-1.5 mt-1 ${
            mine ? 'justify-end' : 'justify-start'
          }`}
        >
          <Text className="text-[10px] text-ink-400">{time}</Text>
          {state === 'sending' ? (
            <Text className="text-[10px] text-ink-400">· sending</Text>
          ) : null}
          {state === 'failed' && onRetry ? (
            <Pressable onPress={onRetry} hitSlop={8}>
              <Text className="text-[10px] text-danger font-semibold">
                · failed, tap to retry
              </Text>
            </Pressable>
          ) : null}
        </View>
      </View>
    </View>
  );
}
