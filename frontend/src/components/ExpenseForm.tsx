/**
 * Expense add/edit form (F5, AGENTS.md §2.1, §2.5).
 *
 * The split editor has three modes (equal / weighted / exact) and shows a
 * live preview computed client-side with the server's exact rules; the
 * server always recomputes and is the only writer of stored shares. Server
 * rejections (422 — e.g. an exact split that doesn't sum to the total) are
 * surfaced inline.
 *
 * The preview error strings in lib/split.ts are intentionally English (they
 * are pinned by tests and shared with the server's semantics); they are
 * mapped to i18n keys here at render time.
 */

import { useEffect, useMemo, useRef, useState } from 'react'

import { ApiError } from '../api/client'
import { fetchAttachmentBlob } from '../api/endpoints'
import { Button, ErrorText, Field, IconEye, IconPaperclip, IconTrash, IconUpload, Modal, Select, TextInput, Textarea } from '../components/ui'
import { useI18n, type TFunc } from '../i18n'
import { formatMoney } from '../lib/money'
import { localTodayIso } from '../lib/dates'
import { previewSplit, sumExactShares, type SplitPreviewRow } from '../lib/split'
import { useCreateExpense, useDeleteReceipt, useMe, useUploadReceipt, useUploadReceiptTo, useUpdateExpense } from '../query/queries'
import type { AttachmentOut, ExpenseOut, MemberOut, SplitMethod } from '../types'
import { ACCEPT } from './ReceiptUpload'

const CATEGORIES = ['na', 'food', 'transport', 'housing', 'entertainment', 'shopping', 'health', 'other']

interface Props {
  groupId: number
  members: MemberOut[]
  currency: string
  /** Present when editing. */
  initial?: ExpenseOut
  onDone: () => void
}

/** Server's deterministic participant order: joined_at asc, user_id asc. */
function inRemainderOrder(members: MemberOut[]): MemberOut[] {
  return [...members].sort((a, b) => {
    if (a.joined_at !== b.joined_at) return a.joined_at < b.joined_at ? -1 : 1
    return a.id - b.id
  })
}

/** Map the English preview errors from lib/split.ts onto i18n keys. */
function localizePreviewError(msg: string, t: TFunc): string {
  const exact: Record<string, string> = {
    'Enter an amount.': t('splitErr.enterAmount'),
    'Enter an amount greater than zero.': t('splitErr.positive'),
    'Pick at least one participant.': t('splitErr.pickParticipant'),
    'Weights must be positive numbers.': t('splitErr.positiveWeights'),
  }
  if (msg in exact) return exact[msg]
  const m = msg.match(/^(.+?) has (\d+) decimal place\(s\); the amount has too many\.$/)
  if (m) return t('splitErr.tooManyDecimals', { currency: m[1], digits: m[2] })
  return msg
}

