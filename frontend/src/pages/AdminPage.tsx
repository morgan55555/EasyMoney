/**
 * Admin panel (AGENTS.md §2.13). Admins manage users directly (create / edit
 * name, email, password, and the admin bit — no email confirmation) and read
 * platform statistics. Two tabs: Users (searchable table + create / edit
 * modals) and Statistics (a v1 summary: user / group / expense / settlement
 * counts, money by currency).
 */

import { useState } from 'react'

import { ApiError } from '../api/client'
import { useI18n } from '../i18n'
import { formatDate } from '../lib/dates'
import { formatMoney } from '../lib/money'
import { matchesAny } from '../lib/search'
import {
  useAdminCreateUser,
  useAdminPatchUser,
  useAdminStats,
  useAdminUsers,
} from '../query/queries'
import type { AdminStatsOut, AdminUserOut, AdminUserPatchBody } from '../types'
import { Avatar } from '../components/Avatar'
import { Badge, Button, Card, EmptyState, ErrorText, Field, Modal, SearchInput, Spinner, TextInput } from '../components/ui'

export function AdminPage() {
  const { t } = useI18n()
  const [tab, setTab] = useState<'users' | 'stats'>('users')

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-gray-900 dark:text-gray-50">{t('admin.title')}</h1>
      <div className="inline-flex rounded-md border border-gray-300 bg-white p-0.5 dark:border-gray-600 dark:bg-gray-800">
        <TabButton active={tab === 'users'} onClick={() => setTab('users')}>
          {t('admin.usersTab')}
        </TabButton>
        <TabButton active={tab === 'stats'} onClick={() => setTab('stats')}>
          {t('admin.statsTab')}
        </TabButton>
      </div>
      {tab === 'users' ? <UsersTab /> : <StatsTab />}
    </div>
  )
}

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`rounded px-3 py-1 text-sm font-medium transition-colors ${
        active
          ? 'bg-indigo-600 text-white'
          : 'text-gray-600 hover:text-gray-900 dark:text-gray-300 dark:hover:text-white'
      }`}
    >
      {children}
    </button>
  )
}

// --- Users tab -----------------------------------------------------------------

