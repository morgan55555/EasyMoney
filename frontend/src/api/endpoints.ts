/**
 * One function per endpoint (AGENTS.md §3.6). Request bodies are JSON strings
 * (money as strings, §2.12).
 */

import { apiFetch, getTokens } from './client'
import type {
  ActivityPageOut,
  AttachmentOut,
  CurrencyBalanceOut,
  ExpenseCreateBody,
  ExpenseOut,
  ExpenseUpdateBody,
  GroupBalancesOut,
  GroupOut,
  InviteOut,
  LoginRequest,
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
}

export const meApi = {
  get: () => apiFetch<UserOut>('/api/me'),
  patch: (display_name: string) =>
    apiFetch<UserOut>('/api/me', { method: 'PATCH', body: JSON.stringify({ display_name }) }),
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

/** Fetch an attachment with the bearer token (plain <a>/<img> can't set it). */
export async function fetchAttachmentBlob(attachmentId: number): Promise<Blob> {
  const res = await fetch(`/api/attachments/${attachmentId}`, {
    headers: { Authorization: `Bearer ${getTokens()?.access ?? ''}` },
  })
  if (!res.ok) throw new Error(`Could not load the attachment (${res.status})`)
  return res.blob()
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
