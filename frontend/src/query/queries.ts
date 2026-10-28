/**
 * TanStack Query layer (AGENTS.md §3.6).
 *
 * The WS bus is the only live writer to group-data caches: every group event
 * patches the balances key from the embedded snapshot (immediate, no extra
 * REST call) and invalidates the rest of that group's keys so the fresh
 * on-read aggregates flow in. REST remains the source for initial loads and
 * refetches; both converge because the server computes on read.
 */

import { useEffect, useRef, useState } from 'react'
import {
  QueryClient,
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from '@tanstack/react-query'

import * as api from '../api/endpoints'
import { invalidateAvatar } from '../lib/avatarCache'
import { initSound, playNotification } from '../lib/sound'
import { socket } from '../ws/socket'
import type {
  ActivityEventOut,
  ActivityPageOut,
  AdminUserCreateBody,
  AdminUserPatchBody,
  CurrencyBalanceOut,
  ExpenseCreateBody,
  ExpenseOut,
  ExpenseUpdateBody,
  GroupBalancesOut,
  GroupOut,
  InviteOut,
  MePatchBody,
  MemberOut,
  NotificationListOut,
  SettlementOut,
  UserOut,
} from '../types'

// --- Keys --------------------------------------------------------------------

export const keys = {
  me: ['me'] as const,
  groups: ['groups'] as const,
  group: (id: number) => ['group', id] as const,
  members: (id: number) => ['group', id, 'members'] as const,
  expenses: (id: number) => ['group', id, 'expenses'] as const,
  balances: (id: number) => ['group', id, 'balances'] as const,
  settlements: (id: number) => ['group', id, 'settlements'] as const,
  invites: (id: number) => ['group', id, 'invites'] as const,
  feed: (id: number, cursor: string | null) => ['group', id, 'feed', cursor] as const,
  notifications: ['notifications'] as const,
  overview: ['balances-all'] as const,
  authStatus: ['auth-status'] as const,
  adminUsers: ['admin', 'users'] as const,
  adminStats: ['admin', 'stats'] as const,
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false, // WS pushes keep us current
    },
  },
})

// --- WS -> cache wiring ----------------------------------------------------------

/** Invalidation for one group: everything about it (prefix match). */
function invalidateGroup(id: number): void {
  for (const key of [
    keys.group(id),
    keys.members(id),
    keys.expenses(id),
    keys.settlements(id),
    keys.invites(id),
    keys.feed(id, null), // prefix: all feed pages
    keys.balances(id),
  ]) {
    void queryClient.invalidateQueries({ queryKey: key })
  }
  void queryClient.invalidateQueries({ queryKey: keys.groups })
}

/**
 * The current user's avatar_url is also embedded in per-group payloads
 * (members, balances, expenses incl. participants, settlements, feed actors)
 * and the admin user list. After their own avatar is set/cleared these
 * caches are invalidated too, so every panel on the page renders the new
 * avatar in place — no tab switch or navigation needed.
 */
export function invalidateAvatarCarriers(qc: QueryClient): void {
  const groups = qc.getQueryData<GroupOut[]>(keys.groups) ?? []
  for (const g of groups) {
    for (const key of [
      keys.members(g.id),
      keys.balances(g.id),
      keys.expenses(g.id),
      keys.settlements(g.id),
      keys.feed(g.id, null), // prefix: all feed pages
    ]) {
      void qc.invalidateQueries({ queryKey: key })
    }
  }
  void qc.invalidateQueries({ queryKey: keys.adminUsers })
}

let bound = false

/** Wire the socket to the query client. Idempotent. */
export function bindSocket(): void {
  if (bound) return
  bound = true

  // Prime the notification sound on the first user gesture (autoplay policy).
  initSound()

  // After a drop + reconnect: refetch everything before trusting state (§3.2).
  socket.onReconnect = () => {
    void queryClient.invalidateQueries()
  }

  socket.onMessage((msg) => {
    if (msg.type === 'notification.created') {
      void queryClient.invalidateQueries({ queryKey: keys.notifications })
      playNotification()
      return
    }
    const groupId = msg.group_id
    if (groupId === null || groupId === undefined) return
    // Embedded fresh snapshot: patch the balances cache immediately.
    const snapshot = msg.data.group_balances
    if (snapshot) {
      queryClient.setQueryData<GroupBalancesOut>(keys.balances(groupId), snapshot)
    }
    invalidateGroup(groupId)
  })
}

