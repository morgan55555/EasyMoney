/**
 * Expense list (F5): newest first, per-expense participants with their exact
 * shares, receipt upload, edit/delete for the creator or the group owner.
 * Only active expenses are listed — the backend hides soft-deleted rows.
 */

import { useEffect, useState } from 'react'

import { useI18n, type TFunc } from '../i18n'
import { formatDate } from '../lib/dates'
import { formatMoney } from '../lib/money'
import { matchesAny } from '../lib/search'
import { useDeleteExpense, useExpenses, useGroup, useMembers, useMe } from '../query/queries'
import type { ExpenseOut } from '../types'
import { Avatar } from './Avatar'
import { Button, Card, EmptyState, IconEdit, IconButton, IconTrash, Modal, SearchInput, Spinner } from './ui'
import { ExpenseForm } from './ExpenseForm'
import { ReceiptUpload } from './ReceiptUpload'

// "Show more" pagination (UI-F1): render the first N, the button loads N more —
// a 1000-expense group keeps a bounded DOM, same pattern as the feed.
const PAGE_SIZE = 50

/**
 * Render a category label without leaking a raw i18n key. An unknown category
 * value has no `category.<value>` key, so t() returns the key string itself;
 * in that case show the raw value (capitalized) instead.
 */
function categoryLabel(t: TFunc, category: string): string {
  const key = 'category.' + category
  const translated = t(key)
  if (translated === key) return category.charAt(0).toUpperCase() + category.slice(1)
  return translated
}

