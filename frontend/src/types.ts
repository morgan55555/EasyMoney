/**
 * API types, mirroring the backend OpenAPI schemas (backend/app/schemas/*).
 * Money is always a string on the wire (AGENTS.md §2.12).
 */

// --- Auth (§2.11) ---------------------------------------------------------

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
}

export interface RegisterRequest {
  email: string
  password: string
  display_name?: string
  /** UI language chosen on the login page (§2.13); 'en' when not sent. */
  preferred_language?: 'en' | 'ru'
}

export interface LoginRequest {
  email: string
  password: string
}

// --- Users ------------------------------------------------------------------

export interface UserOut {
  id: number
  email: string
  display_name: string
  created_at: string
  is_admin: boolean
  email_verified: boolean
  preferred_language: 'en' | 'ru'
  /** Preferred currency (ISO 4217) or null; pinned to the top of the currency
   * picker and used as its default in the UI. */
  preferred_currency: string | null
  /** Short avatar URL or null (image served by GET /users/{id}/avatar). */
  avatar_url: string | null
  /** Effective server-side toggles (§2.13): true only when the confirmation
   * flow is actually enforced; the settings modal shows its hints accordingly. */
  email_change_confirmation: boolean
  password_change_confirmation: boolean
}

// --- Profile / avatar / email confirmation (§2.13) ----------------------------

/** PATCH /me body; all fields optional. */
export interface MePatchBody {
  display_name?: string
  preferred_language?: 'en' | 'ru'
  preferred_currency?: string
}

/** POST /me/avatar response. */
export interface AvatarSetResponse {
  ok: boolean
  avatar_url: string
}

/** GET /auth/status response (§2.13). */
export interface AuthStatusOut {
  registration_open: boolean
}

/** POST /me/email/change response. */
export interface EmailChangeResponse {
  /** "changed" (applied) or "confirmation_sent" (awaiting the old address). */
  status: 'changed' | 'confirmation_sent'
}

/** POST /me/password/change response. */
export interface PasswordChangeResponse {
  /** "changed" (applied) or "confirmation_sent" (awaiting the link). */
  status: 'changed' | 'confirmation_sent'
}

/** POST /auth/email/confirm response. */
export interface EmailConfirmOut {
  ok: boolean
  email: string
  email_verified: boolean
}

/** POST /auth/email/revert response ("this wasn't me" revert). */
export interface EmailRevertOut {
  ok: boolean
  email: string
}

// --- Admin (§2.13) -------------------------------------------------------------

export interface AdminUserOut {
  id: number
  email: string
  display_name: string
  is_admin: boolean
  email_verified: boolean
  avatar_url: string | null
  created_at: string
}

export interface AdminUserCreateBody {
  email: string
  password: string
  display_name?: string
}

/** PATCH /admin/users/{id} body; all fields optional. */
export interface AdminUserPatchBody {
  display_name?: string
  email?: string
  password?: string
  is_admin?: boolean
}

/** A money total rendered in a single currency (string, §2.12). */
export interface CurrencyTotalOut {
  currency: string
  total: string
}

export interface AdminStatsOut {
  users_total: number
  users_admin: number
  users_unverified: number
  groups_active: number
  groups_closed: number
  expenses_total: number
  expenses_by_currency: CurrencyTotalOut[]
  settlements_by_state: Record<string, number>
  settlements_confirmed_by_currency: CurrencyTotalOut[]
}

// --- Groups (§2.9, §2.12) ----------------------------------------------------

export interface GroupOut {
  id: number
  name: string
  currency: string
  status: 'active' | 'closed'
  created_by: number
  closed_at: string | null
  member_count: number
  /** The caller's balance in this group, pre-formatted (string). */
  balance: string
  /** Per-group reminder settings (AGENTS.md §2.7); `null` = the app-wide default. */
  debt_overdue_days: number | null
  reminder_interval_days: number | null
  /** The app-wide defaults/floor, echoed so the UI can show "— (default)". */
  debt_overdue_days_default: number
  reminder_interval_days_default: number
  reminder_interval_days_min: number
}

/** Owner-only group settings (AGENTS.md §2.7); `null` clears an override. */
export interface GroupSettingsBody {
  debt_overdue_days: number | null
  reminder_interval_days: number | null
}

