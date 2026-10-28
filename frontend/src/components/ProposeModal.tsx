/**
 * Settlement proposal dialog (F6, AGENTS.md §2.4).
 *
 * The caller (the authenticated user) proposes paying `amount` to a recipient
 * they currently owe. The recipient list is the caller's debts from the current
 * pairwise view; the amount is capped at the debt to that recipient. Opened from
 * the Balances "settle" affordance or the Payments "propose new" button.
 */

import { useMemo, useState } from 'react'

import { ApiError } from '../api/client'
import { useI18n } from '../i18n'
import { formatMoney } from '../lib/money'
import { useProposeSettlement } from '../query/queries'
import type { GroupBalancesOut } from '../types'
import { Button, ErrorText, Field, Modal, Select, TextInput } from './ui'

interface Props {
  groupId: number
  meId: number
  balances: GroupBalancesOut
  /** Pre-filled recipient (a suggested transfer). */
  initialTo?: number
  /** Pre-filled amount (the suggested transfer amount). */
  initialAmount?: string
  onClose: () => void
}

export function ProposeModal({ groupId, meId, balances, initialTo, initialAmount, onClose }: Props) {
  const propose = useProposeSettlement(groupId)
  const { t, locale } = useI18n()
  const [toUserId, setToUserId] = useState<number>(initialTo ?? 0)
  const [amount, setAmount] = useState<string>(initialAmount ?? '')
  const [error, setError] = useState('')

  const nameById = useMemo(
    () => new Map(balances.balances.map((b) => [b.user_id, b.display_name])),
    [balances],
  )

  // People the caller owes in the current pairwise view (§2.4: only a debtor
  // can propose to a creditor).
  const creditors = useMemo(
    () => balances.pairs.filter((p) => p.from_user_id === meId),
    [balances, meId],
  )

  const maxFor = (userId: number): string | null => {
    const pair = creditors.find((p) => p.to_user_id === userId)
    return pair?.amount ?? null
  }

  const submit = async () => {
    setError('')
    const amt = amount.trim()
    if (toUserId === 0) {
      setError(t('propose.errPick'))
      return
    }
    if (amt === '' || Number.isNaN(Number(amt)) || Number(amt) <= 0) {
      setError(t('propose.errAmount'))
      return
    }
    const max = maxFor(toUserId)
    if (max !== null && Number(amt) > Number(max)) {
      setError(
        t('propose.errTooLarge', {
          amount: formatMoney(max, balances.currency, locale),
          name: nameById.get(toUserId) ?? t('common.userById', { id: toUserId }),
        }),
      )
      return
    }
    try {
      await propose.mutateAsync({ to_user_id: toUserId, amount: amt })
      onClose()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : t('propose.sendError'))
    }
  }

  return (
    <Modal title={t('propose.title')} onClose={onClose}>
      <div className="space-y-4">
        <p className="text-sm text-gray-600 dark:text-gray-300">
          {t('propose.body1')}
          <span className="font-medium">{t('propose.pendingWord')}</span>
          {t('propose.body2')}
        </p>

        {creditors.length === 0 ? (
          <p className="text-sm text-emerald-700 dark:text-emerald-300 bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-900 rounded-md px-3 py-2">
            {t('propose.oweNoOne')}
          </p>
        ) : (
          <>
            <Field label={t('propose.payTo')}>
              <Select
                value={toUserId}
                onChange={(e) => {
                  setToUserId(Number(e.target.value))
                  // Re-seed the amount to the full debt when the recipient changes.
                  const next = maxFor(Number(e.target.value))
                  if (next !== null) setAmount(next)
                }}
              >
                <option value={0}>{t('propose.choose')}</option>
                {creditors.map((c) => (
                  <option key={c.to_user_id} value={c.to_user_id}>
                    {nameById.get(c.to_user_id) ?? t('common.userById', { id: c.to_user_id })}
                  </option>
                ))}
              </Select>
            </Field>
            <Field
              label={t('propose.amount', { currency: balances.currency })}
              hint={
                maxFor(toUserId)
                  ? t('propose.hint', { amount: formatMoney(maxFor(toUserId)!, balances.currency, locale) })
                  : undefined
              }
            >
              <TextInput
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                placeholder="0.00"
                inputMode="decimal"
              />
            </Field>
          </>
        )}

        <ErrorText>{error}</ErrorText>

        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            onClick={() => void submit()}
            busy={propose.isPending}
            disabled={creditors.length === 0 || toUserId === 0}
          >
            {t('propose.send')}
          </Button>
        </div>
      </div>
    </Modal>
  )
}