export function ExpenseList({ groupId }: { groupId: number }) {
  const expenses = useExpenses(groupId)
  const members = useMembers(groupId)
  const group = useGroup(groupId)
  const me = useMe()
  const del = useDeleteExpense(groupId)
  const { t, locale } = useI18n()
  const [showForm, setShowForm] = useState(false)
  const [editing, setEditing] = useState<ExpenseOut | null>(null)
  const [deleting, setDeleting] = useState<ExpenseOut | null>(null)
  const [visible, setVisible] = useState(PAGE_SIZE)
  const [query, setQuery] = useState('')

  // A new filter starts from the first page.
  useEffect(() => setVisible(PAGE_SIZE), [query])

  const currency = group.data?.currency ?? 'EUR'
  if (expenses.isPending) return <Spinner label={t('expenses.loading')} />

  const list = expenses.data ?? []
  // Filter by description, comment, payer, participants and (translated) category.
  const filtered = list.filter((e) =>
    matchesAny(
      query,
      e.description,
      e.comment,
      e.payer,
      categoryLabel(t, e.category),
      ...e.participants.map((p) => p.display_name),
    ),
  )
  const closed = group.data?.status === 'closed'
  const canManage = (e: ExpenseOut) =>
    (me.data?.id ?? -1) === e.created_by || (me.data?.id ?? -1) === (group.data?.created_by ?? -1)

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-800 dark:text-gray-100">{t('expenses.title')}</h2>
        <Button
          onClick={() => {
            setEditing(null)
            setShowForm(true)
          }}
          disabled={closed}
          title={closed ? t('expenses.addDisabledTitle') : undefined}
        >
          {t('expenses.add')}
        </Button>
      </div>

      {closed ? (
        <p className="text-sm text-amber-800 dark:text-amber-300 bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-900 rounded-md px-3 py-2">
          {t('expenses.closedNote')}
        </p>
      ) : null}

      {list.length === 0 ? (
        <Card>
          <EmptyState title={t('expenses.emptyTitle')} hint={t('expenses.emptyHint')} />
        </Card>
      ) : (
        <div className="space-y-3">
          <SearchInput
            value={query}
            onChange={setQuery}
            placeholder={t('expenses.searchPlaceholder')}
            clearLabel={t('common.clearSearch')}
          />
          {filtered.length === 0 ? (
            <Card>
              <EmptyState title={t('expenses.noResults')} />
            </Card>
          ) : (
            <>
          {filtered.slice(0, visible).map((e) => (
            <Card key={e.id}>
              <div className="p-4 space-y-2">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <h3 className="font-medium text-gray-900 dark:text-gray-50 truncate">{e.description}</h3>
                      <span className="text-xs bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 rounded px-1.5 py-0.5">
                        {categoryLabel(t, e.category)}
                      </span>
                    </div>
                    <div className="flex items-center gap-1.5 mt-0.5">
                      <Avatar url={e.payer_avatar_url} name={e.payer} size={16} />
                      <span className="text-xs text-gray-500 dark:text-gray-400">
                        {t('expenses.line', {
                          payer: e.payer,
                          amount: formatMoney(e.amount, currency, locale),
                          date: formatDate(e.date, locale),
                          split: t('splitLine.' + e.split_method),
                        })}
                      </span>
                    </div>
                    {e.comment ? <p className="text-sm text-gray-600 dark:text-gray-300 mt-1">{e.comment}</p> : null}
                  </div>
                  <div className="text-right shrink-0">
                    <span className="text-lg font-semibold text-gray-900 dark:text-gray-50 tabular-nums">
                      {formatMoney(e.amount, currency, locale)}
                    </span>
                  </div>
                </div>

                <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-500 dark:text-gray-400">
                  {e.participants.map((p) => (
                    <span key={p.user_id} className="flex items-center gap-1.5">
                      <Avatar url={p.avatar_url} name={p.display_name} size={16} />
                      <span>
                        {p.display_name}: <span className="tabular-nums">{formatMoney(p.share, currency, locale)}</span>
                      </span>
                    </span>
                  ))}
                </div>

                {/* items-start: the receipt block also reserves a 20px error slot below
                    its buttons, so centering would sink the Edit/Delete icons a half-row.
                    Top-aligning keeps every action on one visual row (no reflow on error). */}
                <div className="flex flex-wrap items-start justify-between gap-2 pt-1 border-t border-gray-100 dark:border-gray-800">
                  <ReceiptUpload expense={e} groupId={groupId} canManage={canManage(e)} />
                  <div className="flex gap-2">
                    <IconButton
                      title={t('expenses.edit')}
                      onClick={() => {
                        setEditing(e)
                        setShowForm(true)
                      }}
                      disabled={!canManage(e)}
                    >
                      <IconEdit />
                    </IconButton>
                    <IconButton
                      title={t('expenses.delete')}
                      variant="danger"
                      onClick={() => setDeleting(e)}
                      disabled={!canManage(e)}
                    >
                      <IconTrash />
                    </IconButton>
                  </div>
                </div>
              </div>
            </Card>
          ))}
          {visible < filtered.length ? (
            <div className="text-center pt-1">
              <Button variant="ghost" onClick={() => setVisible((v) => v + PAGE_SIZE)}>
                {t('expenses.showMore', { n: Math.min(PAGE_SIZE, filtered.length - visible) })}
              </Button>
            </div>
          ) : null}
            </>
          )}
        </div>
      )}

      {showForm && members.data ? (
        <Modal
          title={
            editing
              ? t('expenses.modalEdit', { id: editing.id })
              : t('expenses.modalNew')
          }
          onClose={() => setShowForm(false)}
          wide
        >
          <ExpenseForm
            groupId={groupId}
            members={members.data}
            currency={group.data?.currency ?? 'EUR'}
            initial={editing ?? undefined}
            onDone={() => setShowForm(false)}
          />
        </Modal>
      ) : null}

      {deleting ? (
        <Modal title={t('expenses.deleteModalTitle')} onClose={() => setDeleting(null)}>
          <p className="text-sm text-gray-700 dark:text-gray-300">
            {t('expenses.deleteModalBody', {
              desc: deleting.description,
              amount: formatMoney(deleting.amount, currency, locale),
            })}
          </p>
          <div className="flex justify-end gap-2 mt-4">
            <Button variant="secondary" onClick={() => setDeleting(null)}>
              {t('common.cancel')}
            </Button>
            <Button
              variant="danger"
              busy={del.isPending}
              onClick={() =>
                void del.mutateAsync(deleting.id).then(() => setDeleting(null)).catch(() => setDeleting(null))
              }
            >
              {t('expenses.delete')}
            </Button>
          </div>
        </Modal>
      ) : null}
    </div>
  )
}