export interface MemberOut {
  id: number
  display_name: string
  email: string
  joined_at: string
  is_owner: boolean
  avatar_url: string | null
}

// --- Expenses (§2.1, §2.5, §3.5) ----------------------------------------------

export type SplitMethod = 'equal' | 'weighted' | 'exact'

export interface ParticipantIn {
  user_id: number
  /** Weight for weighted splits (> 0). */
  weight?: string | null
  /** Exact share for exact splits. */
  amount?: string | null
}

export interface ExpenseCreateBody {
  payer_id: number
  description: string
  amount: string
  split_method: SplitMethod
  category: string
  /** ISO date, YYYY-MM-DD. */
  date: string
  comment?: string | null
  participants: ParticipantIn[]
}

export type ExpenseUpdateBody = Partial<ExpenseCreateBody>

export interface ParticipantOut {
  user_id: number
  display_name: string
  share: string
  weight: string | null
  avatar_url: string | null
}

export interface AttachmentOut {
  id: number
  filename: string
  content_type: string
  size: number
}

export interface ExpenseOut {
  id: number
  group_id: number
  payer_id: number
  payer: string
  payer_avatar_url: string | null
  created_by: number
  description: string
  comment: string | null
  amount: string
  split_method: SplitMethod
  category: string
  date: string
  deleted_at: string | null
  participants: ParticipantOut[]
  attachment: AttachmentOut | null
  created_at: string
}

// --- Balances (§2.2, §2.3) ----------------------------------------------------

export interface MemberBalanceOut {
  user_id: number
  display_name: string
  balance: string
  avatar_url: string | null
}

export interface TransferOut {
  from_user_id: number
  to_user_id: number
  amount: string
}

export interface GroupBalancesOut {
  currency: string
  balances: MemberBalanceOut[]
  /** Current pairwise view: every debtor -> creditor amount (§2.4). */
  pairs: TransferOut[]
  /** Greedy suggestion: at most n-1 transfers (§2.3). */
  transfers: TransferOut[]
}

export interface CurrencyBalanceOut {
  currency: string
  balance: string
}

// --- Settlements (§2.4) ---------------------------------------------------------

export type SettlementState = 'pending' | 'confirmed' | 'rejected' | 'cancelled'

export interface SettlementOut {
  id: number
  group_id: number
  from_user_id: number
  from_name: string
  from_avatar_url: string | null
  to_user_id: number
  to_name: string
  to_avatar_url: string | null
  amount: string
  state: SettlementState
  confirmed_at: string | null
  created_at: string
}

// --- Invitations (§2.10) ---------------------------------------------------------

export interface InviteOut {
  id: number
  kind: 'link' | 'email'
  token: string
  url: string
  email_to: string | null
  created_by: number
  expires_at: string
  status: 'active' | 'used' | 'revoked'
  used_by: number | null
  used_at: string | null
}

// --- Notifications (§2.6) --------------------------------------------------------

export interface NotificationOut {
  id: number
  type: string
  payload: Record<string, unknown> | null
  read_at: string | null
  created_at: string
}

export interface NotificationListOut {
  items: NotificationOut[]
  unread_count: number
}

// --- Web Push (§2.14) ------------------------------------------------------------

/** The browser's push subscription keys (base64url, no padding). */
export interface PushSubscriptionKeys {
  p256dh: string
  auth: string
}

/** A Web Push subscription (the browser's PushSubscription.toJSON() shape). */
export interface PushSubscriptionJson {
  endpoint: string
  keys: PushSubscriptionKeys
}

/** The VAPID public key endpoint response (base64url, no padding). */
export interface PushVapidKeyOut {
  key: string
}

// --- Activity feed (§3.3) ---------------------------------------------------------

export interface ActivityEventOut {
  id: number
  type: string
  actor_id: number | null
  actor_name: string | null
  actor_avatar_url: string | null
  ref_type: string | null
  ref_id: number | null
  payload: Record<string, unknown> | null
  created_at: string
}

export interface ActivityPageOut {
  items: ActivityEventOut[]
  next_cursor: string | null
}

// --- WebSocket (§3.2) ---------------------------------------------------------------

export interface WSMessage {
  type: string
  group_id: number | null
  data: Record<string, unknown> & {
    /** Present on every group event: the fresh balance snapshot. */
    group_balances?: GroupBalancesOut
  }
}
