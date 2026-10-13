/**
 * The HTTP client.
 *
 * Two things here are worth knowing before changing anything:
 *
 * 1. **Automatic token refresh.** A 401 triggers one refresh attempt and the
 *    original request is replayed. Concurrent 401s share a single in-flight
 *    refresh (`refreshInFlight`) so a screen that fires five requests at once
 *    does not burn five refresh tokens — which would trip the backend's
 *    theft detection and log the user out.
 *
 * 2. **Token storage is platform-dependent.** `expo-secure-store` is backed by
 *    the iOS keychain / Android keystore, but does not exist on web, where we
 *    fall back to `localStorage`. See `storage.ts`.
 */

import { API_BASE_URL } from './config';
import * as storage from './storage';
import type {
  AdminPhoto,
  AdminReport,
  AdminStats,
  Conversation,
  DiscoveryFeed,
  Gender,
  Interest,
  Match,
  Me,
  Message,
  MyProfile,
  Notification,
  PhotoUploadTicket,
  Preferences,
  PublicProfile,
  ReportReason,
  SwipeAction,
  SwipeResult,
  TokenPair,
} from './types';

export class ApiError extends Error {
  status: number;
  code?: string;

  constructor(status: number, message: string, code?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }

  /** True when the user should be shown a sign-in screen. */
  get isAuthError(): boolean {
    return this.status === 401;
  }
}

type Json = Record<string, unknown> | unknown[];

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE';
  body?: Json;
  /** Set for endpoints that must not trigger a refresh attempt. */
  skipAuth?: boolean;
  query?: Record<string, string | number | boolean | undefined>;
}

let refreshInFlight: Promise<boolean> | null = null;
let onUnauthorized: (() => void) | null = null;

/** Registered by the auth store so a dead session can clear app state. */
export function setUnauthorizedHandler(handler: () => void): void {
  onUnauthorized = handler;
}

function buildUrl(path: string, query?: RequestOptions['query']): string {
  const url = new URL(`${API_BASE_URL}${path}`);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== '') {
        url.searchParams.set(key, String(value));
      }
    }
  }
  return url.toString();
}

async function parseError(response: Response): Promise<ApiError> {
  let detail = `Request failed (${response.status})`;
  try {
    const body = await response.json();
    if (typeof body?.detail === 'string') {
      detail = body.detail;
    } else if (Array.isArray(body?.errors) && body.errors.length) {
      detail = String(body.errors[0]);
    }
  } catch {
    // A non-JSON error body (a proxy error page, say) keeps the default.
  }
  return new ApiError(response.status, detail);
}

/**
 * Exchange the stored refresh token for a new pair.
 *
 * Returns false when refreshing is impossible, in which case the caller
 * should treat the session as over.
 */
