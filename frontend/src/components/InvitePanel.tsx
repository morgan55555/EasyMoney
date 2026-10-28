/**
 * Invites panel (F3, AGENTS.md §2.10).
 *
 * Two kinds:
 *  - link: reusable until revoked/expired — anyone authenticated can open
 *    `FRONTEND_URL/invite/{token}` and join;
 *  - email: single-use, bound to the addressed email (the server validates the
 *    match on accept).
 *
 * The invite `url` is copied straight to the clipboard; revoking an active
 * invite leaves already-joined members unaffected.
 */

import { useState } from 'react'

import { ApiError } from '../api/client'
import { useI18n } from '../i18n'
import { formatDateTime } from '../lib/dates'
import { useCreateInvite, useInvites, useRevokeInvite } from '../query/queries'
import type { InviteOut } from '../types'
import {
  Badge,
  Button,
  Card,
  CardHeader,
  ErrorText,
  IconCheck,
  IconCopy,
  IconButton,
  IconTrash,
  TextInput,
} from './ui'

export function InvitePanel({ groupId }: { groupId: number }) {
  const invites = useInvites(groupId)
  const create = useCreateInvite(groupId)
  const revoke = useRevokeInvite(groupId)
  const { t, locale } = useI18n()

  const [email, setEmail] = useState('')
  const [error, setError] = useState('')
  const [copied, setCopied] = useState<number | null>(null)

  const copy = async (inv: InviteOut) => {
    try {
      await navigator.clipboard.writeText(inv.url)
      setCopied(inv.id)
      setTimeout(() => setCopied(null), 1500)
    } catch {
      setError(t('invites.copyError'))
    }
  }

  const createLink = async () => {
    setError('')
    try {
      await create.mutateAsync({ kind: 'link' })
    } catch (e) {
      setError(e instanceof ApiError ? e.message : t('invites.createError'))
    }
  }

  const createEmail = async () => {
    setError('')
    const to = email.trim()
    if (!to) {
      setError(t('invites.emailError'))
      return
    }
    try {
      await create.mutateAsync({ kind: 'email', email_to: to })
      setEmail('')
    } catch (e) {
      setError(e instanceof ApiError ? e.message : t('invites.createError'))
    }
  }

  const active = (invites.data ?? []).filter((i) => i.status === 'active')
  const usedOrRevoked = (invites.data ?? []).filter((i) => i.status !== 'active')

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title={t('invites.title')} />
        <div className="p-4 space-y-4">
          <p className="text-sm text-gray-600">{t('invites.hint')}</p>

          <div className="flex flex-wrap gap-2 items-end">
            <Button onClick={() => void createLink()} busy={create.isPending && create.variables?.kind === 'link'}>
              {t('invites.createLink')}
            </Button>
            <div className="flex-1 min-w-[220px] flex gap-2">
              <TextInput
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="friend@example.com"
                className="flex-1 min-w-0"
              />
              <Button
                variant="secondary"
                onClick={() => void createEmail()}
                busy={create.isPending && create.variables?.kind === 'email'}
              >
                {t('invites.emailInvite')}
              </Button>
            </div>
          </div>
          <ErrorText>{error}</ErrorText>
        </div>
      </Card>

      <Card>
        <CardHeader title={t('invites.active', { n: active.length })} />
        {active.length === 0 ? (
          <p className="px-4 py-3 text-sm text-gray-500">{t('invites.noActive')}</p>
        ) : (
          <ul className="divide-y divide-gray-100">
            {active.map((inv) => (
              <li key={inv.id} className="px-4 py-3 space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="flex items-center gap-2">
                    <Badge tone={inv.kind === 'link' ? 'indigo' : 'amber'}>{t('inviteKind.' + inv.kind)}</Badge>
                    {inv.email_to ? <span className="text-sm text-gray-700">{inv.email_to}</span> : null}
                  </span>
                  <span className="text-xs text-gray-400">
                    {t('invites.expires', { date: formatDateTime(inv.expires_at, locale) })}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <code className="flex-1 text-xs text-gray-600 bg-gray-50 border border-gray-200 rounded px-2 py-2 truncate">
                    {inv.url}
                  </code>
                  <IconButton
                    title={copied === inv.id ? t('invites.copied') : t('invites.copy')}
                    onClick={() => void copy(inv)}
                  >
                    {copied === inv.id ? <IconCheck /> : <IconCopy />}
                  </IconButton>
                  <IconButton
                    title={t('invites.revoke')}
                    variant="danger"
                    onClick={() => void revoke.mutateAsync(inv.id)}
                    busy={revoke.isPending && revoke.variables === inv.id}
                  >
                    <IconTrash />
                  </IconButton>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {usedOrRevoked.length > 0 ? (
        <Card>
          <CardHeader title={t('invites.usedRevoked')} />
          <ul className="divide-y divide-gray-100">
            {usedOrRevoked.map((inv) => (
              <li key={inv.id} className="px-4 py-2.5 flex items-center justify-between gap-2">
                <span className="flex items-center gap-2 min-w-0">
                  <Badge tone="gray">{t('inviteKind.' + inv.kind)}</Badge>
                  {inv.email_to ? (
                    <span className="text-sm text-gray-600 truncate">{inv.email_to}</span>
                  ) : (
                    <code className="text-xs text-gray-500 truncate">{inv.url}</code>
                  )}
                </span>
                <Badge tone={inv.status === 'used' ? 'green' : 'gray'}>{t('inviteStatus.' + inv.status)}</Badge>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
    </div>
  )
}