// --- Queries -----------------------------------------------------------------------

export function useMe(): UseQueryResult<UserOut> {
  return useQuery({ queryKey: keys.me, queryFn: () => api.meApi.get() })
}

export function useGroups(): UseQueryResult<GroupOut[]> {
  return useQuery({ queryKey: keys.groups, queryFn: () => api.groupsApi.list() })
}

export function useGroup(id: number): UseQueryResult<GroupOut> {
  return useQuery({ queryKey: keys.group(id), queryFn: () => api.groupsApi.get(id) })
}

export function useMembers(id: number): UseQueryResult<MemberOut[]> {
  return useQuery({ queryKey: keys.members(id), queryFn: () => api.groupsApi.members(id) })
}

export function useExpenses(id: number): UseQueryResult<ExpenseOut[]> {
  return useQuery({ queryKey: keys.expenses(id), queryFn: () => api.expensesApi.list(id) })
}

export function useGroupBalances(id: number): UseQueryResult<GroupBalancesOut> {
  return useQuery({ queryKey: keys.balances(id), queryFn: () => api.balancesApi.group(id) })
}

export function useSettlements(id: number): UseQueryResult<SettlementOut[]> {
  return useQuery({ queryKey: keys.settlements(id), queryFn: () => api.settlementsApi.list(id) })
}

export function useInvites(id: number): UseQueryResult<InviteOut[]> {
  return useQuery({ queryKey: keys.invites(id), queryFn: () => api.groupsApi.listInvites(id) })
}

export function useNotifications(): UseQueryResult<NotificationListOut> {
  return useQuery({
    queryKey: keys.notifications,
    queryFn: () => api.notificationsApi.list(),
    refetchInterval: 60_000, // safety net; the WS push is the live path
  })
}

export function useOverview(): UseQueryResult<CurrencyBalanceOut[]> {
  return useQuery({ queryKey: keys.overview, queryFn: () => api.balancesApi.all() })
}

/** Public registration status (§2.13); controls the register form on /login. */
export function useAuthStatus() {
  return useQuery({ queryKey: keys.authStatus, queryFn: () => api.authApi.status(), staleTime: 60_000 })
}

export function useAdminUsers() {
  return useQuery({ queryKey: keys.adminUsers, queryFn: () => api.adminApi.listUsers() })
}

export function useAdminStats() {
  return useQuery({ queryKey: keys.adminStats, queryFn: () => api.adminApi.stats() })
}

/**
 * Feed with "load more" cursor pagination (AGENTS.md §3.3).
 *
 * The newest page lives in a TanStack Query keyed by `cursor = null`, so the
 * WS bus (which invalidates `keys.feed(id, null)`) refetches it live on every
 * group event. Older pages are fetched on demand and accumulated locally.
 * When new activity pushes the first page's boundary down, the accumulated
 * older pages no longer connect to it — they are dropped (and the user can
 * load them again) so the feed always stays contiguous.
 */