async function refreshTokens(): Promise<boolean> {
  const refreshToken = await storage.getRefreshToken();
  if (!refreshToken) return false;

  try {
    const response = await fetch(buildUrl('/auth/refresh'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });

    if (!response.ok) {
      // 401 here means the token was rotated, expired, or revoked for theft.
      await storage.clearTokens();
      return false;
    }

    const tokens: TokenPair = await response.json();
    await storage.setTokens(tokens.access_token, tokens.refresh_token);
    return true;
  } catch {
    // Network failure: keep the tokens, the user may just be offline.
    return false;
  }
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, skipAuth = false, query } = options;

  const send = async (): Promise<Response> => {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      Accept: 'application/json',
    };
    if (!skipAuth) {
      const token = await storage.getAccessToken();
      if (token) headers.Authorization = `Bearer ${token}`;
    }
    return fetch(buildUrl(path, query), {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  };

  let response: Response;
  try {
    response = await send();
  } catch (error) {
    throw new ApiError(
      0,
      'Cannot reach the server. Check your connection and try again.',
    );
  }

  // One refresh-and-retry on 401.
  if (response.status === 401 && !skipAuth) {
    refreshInFlight = refreshInFlight ?? refreshTokens();
    const refreshed = await refreshInFlight;
    refreshInFlight = null;

    if (refreshed) {
      response = await send();
    } else {
      await storage.clearTokens();
      onUnauthorized?.();
      throw new ApiError(401, 'Your session has expired. Please sign in again.');
    }
  }

  if (!response.ok) throw await parseError(response);

  if (response.status === 204) return undefined as T;
  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

// --------------------------------------------------------------------------
// Auth
// --------------------------------------------------------------------------
export const auth = {
  register: (input: {
    email: string;
    password: string;
    display_name: string;
    date_of_birth: string;
    gender: Gender;
    accept_terms: boolean;
    accept_privacy: boolean;
  }) =>
    request<{ message: string; user_id?: string }>('/auth/register', {
      method: 'POST',
      body: input,
      skipAuth: true,
    }),

  async login(email: string, password: string): Promise<TokenPair> {
    const tokens = await request<TokenPair>('/auth/login', {
      method: 'POST',
      body: { email, password },
      skipAuth: true,
    });
    await storage.setTokens(tokens.access_token, tokens.refresh_token);
    return tokens;
  },

  verifyEmail: (token: string) =>
    request<{ message: string }>('/auth/verify-email', {
      method: 'POST',
      body: { token },
      skipAuth: true,
    }),

  resendVerification: () =>
    request<{ message: string }>('/auth/resend-verification', {
      method: 'POST',
      body: {},
    }),

  forgotPassword: (email: string) =>
    request<{ message: string }>('/auth/forgot-password', {
      method: 'POST',
      body: { email },
      skipAuth: true,
    }),

  resetPassword: (token: string, newPassword: string) =>
    request<{ message: string }>('/auth/reset-password', {
      method: 'POST',
      body: { token, new_password: newPassword },
      skipAuth: true,
    }),

  changePassword: (currentPassword: string, newPassword: string) =>
    request<{ message: string }>('/auth/change-password', {
      method: 'POST',
      body: { current_password: currentPassword, new_password: newPassword },
    }),

  me: () => request<Me>('/auth/me'),

  async logout(everywhere = false): Promise<void> {
    const refreshToken = await storage.getRefreshToken();
    try {
      await request('/auth/logout', {
        method: 'POST',
        body: { refresh_token: refreshToken, everywhere },
      });
    } finally {
      // Clear locally even if the call fails, so the device is signed out.
      await storage.clearTokens();
    }
  },

  async deleteAccount(password: string): Promise<void> {
    await request('/auth/me', {
      method: 'DELETE',
      body: { password, confirmation: 'DELETE' },
    });
    await storage.clearTokens();
  },
};

// --------------------------------------------------------------------------
// Profile
// --------------------------------------------------------------------------
export const profiles = {
  mine: () => request<MyProfile>('/profiles/me'),

  update: (input: Partial<{
    display_name: string;
    bio: string | null;
    gender: Gender;
    pronouns: string | null;
    job_title: string | null;
    education: string | null;
    height_cm: number | null;
    interests: string[];
    is_discoverable: boolean;
    show_only_to_matches: boolean;
  }>) => request<MyProfile>('/profiles/me', { method: 'PATCH', body: input }),

  setLocation: (input: {
    latitude: number;
    longitude: number;
    city?: string;
    country?: string;
  }) =>
    request<{ message: string; city: string | null; country: string | null }>(
      '/profiles/me/location',
      { method: 'PUT', body: input },
    ),

  view: (userId: string) => request<PublicProfile>(`/profiles/${userId}`),

  interests: () => request<Interest[]>('/interests'),

  requestPhotoUpload: (contentType: string, byteSize: number) =>
    request<PhotoUploadTicket>('/profiles/me/photos', {
      method: 'POST',
      body: { content_type: contentType, byte_size: byteSize },
    }),

  confirmPhoto: (photoId: string) =>
    request<MyProfile>(`/profiles/me/photos/${photoId}/confirm`, {
      method: 'POST',
    }),

  deletePhoto: (photoId: string) =>
    request<MyProfile>(`/profiles/me/photos/${photoId}`, { method: 'DELETE' }),

  reorderPhotos: (photoIds: string[]) =>
    request<MyProfile>('/profiles/me/photos/order', {
      method: 'PUT',
      body: { photo_ids: photoIds },
    }),

  /**
   * Upload a photo end to end.
   *
   * Three steps, because bytes go straight to object storage and never
   * through the API: ask for a presigned ticket, PUT the file to it, then
   * tell the API the upload landed.
   */
  async uploadPhoto(uri: string, mimeType: string): Promise<MyProfile> {
    const fileResponse = await fetch(uri);
    const blob = await fileResponse.blob();
    const contentType = mimeType || blob.type || 'image/jpeg';

    const ticket = await profiles.requestPhotoUpload(contentType, blob.size);

    const upload = await fetch(ticket.upload_url, {
      method: 'PUT',
      headers: ticket.headers,
      body: blob,
    });
    if (!upload.ok) {
      throw new ApiError(
        upload.status,
        'Uploading the photo failed. Please try again.',
      );
    }

    return profiles.confirmPhoto(ticket.photo_id);
  },
};

// --------------------------------------------------------------------------
// Preferences
// --------------------------------------------------------------------------
export const preferences = {
  get: () => request<Preferences>('/preferences'),
  update: (input: Partial<Preferences>) =>
    request<Preferences>('/preferences', { method: 'PATCH', body: input }),
};

// --------------------------------------------------------------------------
// Discovery & matching
// --------------------------------------------------------------------------
export const discovery = {
  feed: (params?: { limit?: number; cursor?: string }) =>
    request<DiscoveryFeed>('/discovery', { query: params }),

  swipe: (targetUserId: string, action: SwipeAction) =>
    request<SwipeResult>('/discovery/swipe', {
      method: 'POST',
      body: { target_user_id: targetUserId, action },
    }),
};

export const matches = {
  list: () => request<{ matches: Match[]; total: number }>('/matches'),
  unmatch: (matchId: string) =>
    request<{ message: string }>(`/matches/${matchId}`, { method: 'DELETE' }),
};

// --------------------------------------------------------------------------
// Messaging
// --------------------------------------------------------------------------
export const chat = {
  conversations: () =>
    request<{ conversations: Conversation[]; total_unread: number }>(
      '/conversations',
    ),

  history: (conversationId: string, params?: { limit?: number; cursor?: string }) =>
    request<{ messages: Message[]; next_cursor: string | null }>(
      `/conversations/${conversationId}/messages`,
      { query: params },
    ),

  send: (conversationId: string, body: string, clientNonce?: string) =>
    request<Message>(`/conversations/${conversationId}/messages`, {
      method: 'POST',
      body: { body, client_nonce: clientNonce },
    }),

  markRead: (conversationId: string, upToMessageId?: string) =>
    request<{ unread_count: number }>(`/conversations/${conversationId}/read`, {
      method: 'POST',
      body: { up_to_message_id: upToMessageId },
    }),

  unreadCount: () =>
    request<{ total_unread: number }>('/conversations/unread-count'),
};

// --------------------------------------------------------------------------
// Safety
// --------------------------------------------------------------------------
export const safety = {
  block: (userId: string, reason?: string) =>
    request<{ message: string }>('/safety/block', {
      method: 'POST',
      body: { user_id: userId, reason },
    }),

  unblock: (userId: string) =>
    request<{ message: string }>(`/safety/block/${userId}`, { method: 'DELETE' }),

  blocks: () =>
    request<
      { user_id: string; display_name: string | null; blocked_at: string }[]
    >('/safety/blocks'),

  report: (input: {
    user_id: string;
    reason: ReportReason;
    details?: string;
    conversation_id?: string;
    message_id?: string;
    photo_id?: string;
  }) => request<{ id: string; status: string }>('/safety/report', {
    method: 'POST',
    body: input,
  }),
};

export const notifications = {
  list: (params?: { limit?: number; unread_only?: boolean }) =>
    request<{ notifications: Notification[]; unread_count: number }>(
      '/notifications',
      { query: params },
    ),
  markRead: (notificationId?: string) =>
    request<{ message: string }>('/notifications/read', {
      method: 'POST',
      query: { notification_id: notificationId },
    }),
};

// --------------------------------------------------------------------------
// Admin
// --------------------------------------------------------------------------
export const admin = {
  stats: () => request<AdminStats>('/admin/stats'),

  photoQueue: (status: 'pending' | 'approved' | 'rejected' = 'pending') =>
    request<AdminPhoto[]>('/admin/photos', { query: { status } }),

  moderatePhoto: (photoId: string, status: 'approved' | 'rejected', note?: string) =>
    request<{ message: string }>(`/admin/photos/${photoId}/moderate`, {
      method: 'POST',
      body: { status, note },
    }),

  reports: (status?: string) =>
    request<AdminReport[]>('/admin/reports', { query: { status } }),

  resolveReport: (
    reportId: string,
    input: { status: string; resolution_note?: string; suspend_days?: number },
  ) =>
    request<{ message: string }>(`/admin/reports/${reportId}/resolve`, {
      method: 'POST',
      body: input,
    }),

  suspendUser: (userId: string, days: number, reason: string) =>
    request<{ message: string }>(`/admin/users/${userId}/suspend`, {
      method: 'POST',
      body: { days, reason },
    }),

  reinstateUser: (userId: string) =>
    request<{ message: string }>(`/admin/users/${userId}/reinstate`, {
      method: 'POST',
    }),

  searchUsers: (q?: string) =>
    request<{ users: Array<Record<string, unknown>> }>('/admin/users', {
      query: { q },
    }),
};

export { storage };