function UsersTab() {
  const { t } = useI18n()
  const users = useAdminUsers()
  const [query, setQuery] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [editing, setEditing] = useState<AdminUserOut | null>(null)

  if (users.isPending) return <Spinner label={t('common.loading')} />

  const visible = (users.data ?? []).filter((u) => matchesAny(query, u.display_name, u.email))

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <SearchInput
          value={query}
          onChange={setQuery}
          placeholder={t('admin.searchPlaceholder')}
          clearLabel={t('common.clearSearch')}
          className="max-w-xs"
        />
        <Button onClick={() => setCreateOpen(true)}>{t('admin.create')}</Button>
      </div>

      <Card>
        {visible.length === 0 ? (
          <EmptyState title={(users.data ?? []).length === 0 ? t('admin.empty') : t('admin.noResults')} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-gray-400">
                  <th className="px-4 py-2 font-medium">{t('admin.name')}</th>
                  <th className="px-4 py-2 font-medium">{t('admin.email')}</th>
                  <th className="px-4 py-2 font-medium">{t('admin.role')}</th>
                  <th className="px-4 py-2 font-medium">{t('admin.status')}</th>
                  <th className="px-4 py-2 font-medium">{t('admin.joined')}</th>
                  <th className="px-4 py-2" />
                </tr>
              </thead>
              <tbody>
                {visible.map((u) => (
                  <UserRow key={u.id} user={u} onEdit={() => setEditing(u)} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {createOpen ? <CreateUserModal onClose={() => setCreateOpen(false)} /> : null}
      {editing ? <EditUserModal user={editing} onClose={() => setEditing(null)} /> : null}
    </div>
  )
}

function UserRow({ user, onEdit }: { user: AdminUserOut; onEdit: () => void }) {
  const { t, locale } = useI18n()
  return (
    <tr className="border-t border-gray-100 dark:border-gray-800">
      <td className="px-4 py-2.5">
        <div className="flex items-center gap-2">
          <Avatar url={user.avatar_url} name={user.display_name} size={28} />
          <span className="text-gray-800 dark:text-gray-100">{user.display_name}</span>
        </div>
      </td>
      <td className="px-4 py-2.5 text-gray-500 dark:text-gray-400">{user.email}</td>
      <td className="px-4 py-2.5">
        {user.is_admin ? (
          <Badge tone="indigo">{t('admin.roleAdmin')}</Badge>
        ) : (
          <Badge tone="gray">{t('admin.roleUser')}</Badge>
        )}
      </td>
      <td className="px-4 py-2.5">
        {user.email_verified ? (
          <Badge tone="green">{t('admin.verified')}</Badge>
        ) : (
          <Badge tone="amber">{t('admin.unverified')}</Badge>
        )}
      </td>
      <td className="px-4 py-2.5 text-gray-500 dark:text-gray-400 whitespace-nowrap">
        {formatDate(user.created_at, locale)}
      </td>
      <td className="px-4 py-2.5 text-right">
        <Button variant="secondary" onClick={onEdit}>
          {t('admin.edit')}
        </Button>
      </td>
    </tr>
  )
}

function CreateUserModal({ onClose }: { onClose: () => void }) {
  const { t } = useI18n()
  const create = useAdminCreateUser()
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')

  const valid = email.trim().length > 0 && password.length >= 8

  const submit = () => {
    setError('')
    create.mutate(
      { email: email.trim(), password, display_name: name.trim() || undefined },
      {
        onSuccess: () => onClose(),
        onError: (e) => setError(e instanceof ApiError ? e.message : t('admin.createdError')),
      },
    )
  }

  return (
    <Modal title={t('admin.createTitle')} onClose={onClose}>
      <div className="space-y-3">
        <Field label={t('admin.displayName')}>
          <TextInput value={name} onChange={(e) => setName(e.target.value)} maxLength={120} />
        </Field>
        <Field label={t('admin.email')}>
          <TextInput type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label={t('admin.password')} hint={t('admin.passwordHint')}>
          <TextInput type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <ErrorText>{error}</ErrorText>
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button busy={create.isPending} disabled={!valid} onClick={submit}>
            {t('admin.createBtn')}
          </Button>
        </div>
      </div>
    </Modal>
  )
}

function EditUserModal({ user, onClose }: { user: AdminUserOut; onClose: () => void }) {
  const { t } = useI18n()
  const patch = useAdminPatchUser()
  const [name, setName] = useState(user.display_name)
  const [email, setEmail] = useState(user.email)
  const [password, setPassword] = useState('')
  const [isAdmin, setIsAdmin] = useState(user.is_admin)
  const [error, setError] = useState('')

  const submit = () => {
    setError('')
    if (!name.trim() || !email.trim()) return
    const body: AdminUserPatchBody = { display_name: name.trim(), email: email.trim(), is_admin: isAdmin }
    if (password) body.password = password
    patch.mutate(
      { id: user.id, body },
      {
        onSuccess: () => onClose(),
        onError: (e) => setError(e instanceof ApiError ? e.message : t('admin.saveError')),
      },
    )
  }

  return (
    <Modal title={t('admin.editTitle')} onClose={onClose}>
      <div className="space-y-3">
        <Field label={t('admin.displayName')}>
          <TextInput value={name} onChange={(e) => setName(e.target.value)} maxLength={120} />
        </Field>
        <Field label={t('admin.email')}>
          <TextInput type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label={t('admin.password')} hint={t('admin.passwordHint')}>
          <TextInput type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-200">
          <input
            type="checkbox"
            checked={isAdmin}
            onChange={(e) => setIsAdmin(e.target.checked)}
            className="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
          />
          {t('admin.isAdmin')}
        </label>
        <ErrorText>{error}</ErrorText>
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button busy={patch.isPending} disabled={!name.trim() || !email.trim()} onClick={submit}>
            {t('admin.save')}
          </Button>
        </div>
      </div>
    </Modal>
  )
}

// --- Statistics tab --------------------------------------------------------------

function StatsTab() {
  const { t, locale } = useI18n()
  const stats = useAdminStats()

  if (stats.isPending) return <Spinner label={t('common.loading')} />
  const s: AdminStatsOut | undefined = stats.data
  if (!s) return null

  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <StatCard title={t('stats.users')}>
        <StatRow label={t('stats.usersTotal')} value={String(s.users_total)} />
        <StatRow label={t('stats.usersAdmin')} value={String(s.users_admin)} />
        <StatRow label={t('stats.usersUnverified')} value={String(s.users_unverified)} />
      </StatCard>

      <StatCard title={t('stats.groups')}>
        <StatRow label={t('stats.groupsActive')} value={String(s.groups_active)} />
        <StatRow label={t('stats.groupsClosed')} value={String(s.groups_closed)} />
      </StatCard>

      <StatCard title={t('stats.expenses')}>
        <StatRow label={t('stats.expensesTotal')} value={String(s.expenses_total)} />
        <p className="mt-3 text-xs uppercase tracking-wide text-gray-400">{t('stats.expensesByCurrency')}</p>
        {s.expenses_by_currency.length === 0 ? (
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">{t('stats.noData')}</p>
        ) : (
          <ul className="mt-1 space-y-1">
            {s.expenses_by_currency.map((c) => (
              <MoneyRow key={c.currency} currency={c.currency} total={c.total} locale={locale} />
            ))}
          </ul>
        )}
      </StatCard>

      <StatCard title={t('stats.settlements')}>
        <p className="text-xs uppercase tracking-wide text-gray-400">{t('stats.byState')}</p>
        {Object.keys(s.settlements_by_state).length === 0 ? (
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">{t('stats.noData')}</p>
        ) : (
          <ul className="mt-1 space-y-1">
            {Object.entries(s.settlements_by_state).map(([state, count]) => (
              <li key={state} className="flex items-center justify-between text-sm">
                <span className="text-gray-600 dark:text-gray-300">{t('state.' + state)}</span>
                <span className="font-medium tabular-nums text-gray-900 dark:text-gray-100">{count}</span>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-3 text-xs uppercase tracking-wide text-gray-400">{t('stats.confirmedByCurrency')}</p>
        {s.settlements_confirmed_by_currency.length === 0 ? (
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">{t('stats.noData')}</p>
        ) : (
          <ul className="mt-1 space-y-1">
            {s.settlements_confirmed_by_currency.map((c) => (
              <MoneyRow key={c.currency} currency={c.currency} total={c.total} locale={locale} />
            ))}
          </ul>
        )}
      </StatCard>
    </div>
  )
}

function StatCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card className="p-4">
      <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100 mb-3">{title}</h3>
      <div className="space-y-2">{children}</div>
    </Card>
  )
}

function StatRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-gray-600 dark:text-gray-300">{label}</span>
      <span className="font-medium tabular-nums text-gray-900 dark:text-gray-100">{value}</span>
    </div>
  )
}

function MoneyRow({ currency, total, locale }: { currency: string; total: string; locale: string }) {
  return (
    <li className="flex items-center justify-between text-sm">
      <span className="text-gray-600 dark:text-gray-300">{currency}</span>
      <span className="font-medium tabular-nums text-gray-900 dark:text-gray-100">
        {formatMoney(total, currency, locale)}
      </span>
    </li>
  )
}
