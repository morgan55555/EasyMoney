/**
 * Group Settings tab (owner only — AGENTS.md §2.7, §2.9).
 *
 * Renames the group, edits the per-group reminder thresholds, transfers
 * ownership to another current member, and hosts the close/reopen lifecycle
 * actions that used to sit in the page header. Every field saves independently
 * through a "Save" button on its right (the same pattern as the user Settings
 * modal).
 *
 * The two reminder fields are never empty: they always show a concrete number —
 * the group's own override when set, otherwise the app-wide default — and saving
 * stores that number on the group. The interval can never be set below the
 * `REMINDER_INTERVAL_DAYS_MIN` floor.
 */

import { useState } from 'react'

import { ApiError } from '../api/client'
import { useI18n } from '../i18n'
import {
  useCloseGroup,
  useGroup,
  useMembers,
  useRenameGroup,
  useReopenGroup,
  useTransferOwner,
  useUpdateGroupSettings,
} from '../query/queries'
import { Avatar } from './Avatar'
import { Button, Card, CardHeader, ErrorText, Field, Modal, Select, TextInput } from './ui'

const MAX_DAYS = 3650

/** Parse a days input: empty or out-of-range = undefined (invalid), else the int. */
function parseDays(value: string, min: number, max: number): number | undefined {
  const trimmed = value.trim()
  if (trimmed === '') return undefined
  const n = Number(trimmed)
  if (!Number.isInteger(n) || n < min || n > max) return undefined
  return n
}

