/**
 * Types mirroring the backend's Pydantic schemas.
 *
 * These are the API contract as the client sees it. They are kept in one file
 * so a backend change surfaces as a TypeScript error at every call site
 * rather than as a runtime surprise in a screen.
 */

export type Gender = 'woman' | 'man' | 'nonbinary' | 'other';
export type SwipeAction = 'like' | 'pass' | 'superlike';
export type ModerationStatus = 'pending' | 'approved' | 'rejected';
export type UserStatus =
  | 'pending_verification'
  | 'active'
  | 'suspended'
  | 'deactivated'
  | 'deleted';

export type ReportReason =
  | 'spam'
  | 'harassment'
  | 'inappropriate_content'
  | 'fake_profile'
  | 'underage'
  | 'scam'
  | 'other';

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface Me {
  id: string;
  email: string;
  status: UserStatus;
  is_admin: boolean;
  email_verified: boolean;
  age: number;
  has_profile: boolean;
  profile_complete: boolean;
  created_at: string;
}

export interface Interest {
  slug: string;
  label: string;
  category: string;
}

export interface Photo {
  id: string;
  position: number;
  url: string;
  moderation_status: ModerationStatus;
  moderation_note: string | null;
  upload_confirmed: boolean;
  created_at: string;
}

export interface PublicPhoto {
  id: string;
  position: number;
  url: string;
}

export interface MyProfile {
  id: string;
  user_id: string;
  display_name: string;
  age: number;
  bio: string | null;
  gender: Gender;
  pronouns: string | null;
  job_title: string | null;
  education: string | null;
  height_cm: number | null;
  city: string | null;
  country: string | null;
  is_discoverable: boolean;
  show_only_to_matches: boolean;
  is_complete: boolean;
  interests: Interest[];
  photos: Photo[];
}

/**
 * Another user's profile. Note the absence of coordinates: the server sends a
 * rounded `distance_km` and never the location it was derived from.
 */
export interface PublicProfile {
  id: string;
  user_id: string;
  display_name: string;
  age: number;
  bio: string | null;
  gender: Gender;
  pronouns: string | null;
  job_title: string | null;
  education: string | null;
  height_cm: number | null;
  city: string | null;
  distance_km: number | null;
  interests: Interest[];
  photos: PublicPhoto[];
}

export interface Preferences {
  interested_in: string[];
  min_age: number;
  max_age: number;
  max_distance_km: number;
  notify_new_match: boolean;
  notify_new_message: boolean;
  notify_new_like: boolean;
}

export interface DiscoveryFeed {
  profiles: PublicProfile[];
  next_cursor: string | null;
  exhausted: boolean;
}

export interface Match {
  id: string;
  conversation_id: string | null;
  matched_at: string;
  profile: PublicProfile;
  unread_count: number;
  last_message_at: string | null;
  last_message_preview: string | null;
}

export interface SwipeResult {
  recorded: boolean;
  action: SwipeAction;
  is_match: boolean;
  match: Match | null;
  was_update: boolean;
}

export interface Message {
  id: string;
  conversation_id: string;
  sender_id: string;
  body: string;
  created_at: string;
  client_nonce: string | null;
}

export interface Conversation {
  id: string;
  match_id: string;
  other_user: PublicProfile;
  unread_count: number;
  last_message_at: string | null;
  last_message_preview: string | null;
  can_send: boolean;
}

export interface Notification {
  id: string;
  type: string;
  title: string;
  body: string | null;
  payload: Record<string, unknown>;
  read_at: string | null;
  created_at: string;
}

export interface PhotoUploadTicket {
  photo_id: string;
  upload_url: string;
  method: string;
  headers: Record<string, string>;
  expires_in: number;
}

// --- Admin -----------------------------------------------------------------
export interface AdminStats {
  total_users: number;
  active_users: number;
  pending_photo_moderation: number;
  open_reports: number;
  matches_created: number;
  messages_sent: number;
}

export interface AdminReport {
  id: string;
  reason: ReportReason;
  status: 'open' | 'reviewing' | 'actioned' | 'dismissed';
  details: string | null;
  created_at: string;
  reporter_id: string;
  reported_id: string;
  reported_display_name: string | null;
  reported_email: string | null;
  report_count_against_reported: number;
  conversation_id: string | null;
  message_id: string | null;
  message_body: string | null;
  photo_id: string | null;
}

export interface AdminPhoto {
  id: string;
  user_id: string;
  display_name: string;
  url: string;
  moderation_status: ModerationStatus;
  created_at: string;
}

// --- WebSocket frames ------------------------------------------------------
export type ServerFrame =
  | { type: 'ready'; user_id: string }
  | { type: 'pong' }
  | { type: 'message.created'; message: Message }
  | { type: 'message.read'; conversation_id: string; by_user_id: string }
  | { type: 'message.read.ack'; conversation_id: string; unread_count: number }
  | {
      type: 'typing';
      conversation_id: string;
      user_id: string;
      is_typing: boolean;
    }
  | { type: 'match.created'; match: Match }
  | { type: 'match.removed'; match_id: string }
  | { type: 'error'; detail: string; code: string; retry_after?: number };
