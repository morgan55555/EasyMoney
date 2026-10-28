/**
 * Receipt upload/replace/view for one expense (AGENTS.md §3.5).
 * The file goes through the bearer-authenticated fetch (plain <a> can't set
 * headers). Viewing opens a modal fed by a single object URL, created once per
 * view and revoked when the modal closes (and on unmount) so URLs never leak.
 * Receipts are never inlined in the list — only icon+text buttons.
 */

import { useEffect, useRef, useState } from 'react'

import { ApiError } from '../api/client'
import { fetchAttachmentBlob } from '../api/endpoints'
import { useI18n } from '../i18n'
import { useDeleteReceipt, useUploadReceipt } from '../query/queries'
import type { ExpenseOut } from '../types'
import { Button, IconEye, IconPaperclip, IconTrash, IconUpload, Modal } from './ui'

// Only what the backend accepts (image/* or application/pdf, §3.5).
export const ACCEPT = 'image/jpeg,image/png,image/gif,image/webp,application/pdf'

type ViewKind = 'image' | 'document'

export function ReceiptUpload({ expense, groupId }: { expense: ExpenseOut; groupId: number | null }) {
  const upload = useUploadReceipt(groupId, expense.id)
  const remove = useDeleteReceipt(groupId, expense.id)
  const inputRef = useRef<HTMLInputElement>(null)
  const { t } = useI18n()
  const [error, setError] = useState('')
  const [viewUrl, setViewUrl] = useState<string | null>(null)
  const [viewKind, setViewKind] = useState<ViewKind>('image')
  const [confirmRemove, setConfirmRemove] = useState(false)

  // Revoke the object URL whenever it changes (covers close and unmount).
  useEffect(() => {
    if (!viewUrl) return
    return () => URL.revokeObjectURL(viewUrl)
  }, [viewUrl])

  const attach = expense.attachment

  const onFile = async (file: File | undefined) => {
    if (!file) return
    setError('')
    try {
      await upload.mutateAsync(file)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : t('receipt.uploadError'))
    } finally {
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  const view = async () => {
    if (!attach) return
    setError('')
    try {
      const blob = await fetchAttachmentBlob(attach.id)
      setViewKind(attach.content_type.startsWith('image/') ? 'image' : 'document')
      setViewUrl(URL.createObjectURL(blob))
    } catch (e) {
      setError(e instanceof Error ? e.message : t('receipt.loadError'))
    }
  }

  const onRemove = async () => {
    if (!attach) return
    setError('')
    try {
      await remove.mutateAsync()
      setConfirmRemove(false)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : t('receipt.removeError'))
    }
  }

  const viewLabel = !attach
    ? ''
    : attach.content_type === 'application/pdf'
      ? t('receipt.viewPdf')
      : attach.content_type.startsWith('image/')
        ? t('receipt.view')
        : t('receipt.viewFile')

  return (
    <div className="space-y-1">
      {/* The `hidden` attribute (not just the class) keeps this first child out
          of the space-y-1 sibling-margin rule, so the button row aligns with
          the Edit/Delete icons in the card footer. */}
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        hidden
        className="hidden"
        onChange={(e) => void onFile(e.target.files?.[0])}
        aria-label={t('receipt.file')}
      />
      <div className="flex flex-wrap items-center gap-2">
        {attach ? (
          <>
            <Button variant="secondary" onClick={() => void view()}>
              <IconEye />
              {viewLabel}
            </Button>
            <Button variant="secondary" busy={upload.isPending} onClick={() => inputRef.current?.click()}>
              <IconUpload />
              {t('receipt.replace')}
            </Button>
            <Button variant="secondary" busy={remove.isPending} onClick={() => setConfirmRemove(true)}>
              <IconTrash />
              {t('receipt.remove')}
            </Button>
          </>
        ) : (
          <Button variant="secondary" busy={upload.isPending} onClick={() => inputRef.current?.click()}>
            <IconPaperclip />
            {t('receipt.attach')}
          </Button>
        )}
      </div>
      {error ? <p className="text-xs text-red-600 dark:text-red-400">{error}</p> : null}
      {viewUrl ? (
        <Modal title={t('receipt.file')} onClose={() => setViewUrl(null)} wide closeOnBackdrop>
          {viewKind === 'image' ? (
            <img src={viewUrl} alt={t('receipt.file')} className="mx-auto max-h-[70vh] max-w-full w-auto rounded-md" />
          ) : (
            <iframe
              src={viewUrl}
              title={t('receipt.file')}
              className="h-[70vh] w-full rounded-md border border-gray-200 dark:border-gray-800"
            />
          )}
        </Modal>
      ) : null}
      {confirmRemove ? (
        <Modal title={t('receipt.removeModalTitle')} onClose={() => setConfirmRemove(false)}>
          <p className="text-sm text-gray-700 dark:text-gray-300">{t('receipt.removeModalBody')}</p>
          <div className="flex justify-end gap-2 mt-4">
            <Button variant="secondary" onClick={() => setConfirmRemove(false)}>
              {t('common.cancel')}
            </Button>
            <Button variant="danger" busy={remove.isPending} onClick={() => void onRemove()}>
              {t('receipt.remove')}
            </Button>
          </div>
        </Modal>
      ) : null}
    </div>
  )
}
