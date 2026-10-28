/**
 * Full-screen registration-confirmation gate (AGENTS.md §2.13). Shown in place
 * of the whole app while the signed-in user's `email_verified` is false (only
 * when register confirmation is enabled). The user is blocked from the app
 * until they click the confirmation link in their email; a "resend" reissues
 * the pending token and emails it again.
 */

import { useState } from 'react'

import { ApiError } from '../api/client'
import { useI18n } from '../i18n'
import { useResendConfirmation } from '../query/queries'
import type { UserOut } from '../types'
import { AppIcon } from './AppIcon'
import { Button, Card } from './ui'

export function VerifyEmailGate({ me }: { me: UserOut }) {
  const { t } = useI18n()
  const resend = useResendConfirmation()
  const [msg, setMsg] = useState<{ kind: 'success' | 'error'; text: string } | null>(null)

  const onResend = () => {
    setMsg(null)
    resend.mutate(undefined, {
      onSuccess: () => setMsg({ kind: 'success', text: t('verify.resent') }),
      onError: (e) =>
        setMsg({ kind: 'error', text: e instanceof ApiError ? e.message : t('verify.resendError') }),
    })
  }

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-950 flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <AppIcon className="h-20 w-20 mx-auto mb-4 rounded-2xl shadow-sm" />
        <h1 className="text-xl font-bold text-gray-900 text-center mb-1 dark:text-gray-50">
          Easy<span className="text-indigo-600">Money</span>
        </h1>
        <Card className="p-5 text-center space-y-4">
          <p className="text-sm font-medium text-gray-800 dark:text-gray-100">{t('verify.title')}</p>
          <p className="text-sm text-gray-600 dark:text-gray-300">
            {t('verify.text', { email: me.email })}
          </p>
          <Button variant="secondary" onClick={onResend} busy={resend.isPending}>
            {t('verify.resend')}
          </Button>
          {msg ? (
            <p
              className={`text-sm ${
                msg.kind === 'success'
                  ? 'text-emerald-700 dark:text-emerald-400'
                  : 'text-red-600 dark:text-red-400'
              }`}
            >
              {msg.text}
            </p>
          ) : null}
        </Card>
      </div>
    </div>
  )
}