export function useFeed(id: number) {
  const firstPage = useQuery<ActivityPageOut>({
    queryKey: keys.feed(id, null),
    queryFn: () => api.feedApi.page(id, undefined),
  })

  const [extra, setExtra] = useState<ActivityEventOut[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [loadingMore, setLoadingMore] = useState(false)
  const firstBoundaryRef = useRef<string | null | undefined>(undefined)
  const loadGenRef = useRef(0)

  // Keep the continuation cursor in sync with the (possibly refetched) newest
  // page; a moved boundary drops the accumulated older pages.
  useEffect(() => {
    const boundary = firstPage.data?.next_cursor ?? null
    if (firstBoundaryRef.current !== boundary) {
      firstBoundaryRef.current = boundary
      loadGenRef.current += 1
      setCursor(boundary)
      setExtra([])
    }
  }, [firstPage.data])

  const loadMore = async () => {
    if (!cursor || loadingMore) return
    const gen = loadGenRef.current
    setLoadingMore(true)
    try {
      const page = await api.feedApi.page(id, cursor)
      if (gen !== loadGenRef.current) return // the feed moved while loading
      setExtra((prev) => [...prev, ...page.items])
      setCursor(page.next_cursor)
    } finally {
      setLoadingMore(false)
    }
  }

  return {
    items: [...(firstPage.data?.items ?? []), ...extra],
    hasMore: cursor !== null,
    loading: firstPage.isPending,
    loadingMore,
    error: firstPage.error,
    refetch: firstPage.refetch,
    loadMore: () => void loadMore(),
  }
}

// --- Mutations ---------------------------------------------------------------------

function useInvalidateGroup(id: number | null) {
  const qc = useQueryClient()
  return () => {
    if (id !== null) invalidateGroup(id)
    void qc.invalidateQueries({ queryKey: keys.overview })
  }
}

export function useCreateGroup() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { name: string; currency: string }) => api.groupsApi.create(body.name, body.currency),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.groups }),
  })
}

export function useRenameGroup(groupId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (name: string) => api.groupsApi.rename(groupId, name),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.group(groupId) })
      void qc.invalidateQueries({ queryKey: keys.groups })
    },
  })
}

export function useCloseGroup(groupId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.groupsApi.close(groupId),
    onSuccess: () => {
      invalidateGroup(groupId)
      void qc.invalidateQueries({ queryKey: keys.overview })
    },
  })
}

export function useReopenGroup(groupId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.groupsApi.reopen(groupId),
    onSuccess: () => {
      invalidateGroup(groupId)
      void qc.invalidateQueries({ queryKey: keys.overview })
    },
  })
}

export function useRemoveMember(groupId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (userId: number) => api.groupsApi.removeMember(groupId, userId),
    onSuccess: () => {
      invalidateGroup(groupId)
      void qc.invalidateQueries({ queryKey: keys.groups })
    },
  })
}

export function useLeaveGroup(groupId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.groupsApi.leave(groupId),
    onSuccess: () => {
      invalidateGroup(groupId)
      void qc.invalidateQueries({ queryKey: keys.groups })
    },
  })
}

export function useCreateInvite(groupId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { kind: 'link' | 'email'; email_to?: string }) =>
      api.groupsApi.createInvite(groupId, body.kind, body.email_to),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.invites(groupId) }),
  })
}

export function useRevokeInvite(groupId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (inviteId: number) => api.groupsApi.revokeInvite(inviteId),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.invites(groupId) }),
  })
}

export function useCreateExpense(groupId: number) {
  const invalidate = useInvalidateGroup(groupId)
  return useMutation({
    mutationFn: (body: ExpenseCreateBody) => api.expensesApi.create(groupId, body),
    onSuccess: invalidate,
  })
}

export function useUpdateExpense(groupId: number | null) {
  const invalidate = useInvalidateGroup(groupId)
  return useMutation({
    mutationFn: (body: { id: number; data: ExpenseUpdateBody }) =>
      api.expensesApi.update(body.id, body.data),
    onSuccess: invalidate,
  })
}

export function useDeleteExpense(groupId: number | null) {
  const invalidate = useInvalidateGroup(groupId)
  return useMutation({
    mutationFn: (id: number) => api.expensesApi.remove(id),
    onSuccess: invalidate,
  })
}

export function useUploadReceipt(groupId: number | null, expenseId: number) {
  const invalidate = useInvalidateGroup(groupId)
  return useMutation({
    mutationFn: (file: File) => api.expensesApi.uploadReceipt(expenseId, file),
    onSuccess: invalidate,
  })
}

export function useDeleteReceipt(groupId: number | null, expenseId: number) {
  const invalidate = useInvalidateGroup(groupId)
  return useMutation({
    mutationFn: () => api.expensesApi.deleteReceipt(expenseId),
    onSuccess: invalidate,
  })
}

