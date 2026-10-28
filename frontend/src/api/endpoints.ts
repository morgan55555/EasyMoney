/**
 * One function per endpoint (AGENTS.md §3.6). Request bodies are JSON strings
 * (money as strings, §2.12).
 */

import { apiFetch, getTokens } from './client'
import type {
  ActivityPageOut,
  AdminStatsOut,
  AdminUserCreateBody,
  AdminUserOut,
  AdminUserPatchBody,
  AttachmentOut,
  AuthStatusOut,
  AvatarSetResponse,
  CurrencyBalanceOut,
  EmailChangeResponse,
  EmailConfirmOut,
  ExpenseCreateBody,
  ExpenseOut,
  ExpenseUpdateBody,
  GroupBalancesOut,
  GroupOut,
  InviteOut,
  LoginRequest,
  MePatchBody,
  MemberOut,
  NotificationListOut,
  ParticipantIn,
  RegisterRequest,
  SettlementOut,
  TokenResponse,
  UserOut,
} from '../types'

export const authApi = {
  register: (body: RegisterRequest) =>
    apiFetch<TokenResponse>('/api/auth/register', { method: 'POST', body: JSON.stringify(body) }, { isAuthCall: true }),
  login: (body: LoginRequest) =>
    apiFetch<TokenResponse>('/api/auth/login', { method: 'POST', body: JSON.stringify(body) }, { isAuthCall: true }),
  logout: (refreshToken: string) =>
    apiFetch<void>('/api/auth/logout', { method: 'POST', body: JSON.stringify({ refresh_token: refreshToken }) }, { isAuthCall: true }),
  /** Public: whether the registration form should be shown (§2.13). */
  status: () =>
    apiFetch<AuthStatusOut>('/api/auth/status', undefined, { isAuthCall: true }),
  /** Apply a confirmation token (register or change). */
  confirmEmail: (token: string) =>
    apiFetch<EmailConfirmOut>('/api/auth/email/confirm', { method: 'POST', body: JSON.stringify({ token }) }, { isAuthCall: true }),
  /** Resend the user's pending register confirmation (authenticated, 204). */
  resendConfirmation: () =>
    apiFetch<void>('/api/auth/email/resend', { method: 'POST' }),
}

export const meApi = {
  get: () => apiFetch<UserOut>('/api/me'),
  patch: (body: MePatchBody) =>
    apiFetch<UserOut>('/api/me', { method: 'PATCH', body: JSON.stringify(body) }),
  changeEmail: (new_email: string) =>
    apiFetch<EmailChangeResponse>('/api/me/email/change', { method: 'POST', body: JSON.stringify({ new_email }) }),
  setAvatar: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return apiFetch<AvatarSetResponse>('/api/me/avatar', { method: 'POST', body: form })
  },
  clearAvatar: () => apiFetch<void>('/api/me/avatar', { method: 'DELETE' }),
}

