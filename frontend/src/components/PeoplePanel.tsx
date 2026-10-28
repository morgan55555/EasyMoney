/**
 * People panel (F3): the group's members with join dates, an owner badge,
 * the owner's ability to remove a member, and a non-owner's ability to leave.
 * The owner cannot leave (the server returns 409) — they must transfer or
 * close the group instead.
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { ApiError } from '../api/client'
import { useI18n } from '../i18n'
import { formatDate } from '../lib/dates'
import { matchesAny } from '../lib/search'
import { useLeaveGroup, useMembers, useMe, useRemoveMember } from '../query/queries'
import type { MemberOut } from '../types'
import { Badge, Button, Card, CardHeader, EmptyState, ErrorText, IconUserMinus, IconButton, Modal, SearchInput, Spinner } from './ui'

export function PeoplePanel({ groupId }: { groupId: number }) {
  const members = useMembers(groupId)
  const me = useMe()
  const navigate = useNavigate()
  const remove = useRemoveMember(groupId)
  const leave = useLeaveGroup(groupId)
  const { t, locale } = useI18n()

  const [removing, setRemoving] = useState<MemberOut | null>(null)
  const [confirmLeave, setConfirmLeave] = useState(false)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')

  if (members.isPending) return <Spinner label={t('people.loading')} />

  const meId = me.data?.id ?? -1
  const iAmOwner = members.data?.some((m) => m.id === meId && m.is_owner) ?? false
  // Filter by display name and email.
  const visible = (members.data ?? []).filter((m) => matchesAny(query, m.display_name, m.email))

  const onLeave = async () => {
    setError('')
    try {
      await leave.mutateAsync()
      navigate('/', { replace: true })
    } catch (e) {
      setError(e instanceof ApiError ? e.message : t('people.leaveError'))
    }
  }

  return (
    <div className="space-y-4">
      {!iAmOwner ? (
        <div className="flex justify-end">
          <Button variant="danger" onClick={() => setConfirmLeave(true)}>
            {t('people.leave')}
          </Button>
        </div>
      ) : null}
      <ErrorText>{error}</ErrorText>

      <Card>
        <CardHeader title={t('people.members', { n: members.data?.length ?? 0 })} />
        {(members.data ?? []).length > 0 ? (
          <div className="px-4 pt-3">
            <SearchInput
              value={query}
              onChange={setQuery}
              placeholder={t('people.searchPlaceholder')}
              clearLabel={t('common.clearSearch')}
            />
          </div>
        ) : null}
        <ul className="divide-y divide-gray-100">
          {visible.map((m) => (
            <li key={m.id} className="flex items-center justify-between gap-3 px-4 py-3">
              <div className="min-w-0">
                <p className="text-sm text-gray-800 flex items-center gap-2">
                  <span className="truncate">{m.display_name}</span>
                  {m.id === meId ? <span className="text-xs text-gray-400">({t('common.you')})</span> : null}
                  {m.is_owner ? <Badge tone="indigo">{t('people.owner')}</Badge> : null}
                </p>
                <p className="text-xs text-gray-500 mt-0.5">
                  {m.email} · {t('people.joined', { date: formatDate(m.joined_at, locale) })}
                </p>
              </div>
              {iAmOwner && m.id !== meId ? (
                <IconButton title={t('people.remove')} variant="danger" onClick={() => setRemoving(m)}>
                  <IconUserMinus />
                </IconButton>
              ) : null}
            </li>
          ))}
        </ul>
        {visible.length === 0 ? <EmptyState title={t('people.noResults')} /> : null}
      </Card>

      {removing ? (
        <Modal title={t('people.removeModalTitle')} onClose={() => setRemoving(null)}>
          <p className="text-sm text-gray-700">
            {t('people.removeModalBody', { name: removing.display_name })}
          </p>
          <div className="flex justify-end gap-2 mt-4">
            <Button variant="secondary" onClick={() => setRemoving(null)}>
              {t('common.cancel')}
            </Button>
            <Button
              variant="danger"
              busy={remove.isPending}
              onClick={() =>
                void remove
                  .mutateAsync(removing.id)
                  .then(() => setRemoving(null))
                  .catch((e) => setError(e instanceof ApiError ? e.message : t('people.removeError')))
              }
            >
              {t('people.remove')}
            </Button>
          </div>
        </Modal>
      ) : null}

      {confirmLeave ? (
        <Modal title={t('people.leaveModalTitle')} onClose={() => setConfirmLeave(false)}>
          <p className="text-sm text-gray-700">{t('people.leaveModalBody')}</p>
          <div className="flex justify-end gap-2 mt-4">
            <Button variant="secondary" onClick={() => setConfirmLeave(false)}>
              {t('common.cancel')}
            </Button>
            <Button
              variant="danger"
              busy={leave.isPending}
              onClick={() => void onLeave()}
            >
              {t('people.leaveConfirm')}
            </Button>
          </div>
        </Modal>
      ) : null}
    </div>
  )
}