export function GroupSettingsPanel({ groupId }: { groupId: number }) {
  const group = useGroup(groupId)
  const members = useMembers(groupId)
  const rename = useRenameGroup(groupId)
  const settings = useUpdateGroupSettings(groupId)
  const transfer = useTransferOwner(groupId)
  const close = useCloseGroup(groupId)
  const reopen = useReopenGroup(groupId)
  const { t } = useI18n()

  // null draft = "show the stored value"; a typed value overrides it.
  const [nameDraft, setNameDraft] = useState<string | null>(null)
  const [overdueDraft, setOverdueDraft] = useState<string | null>(null)
  const [intervalDraft, setIntervalDraft] = useState<string | null>(null)
  const [target, setTarget] = useState<number | null>(null)
  const [confirmTransfer, setConfirmTransfer] = useState(false)
  const [confirmClose, setConfirmClose] = useState(false)
  const [confirmReopen, setConfirmReopen] = useState(false)
  const [error, setError] = useState('')

  if (group.isPending || group.isError) return null
  const g = group.data!

  const closed = g.status === 'closed'
  const others = (members.data ?? []).filter((m) => !m.is_owner)
  const targetName = (members.data ?? []).find((m) => m.id === target)?.display_name ?? ''

  // The reminder fields are always concrete: the group's override when set,
  // otherwise the app-wide default — so they never render empty.
  const storedOverdue = String(g.debt_overdue_days ?? g.debt_overdue_days_default)
  const storedInterval = String(g.reminder_interval_days ?? g.reminder_interval_days_default)
  const nameShown = nameDraft ?? g.name
  const overdueShown = overdueDraft ?? storedOverdue
  const intervalShown = intervalDraft ?? storedInterval

  const overdueValue = parseDays(overdueShown, 0, MAX_DAYS)
  const intervalValue = parseDays(intervalShown, g.reminder_interval_days_min, MAX_DAYS)
  const overdueValid = overdueValue !== undefined
  const intervalValid = intervalValue !== undefined

  const saveName = async () => {
    const n = nameShown.trim()
    if (!n) return
    setError('')
    try {
      await rename.mutateAsync(n)
      setNameDraft(null)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '')
    }
  }

  // Both fields are sent together (the settings endpoint takes the full state);
  // each Save button persists the current values of both.
  const saveSettings = async () => {
    if (overdueValue === undefined || intervalValue === undefined) return
    setError('')
    try {
      await settings.mutateAsync({
        debt_overdue_days: overdueValue,
        reminder_interval_days: intervalValue,
      })
      setOverdueDraft(null)
      setIntervalDraft(null)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '')
    }
  }

  const doTransfer = async () => {
    if (target === null) return
    setError('')
    try {
      await transfer.mutateAsync(target)
      setConfirmTransfer(false)
      setTarget(null)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '')
    }
  }

  const doClose = async () => {
    setError('')
    try {
      await close.mutateAsync()
      setConfirmClose(false)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : t('group.closeError'))
    }
  }

  const doReopen = async () => {
    setError('')
    try {
      await reopen.mutateAsync()
      setConfirmReopen(false)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : t('group.reopenError'))
    }
  }

  return (
    <div className="space-y-4">
      <ErrorText>{error}</ErrorText>

      {/* Rename. */}
      <Card>
        <CardHeader title={t('settings.nameLabel')} />
        <div className="px-4 py-3">
          <Field
            label={t('settings.nameLabel')}
            trailing={
              <Button onClick={() => void saveName()} busy={rename.isPending} disabled={!nameShown.trim()}>
                {t('settings.save')}
              </Button>
            }
          >
            <TextInput
              value={nameShown}
              onChange={(e) => setNameDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void saveName()
              }}
            />
          </Field>
        </div>
      </Card>

      {/* Per-group reminder thresholds. */}
      <Card>
        <CardHeader title={t('settings.remindersTitle')} />
        <div className="px-4 py-3 space-y-4">
          <Field
            label={t('settings.overdueLabel')}
            hint={t('settings.overdueHint', { days: g.debt_overdue_days_default })}
            trailing={
              <Button
                onClick={() => void saveSettings()}
                busy={settings.isPending}
                disabled={!overdueValid}
              >
                {t('settings.save')}
              </Button>
            }
          >
            <TextInput
              type="number"
              min={0}
              max={MAX_DAYS}
              value={overdueShown}
              onChange={(e) => setOverdueDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void saveSettings()
              }}
            />
          </Field>
          <Field
            label={t('settings.intervalLabel')}
            hint={t('settings.intervalHint', {
              days: g.reminder_interval_days_default,
              min: g.reminder_interval_days_min,
            })}
            trailing={
              <Button
                onClick={() => void saveSettings()}
                busy={settings.isPending}
                disabled={!intervalValid}
              >
                {t('settings.save')}
              </Button>
            }
          >
            <TextInput
              type="number"
              min={g.reminder_interval_days_min}
              max={MAX_DAYS}
              value={intervalShown}
              onChange={(e) => setIntervalDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void saveSettings()
              }}
            />
          </Field>
        </div>
      </Card>

      {/* Owner transfer. */}
      <Card>
        <CardHeader title={t('settings.ownerTitle')} />
        <div className="px-4 py-3 space-y-2">
          <Field label={t('settings.ownerTitle')} hint={t('settings.ownerHint')}>
            <Select
              value={target === null ? '' : String(target)}
              onChange={(e) => setTarget(e.target.value === '' ? null : Number(e.target.value))}
            >
              <option value="">{t('settings.ownerSelect')}</option>
              {others.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.display_name}
                </option>
              ))}
            </Select>
          </Field>
          {target !== null ? (
            <div className="flex items-center justify-between gap-2 pt-1">
              <div className="flex items-center gap-2 min-w-0">
                <Avatar
                  url={(members.data ?? []).find((m) => m.id === target)?.avatar_url ?? null}
                  name={targetName}
                  size={28}
                />
                <span className="truncate text-sm text-gray-700 dark:text-gray-300">{targetName}</span>
              </div>
              <Button onClick={() => setConfirmTransfer(true)}>{t('settings.transfer')}</Button>
            </div>
          ) : null}
        </div>
      </Card>

      {/* Close / reopen. */}
      <Card>
        <CardHeader title={t('settings.lifespanTitle')} />
        <div className="px-4 py-3 flex justify-end">
          {closed ? (
            <Button onClick={() => setConfirmReopen(true)}>{t('group.reopen')}</Button>
          ) : (
            <Button variant="danger" onClick={() => setConfirmClose(true)}>
              {t('group.close')}
            </Button>
          )}
        </div>
      </Card>

      {confirmTransfer ? (
        <Modal title={t('settings.transferModalTitle')} onClose={() => setConfirmTransfer(false)}>
          <p className="text-sm text-gray-700 dark:text-gray-300">
            {t('settings.transferModalBody', { name: targetName })}
          </p>
          <div className="flex justify-end gap-2 mt-4">
            <Button variant="secondary" onClick={() => setConfirmTransfer(false)}>
              {t('common.cancel')}
            </Button>
            <Button busy={transfer.isPending} onClick={() => void doTransfer()}>
              {t('settings.transfer')}
            </Button>
          </div>
        </Modal>
      ) : null}

      {confirmClose ? (
        <Modal title={t('group.closeModalTitle')} onClose={() => setConfirmClose(false)}>
          <p className="text-sm text-gray-700 dark:text-gray-300">
            {t('group.closeModalQ', { name: g.name })} {t('group.closeModalBody')}
          </p>
          <div className="flex justify-end gap-2 mt-4">
            <Button variant="secondary" onClick={() => setConfirmClose(false)}>
              {t('common.cancel')}
            </Button>
            <Button variant="danger" busy={close.isPending} onClick={() => void doClose()}>
              {t('group.close')}
            </Button>
          </div>
        </Modal>
      ) : null}

      {confirmReopen ? (
        <Modal title={t('group.reopenModalTitle')} onClose={() => setConfirmReopen(false)}>
          <p className="text-sm text-gray-700 dark:text-gray-300">
            {t('group.reopenModalQ', { name: g.name })} {t('group.reopenModalBody')}
          </p>
          <div className="flex justify-end gap-2 mt-4">
            <Button variant="secondary" onClick={() => setConfirmReopen(false)}>
              {t('common.cancel')}
            </Button>
            <Button busy={reopen.isPending} onClick={() => void doReopen()}>
              {t('group.reopen')}
            </Button>
          </div>
        </Modal>
      ) : null}
    </div>
  )
}