export const adminApi = {
  listUsers: () => apiFetch<AdminUserOut[]>('/api/admin/users'),
  createUser: (body: AdminUserCreateBody) =>
    apiFetch<AdminUserOut>('/api/admin/users', { method: 'POST', body: JSON.stringify(body) }),
  patchUser: (id: number, body: AdminUserPatchBody) =>
    apiFetch<AdminUserOut>(`/api/admin/users/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  stats: () => apiFetch<AdminStatsOut>('/api/admin/stats'),
}

export const groupsApi = {
  list: () => apiFetch<GroupOut[]>('/api/groups'),
  create: (name: string, currency: string) =>
    apiFetch<GroupOut>('/api/groups', { method: 'POST', body: JSON.stringify({ name, currency }) }),
  get: (id: number) => apiFetch<GroupOut>(`/api/groups/${id}`),
  rename: (id: number, name: string) =>
    apiFetch<GroupOut>(`/api/groups/${id}`, { method: 'PATCH', body: JSON.stringify({ name }) }),
  close: (id: number) => apiFetch<GroupOut>(`/api/groups/${id}/close`, { method: 'POST' }),
  reopen: (id: number) => apiFetch<GroupOut>(`/api/groups/${id}/reopen`, { method: 'POST' }),
  members: (id: number) => apiFetch<MemberOut[]>(`/api/groups/${id}/members`),
  removeMember: (id: number, userId: number) =>
    apiFetch<void>(`/api/groups/${id}/members/${userId}`, { method: 'DELETE' }),
  leave: (id: number) => apiFetch<void>(`/api/groups/${id}/members/leave`, { method: 'POST' }),
  listInvites: (id: number) => apiFetch<InviteOut[]>(`/api/groups/${id}/invites`),
  createInvite: (id: number, kind: 'link' | 'email', emailTo?: string) =>
    apiFetch<InviteOut>(`/api/groups/${id}/invites`, {
      method: 'POST',
      body: JSON.stringify(kind === 'email' ? { kind, email_to: emailTo } : { kind }),
    }),
  revokeInvite: (inviteId: number) => apiFetch<void>(`/api/invites/${inviteId}`, { method: 'DELETE' }),
}

export const invitesApi = {
  /** Authenticated (documented deviation, AGENTS.md §2.10). Returns the group. */
  accept: (token: string) =>
    apiFetch<GroupOut>('/api/invites/accept', { method: 'POST', body: JSON.stringify({ token }) }),
}

export const expensesApi = {
  list: (groupId: number) => apiFetch<ExpenseOut[]>(`/api/groups/${groupId}/expenses`),
  get: (id: number) => apiFetch<ExpenseOut>(`/api/expenses/${id}`),
  create: (groupId: number, body: ExpenseCreateBody) =>
    apiFetch<ExpenseOut>(`/api/groups/${groupId}/expenses`, { method: 'POST', body: JSON.stringify(body) }),
  update: (id: number, body: ExpenseUpdateBody) =>
    apiFetch<ExpenseOut>(`/api/expenses/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  remove: (id: number) => apiFetch<void>(`/api/expenses/${id}`, { method: 'DELETE' }),
  uploadReceipt: (id: number, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return apiFetch<AttachmentOut>(`/api/expenses/${id}/receipt`, { method: 'POST', body: form })
  },
  deleteReceipt: (id: number) => apiFetch<void>(`/api/expenses/${id}/receipt`, { method: 'DELETE' }),
}

/** Fetch a binary resource with the bearer token (plain <a>/<img> can't set it). */
export async function fetchAuthedBlob(path: string): Promise<Blob> {
  const res = await fetch(path, {
    headers: { Authorization: `Bearer ${getTokens()?.access ?? ''}` },
  })
  if (!res.ok) throw new Error(`Could not load resource (${res.status})`)
  return res.blob()
}

/** Fetch an attachment with the bearer token. */
export async function fetchAttachmentBlob(attachmentId: number): Promise<Blob> {
  return fetchAuthedBlob(`/api/attachments/${attachmentId}`)
}

export const balancesApi = {
  group: (id: number) => apiFetch<GroupBalancesOut>(`/api/groups/${id}/balances`),
  all: () => apiFetch<CurrencyBalanceOut[]>('/api/balances/all'),
}

export const settlementsApi = {
  list: (groupId: number) => apiFetch<SettlementOut[]>(`/api/groups/${groupId}/settlements`),
  propose: (groupId: number, toUserId: number, amount: string) =>
    apiFetch<SettlementOut>(`/api/groups/${groupId}/settlements`, {
      method: 'POST',
      body: JSON.stringify({ to_user_id: toUserId, amount }),
    }),
  confirm: (id: number) => apiFetch<SettlementOut>(`/api/settlements/${id}/confirm`, { method: 'POST' }),
  reject: (id: number) => apiFetch<SettlementOut>(`/api/settlements/${id}/reject`, { method: 'POST' }),
  cancel: (id: number) => apiFetch<SettlementOut>(`/api/settlements/${id}/cancel`, { method: 'POST' }),
}

export const notificationsApi = {
  list: () => apiFetch<NotificationListOut>('/api/notifications'),
  markRead: (ids?: number[]) =>
    apiFetch<{ marked: number }>('/api/notifications/read', {
      method: 'POST',
      body: JSON.stringify(ids ? { ids } : {}),
    }),
}

export const feedApi = {
  page: (groupId: number, cursor?: string, limit = 30) => {
    const params = new URLSearchParams({ limit: String(limit) })
    if (cursor) params.set('cursor', cursor)
    return apiFetch<ActivityPageOut>(`/api/groups/${groupId}/activity?${params.toString()}`)
  },
}

export { ParticipantIn }