export function ExpenseForm({ groupId, members, currency, initial, onDone }: Props) {
  const { t, locale } = useI18n()
  const ordered = useMemo(() => inRemainderOrder(members), [members])
  const me = useMe()

  // Default payer: the current user when they're a member, else the earliest joiner.
  const meId = me.data?.id
  const defaultPayer = meId != null && ordered.some((m) => m.id === meId) ? meId : (ordered[0]?.id ?? 0)

  const [description, setDescription] = useState(initial?.description ?? '')
  const [amount, setAmount] = useState(initial?.amount ?? '')
  const [date, setDate] = useState(initial?.date ?? localTodayIso())
  const [category, setCategory] = useState(initial?.category ?? 'na')
  const [comment, setComment] = useState(initial?.comment ?? '')
  const [payerId, setPayerId] = useState<number>(initial?.payer_id ?? defaultPayer)
  const [method, setMethod] = useState<SplitMethod>(initial?.split_method ?? 'equal')
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<Set<number>>(
    () => new Set(initial ? initial.participants.map((p) => p.user_id) : members.map((m) => m.id)),
  )
  const [weights, setWeights] = useState<Record<number, string>>(() => {
    const w: Record<number, string> = {}
    for (const m of members) {
      const p = initial?.participants.find((pp) => pp.user_id === m.id)
      w[m.id] = p?.weight ?? '1'
    }
    return w
  })
  const [exactAmounts, setExactAmounts] = useState<Record<number, string>>(() => {
    const a: Record<number, string> = {}
    for (const m of members) {
      a[m.id] = initial?.participants.find((p) => p.user_id === m.id)?.share ?? ''
    }
    return a
  })
  const [error, setError] = useState('')

  const createExpense = useCreateExpense(groupId)
  const updateExpense = useUpdateExpense(groupId)

  // --- Current receipt (edit mode only) ---------------------------------------------
  const receiptUpload = useUploadReceipt(groupId, initial?.id ?? 0)
  const receiptDelete = useDeleteReceipt(groupId, initial?.id ?? 0)
  const receiptInputRef = useRef<HTMLInputElement>(null)
  const [attachment, setAttachment] = useState<AttachmentOut | null>(initial?.attachment ?? null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [receiptError, setReceiptError] = useState('')
  const [openFull, setOpenFull] = useState(false)
  const [confirmRemoveReceipt, setConfirmRemoveReceipt] = useState(false)

  // Load the receipt as an object URL; revoke the previous one on change/unmount.
  useEffect(() => {
    if (!attachment) {
      setPreviewUrl(null)
      return
    }
    let cancelled = false
    let url: string | null = null
    void (async () => {
      try {
        const blob = await fetchAttachmentBlob(attachment.id)
        if (cancelled) return
        url = URL.createObjectURL(blob)
        setPreviewUrl(url)
      } catch {
        if (!cancelled) setPreviewUrl(null)
      }
    })()
    return () => {
      cancelled = true
      if (url) URL.revokeObjectURL(url)
    }
  }, [attachment])

  // --- Pending receipt (add mode) ---------------------------------------------------
  // The receipt can't be uploaded until the expense exists
  // (POST /expenses/{id}/receipt, §3.5), so in the add flow we keep the picked
  // File locally, preview it from an object URL, and upload it right after the
  // expense is created in submit().
  const uploadToReceipt = useUploadReceiptTo(groupId)
  const pendingInputRef = useRef<HTMLInputElement>(null)
  const [pendingFile, setPendingFile] = useState<File | null>(null)
  const [pendingPreview, setPendingPreview] = useState<string | null>(null)
  // Guard against creating a duplicate expense if a retry re-runs submit()
  // after the receipt upload — not the expense creation — failed.
  const createdIdRef = useRef<number | null>(null)
  const [submitting, setSubmitting] = useState(false)

  // Object URL for the pending file: create on change, revoke on change/unmount.
  useEffect(() => {
    if (!pendingFile) {
      setPendingPreview(null)
      return
    }
    const url = URL.createObjectURL(pendingFile)
    setPendingPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [pendingFile])

  const pickPendingFile = (file: File | undefined) => {
    if (!file) return
    setReceiptError('')
    setPendingFile(file)
    if (pendingInputRef.current) pendingInputRef.current.value = ''
  }

  const clearPendingFile = () => {
    setReceiptError('')
    setPendingFile(null)
  }

  const replaceReceipt = async (file: File | undefined) => {
    if (!file) return
    setReceiptError('')
    try {
      // The mutation returns the freshly-written attachment row; the old one is
      // deleted server-side, so adopt the new row (its id is what we preview).
      setAttachment(await receiptUpload.mutateAsync(file))
      setOpenFull(false)
    } catch (e) {
      setReceiptError(e instanceof ApiError ? e.message : t('receipt.uploadError'))
    } finally {
      if (receiptInputRef.current) receiptInputRef.current.value = ''
    }
  }

  const removeReceipt = async () => {
    setReceiptError('')
    try {
      await receiptDelete.mutateAsync()
      // setAttachment(null) also revokes the preview URL via its effect.
      setAttachment(null)
      setOpenFull(false)
      setConfirmRemoveReceipt(false)
    } catch (e) {
      setReceiptError(e instanceof ApiError ? e.message : t('receipt.removeError'))
    }
  }

  const participantsInOrder = ordered.filter((m) => selected.has(m.id))
  const visibleMembers =
    query.trim() === ''
      ? ordered
      : ordered.filter((m) => m.display_name.toLowerCase().includes(query.trim().toLowerCase()))
  const isPreviewImage = attachment?.content_type.startsWith('image/') ?? false
  const isPendingImage = pendingFile?.type.startsWith('image/') ?? false

  const preview = useMemo(
    () =>
      previewSplit({
        amount,
        currency,
        method,
        participants: participantsInOrder.map((m) => ({
          user_id: m.id,
          weight: weights[m.id],
          amount: exactAmounts[m.id],
        })),
      }),
    [amount, currency, method, participantsInOrder, weights, exactAmounts],
  )

  const exactTotal = method === 'exact' ? sumExactShares(participantsInOrder.map((m) => ({ user_id: m.id, amount: exactAmounts[m.id] })), currency) : null
  const exactBalanced = exactTotal !== null && exactTotal === amount.trim()

  const toggle = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const submit = async () => {
    if (submitting) return
    setError('')
    setReceiptError('')
    const participants = participantsInOrder.map((m) => {
      const base = { user_id: m.id }
      if (method === 'weighted') return { ...base, weight: (weights[m.id] ?? '1').trim() || '1' }
      if (method === 'exact') return { ...base, amount: (exactAmounts[m.id] ?? '0').trim() || '0' }
      return base
    })
    const body = {
      payer_id: payerId,
      description: description.trim(),
      amount: amount.trim(),
      split_method: method,
      category,
      date,
      comment: comment.trim() === '' ? null : comment.trim(),
      participants,
    }
    setSubmitting(true)
    try {
      // Create/update first — the receipt endpoint needs the expense id.
      let expenseId = createdIdRef.current
      if (expenseId === null) {
        if (initial) {
          await updateExpense.mutateAsync({ id: initial.id, data: body })
          expenseId = initial.id
        } else {
          const created = await createExpense.mutateAsync(body)
          createdIdRef.current = created.id
          expenseId = created.id
        }
      }
      // Add mode: attach the picked receipt now that the expense exists.
      if (!initial && pendingFile) {
        await uploadToReceipt.mutateAsync({ expenseId, file: pendingFile })
      }
      onDone()
    } catch (e) {
      // If the expense was already created and only the receipt upload failed,
      // keep the modal open (and remember the id) so a retry re-uploads without
      // creating a second expense.
      if (!initial && pendingFile && createdIdRef.current !== null) {
        setReceiptError(e instanceof ApiError ? e.message : t('receipt.uploadError'))
      } else {
        setError(e instanceof ApiError ? e.message : t('expenses.saveError'))
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <Field label={t('expenseForm.description')}>
          <TextInput
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder={t('expenseForm.descriptionPh')}
            maxLength={200}
          />
        </Field>
        <Field label={t('expenseForm.amount', { currency })}>
          <TextInput value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="0.00" inputMode="decimal" />
        </Field>
        <Field label={t('expenseForm.paidBy')}>
          <Select value={payerId} onChange={(e) => setPayerId(Number(e.target.value))}>
            {members.map((m) => (
              <option key={m.id} value={m.id}>
                {m.display_name}
              </option>
            ))}
          </Select>
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label={t('expenseForm.date')}>
            <TextInput type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </Field>
          <Field label={t('expenseForm.category')}>
            <Select value={category} onChange={(e) => setCategory(e.target.value)}>
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {t('category.' + c)}
                </option>
              ))}
            </Select>
          </Field>
        </div>
      </div>

      <Field label={t('expenseForm.splitMethod')}>
        <div className="flex gap-2">
          {(['equal', 'weighted', 'exact'] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMethod(m)}
              className={`flex-1 rounded-md px-3 py-1.5 text-sm font-medium border transition-colors ${
                method === m
                  ? 'bg-indigo-600 border-indigo-600 text-white'
                  : 'bg-white border-gray-300 text-gray-700 hover:bg-gray-50 dark:bg-gray-800 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-700'
              }`}
            >
              {t('split.' + m)}
            </button>
          ))}
        </div>
      </Field>

      <div>
        <p className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
          {method === 'exact' ? t('expenseForm.participantsExact') : t('expenseForm.participants')}
        </p>
        <TextInput
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t('expenseForm.searchMembers')}
          aria-label={t('expenseForm.searchMembers')}
          className="mb-2"
        />
        <div className="border border-gray-200 dark:border-gray-800 rounded-md divide-y divide-gray-100 dark:divide-gray-800 max-h-64 overflow-y-auto">
          {visibleMembers.map((m) => {
            const on = selected.has(m.id)
            return (
              <div key={m.id} className="flex items-center gap-3 px-3 py-2">
                <input
                  type="checkbox"
                  checked={on}
                  onChange={() => toggle(m.id)}
                  className="h-4 w-4 rounded border-gray-300 accent-indigo-600 focus:ring-indigo-500 dark:border-gray-600 dark:bg-gray-800"
                  aria-label={t('expenseForm.include', { name: m.display_name })}
                />
                <span
                  className={`flex-1 min-w-0 truncate text-sm ${on ? 'text-gray-800 dark:text-gray-100' : 'text-gray-400 dark:text-gray-500'}`}
                  title={m.display_name}
                >
                  {m.display_name}
                </span>
                {on && method === 'weighted' ? (
                  <TextInput
                    value={weights[m.id] ?? ''}
                    onChange={(e) => setWeights((w) => ({ ...w, [m.id]: e.target.value }))}
                    placeholder="1"
                    inputMode="decimal"
                    className="!w-16 sm:!w-20 !py-1 text-right min-w-0"
                  />
                ) : null}
                {on && method === 'exact' ? (
                  <TextInput
                    value={exactAmounts[m.id] ?? ''}
                    onChange={(e) => setExactAmounts((a) => ({ ...a, [m.id]: e.target.value }))}
                    placeholder="0.00"
                    inputMode="decimal"
                    className="!w-20 sm:!w-24 !py-1 text-right min-w-0"
                  />
                ) : null}
                {on && (method === 'equal' || method === 'weighted') && !('error' in (preview ?? {})) ? (
                  <span className="text-sm text-gray-500 dark:text-gray-400 tabular-nums w-24 text-right">
                    {formatMoney((preview as SplitPreviewRow[]).find((r) => r.user_id === m.id)?.share ?? '0', currency, locale)}
                  </span>
                ) : null}
              </div>
            )
          })}
        </div>
      </div>

      {method === 'exact' && exactTotal !== null ? (
        <p className={`text-sm ${exactBalanced ? 'text-emerald-700 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'}`}>
          {t('expenseForm.sharesTotal', { total: formatMoney(exactTotal, currency, locale) })}
          {exactBalanced
            ? t('expenseForm.matchesAmount')
            : t('expenseForm.expectedAmount', { amount: formatMoney(amount.trim() || '0', currency, locale) })}
        </p>
      ) : null}
      {'error' in (preview ?? {}) ? (
        <ErrorText>{localizePreviewError((preview as { error: string }).error, t)}</ErrorText>
      ) : null}

      {!initial ? (
        <div className="rounded-md border border-gray-200 dark:border-gray-800 p-3 space-y-2">
          <div className="flex items-baseline justify-between gap-2">
            <p className="text-sm font-medium text-gray-700 dark:text-gray-300">{t('expenseForm.receipt')}</p>
            <p className="text-xs text-gray-500 dark:text-gray-400">{t('expenseForm.receiptHint')}</p>
          </div>
          <input
            ref={pendingInputRef}
            type="file"
            accept={ACCEPT}
            className="hidden"
            onChange={(e) => pickPendingFile(e.target.files?.[0])}
            aria-label={t('receipt.file')}
          />
          {pendingPreview ? (
            <>
              {isPendingImage ? (
                <img src={pendingPreview} alt={t('receipt.file')} className="max-h-48 max-w-full rounded-md border border-gray-200 dark:border-gray-800" />
              ) : (
                <iframe src={pendingPreview} title={t('receipt.file')} className="h-48 w-full rounded-md border border-gray-200 dark:border-gray-800" />
              )}
              <div className="flex flex-wrap items-center gap-2">
                <Button variant="secondary" onClick={() => pendingInputRef.current?.click()}>
                  <IconUpload />
                  {t('receipt.replace')}
                </Button>
                <Button variant="secondary" onClick={clearPendingFile}>
                  <IconTrash />
                  {t('receipt.remove')}
                </Button>
              </div>
            </>
          ) : (
            <Button variant="secondary" onClick={() => pendingInputRef.current?.click()}>
              <IconPaperclip />
              {t('receipt.attach')}
            </Button>
          )}
          {receiptError ? <p className="text-xs text-red-600 dark:text-red-400">{receiptError}</p> : null}
        </div>
      ) : null}

      {initial ? (
        <div className="rounded-md border border-gray-200 dark:border-gray-800 p-3 space-y-2">
          <input
            ref={receiptInputRef}
            type="file"
            accept={ACCEPT}
            className="hidden"
            onChange={(e) => void replaceReceipt(e.target.files?.[0])}
            aria-label={t('receipt.file')}
          />
          {attachment ? (
            <>
              <p className="text-sm font-medium text-gray-700 dark:text-gray-300">{t('expenseForm.currentReceipt')}</p>
              {previewUrl ? (
                isPreviewImage ? (
                  <img src={previewUrl} alt={t('receipt.file')} className="max-h-48 max-w-full rounded-md border border-gray-200 dark:border-gray-800" />
                ) : (
                  <iframe src={previewUrl} title={t('receipt.file')} className="h-48 w-full rounded-md border border-gray-200 dark:border-gray-800" />
                )
              ) : null}
              <div className="flex flex-wrap items-center gap-2">
                {previewUrl ? (
                  <Button variant="secondary" onClick={() => setOpenFull(true)}>
                    <IconEye />
                    {t('receipt.openFull')}
                  </Button>
                ) : null}
                <Button variant="secondary" busy={receiptUpload.isPending} onClick={() => receiptInputRef.current?.click()}>
                  <IconUpload />
                  {t('receipt.replace')}
                </Button>
                <Button variant="secondary" busy={receiptDelete.isPending} onClick={() => setConfirmRemoveReceipt(true)}>
                  <IconTrash />
                  {t('receipt.remove')}
                </Button>
              </div>
            </>
          ) : (
            <>
              <div className="flex items-baseline justify-between gap-2">
                <p className="text-sm font-medium text-gray-700 dark:text-gray-300">{t('expenseForm.receipt')}</p>
                <p className="text-xs text-gray-500 dark:text-gray-400">{t('expenseForm.receiptHint')}</p>
              </div>
              <Button variant="secondary" busy={receiptUpload.isPending} onClick={() => receiptInputRef.current?.click()}>
                <IconPaperclip />
                {t('receipt.attach')}
              </Button>
            </>
          )}
          {receiptError ? <p className="text-xs text-red-600 dark:text-red-400">{receiptError}</p> : null}
        </div>
      ) : null}

      <Field label={t('expenseForm.comment')} hint={t('expenseForm.commentHint')}>
        <Textarea rows={2} value={comment} onChange={(e) => setComment(e.target.value)} placeholder={t('expenseForm.commentPh')} />
      </Field>

      <ErrorText>{error}</ErrorText>

      <div className="flex gap-2 justify-end">
        <Button variant="secondary" onClick={onDone}>
          {t('common.cancel')}
        </Button>
        <Button
          onClick={() => void submit()}
          busy={submitting || createExpense.isPending || updateExpense.isPending || uploadToReceipt.isPending}
          disabled={(selected.size === 0 || !description.trim() || !amount.trim() || payerId === 0) || submitting}
        >
          {initial ? t('expenseForm.save') : t('expenseForm.add')}
        </Button>
      </div>

      {openFull && previewUrl ? (
        <Modal title={t('receipt.file')} onClose={() => setOpenFull(false)} wide closeOnBackdrop>
          {isPreviewImage ? (
            <img src={previewUrl} alt={t('receipt.file')} className="mx-auto max-h-[70vh] max-w-full w-auto rounded-md" />
          ) : (
            <iframe src={previewUrl} title={t('receipt.file')} className="h-[70vh] w-full rounded-md border border-gray-200 dark:border-gray-800" />
          )}
        </Modal>
      ) : null}
      {confirmRemoveReceipt ? (
        <Modal title={t('receipt.removeModalTitle')} onClose={() => setConfirmRemoveReceipt(false)}>
          <p className="text-sm text-gray-700 dark:text-gray-300">{t('receipt.removeModalBody')}</p>
          <div className="flex justify-end gap-2 mt-4">
            <Button variant="secondary" onClick={() => setConfirmRemoveReceipt(false)}>
              {t('common.cancel')}
            </Button>
            <Button variant="danger" busy={receiptDelete.isPending} onClick={() => void removeReceipt()}>
              {t('receipt.remove')}
            </Button>
          </div>
        </Modal>
      ) : null}
    </div>
  )
}