/**
 * Upload a receipt to an expense whose id is only known at call time (the
 * "add expense" flow: the expense is created first, then the picked file is
 * attached to it). Unlike {@link useUploadReceipt}, the expense id is part of
 * the mutation variable rather than the hook argument.
 */
export function useUploadReceiptTo(groupId: number | null) {
  const invalidate = useInvalidateGroup(groupId)
  return useMutation({
    mutationFn: (vars: { expenseId: number; file: File }) => api.expensesApi.uploadReceipt(vars.expenseId, vars.file),
    onSuccess: invalidate,
  })
}

export function useProposeSettlement(groupId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { to_user_id: number; amount: string }) =>
      api.settlementsApi.propose(groupId, body.to_user_id, body.amount),
    onSuccess: () => {
      invalidateGroup(groupId)
      void qc.invalidateQueries({ queryKey: keys.overview })
    },
  })
}

export function useConfirmSettlement(groupId: number | null) {
  const qc = useQueryClient()
  const invalidate = useInvalidateGroup(groupId)
  return useMutation({
    mutationFn: (id: number) => api.settlementsApi.confirm(id),
    onSuccess: () => {
      invalidate()
      void qc.invalidateQueries({ queryKey: keys.overview })
    },
  })
}

export function useRejectSettlement(groupId: number | null) {
  const invalidate = useInvalidateGroup(groupId)
  return useMutation({
    mutationFn: (id: number) => api.settlementsApi.reject(id),
    onSuccess: invalidate,
  })
}

export function useCancelSettlement(groupId: number | null) {
  const invalidate = useInvalidateGroup(groupId)
  return useMutation({
    mutationFn: (id: number) => api.settlementsApi.cancel(id),
    onSuccess: invalidate,
  })
}

export function useMarkNotificationsRead() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (ids?: number[]) => api.notificationsApi.markRead(ids),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.notifications }),
  })
}

export function usePatchMe() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: MePatchBody) => api.meApi.patch(body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.me }),
  })
}

export function useChangeEmail() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (new_email: string) => api.meApi.changeEmail(new_email),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.me }),
  })
}

export function useChangePassword() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (new_password: string) => api.meApi.changePassword(new_password),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.me }),
  })
}

export function useSetAvatar() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => api.meApi.setAvatar(file),
    onSuccess: () => {
      // The avatar URL string is unchanged by the new content, so bust the
      // cached object URL for it before the fresh `me` flows in; every
      // mounted <Avatar> of the current user then swaps to the new image
      // in place (no tab switch, no reload).
      invalidateAvatar(qc.getQueryData<UserOut>(keys.me)?.avatar_url ?? null)
      void qc.invalidateQueries({ queryKey: keys.me })
      invalidateAvatarCarriers(qc)
    },
  })
}

export function useClearAvatar() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.meApi.clearAvatar(),
    onSuccess: () => {
      invalidateAvatar(qc.getQueryData<UserOut>(keys.me)?.avatar_url ?? null)
      void qc.invalidateQueries({ queryKey: keys.me })
      invalidateAvatarCarriers(qc)
    },
  })
}

/** Apply a confirmation token; refresh /me so email_verified flips. */
export function useConfirmEmail() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (token: string) => api.authApi.confirmEmail(token),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.me }),
  })
}

/** Apply a "this wasn't me" revert token; refresh /me so the email is current. */
export function useRevertEmail() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (token: string) => api.authApi.revertEmail(token),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.me }),
  })
}

export function useResendConfirmation() {
  return useMutation({ mutationFn: () => api.authApi.resendConfirmation() })
}

export function useAdminCreateUser() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: AdminUserCreateBody) => api.adminApi.createUser(body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.adminUsers }),
  })
}

export function useAdminPatchUser() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (vars: { id: number; body: AdminUserPatchBody }) => api.adminApi.patchUser(vars.id, vars.body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.adminUsers })
      void qc.invalidateQueries({ queryKey: keys.adminStats })
      // The admin may be editing their own account.
      void qc.invalidateQueries({ queryKey: keys.me })
    },
  })
}

export { api }
