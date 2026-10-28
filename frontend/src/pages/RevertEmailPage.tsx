/**
 * Email-change revert landing page (AGENTS.md §2.13). The "this wasn't me"
 * link in the revert email (mailed to the *previous* address when an email
 * change is requested) points here with a `?token=` query param. On mount the
 * token is applied via POST /api/auth/email/revert (public — works whether or
 * not the visitor is signed in). It cancels a still-pending change, or switches
 * the account email back to the previous address if the new address already
 * confirmed it. The page reports success or failure (invalid / expired link).
 */

import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { ApiError } from '../api/client'
import { useI18n } from '../i18n'
import { useRevertEmail } from '../query/queries'
import { Button, Card } from '../components/ui'

type Phase = 'working' | 'success' | 'failure'

export function RevertEmailPage() {
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''
  const { t } = useI18n()
  const revert = useRevertEmail()
  const [phase, setPhase] = useState<Phase>(token ? 'working' : 'failure')
  const [error, setError] = useState('')
  const started = useRef(false)

  useEffect(() => {
    if (!token || started.current) return
    started.current = true
    void revert
      .mutateAsync(token)
      .then(() => setPhase('success'))
      .catch((e) => {
        setError(e instanceof ApiError ? e.message : '')
        setPhase('failure')
      })
  }, [token, revert])

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-950 flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <img src="/icon.png" alt="" className="h-20 w-20 mx-auto mb-4 rounded-2xl shadow-sm" />
        <h1 className="text-xl font-bold text-gray-900 text-center mb-4 dark:text-gray-50">
          {t('revert.title')}
        </h1>
        <Card className="p-5 text-center">
          {phase === 'working' ? (
            <p className="text-sm text-gray-500 dark:text-gray-400">{t('revert.working')}</p>
          ) : phase === 'success' ? (
            <div className="space-y-4">
              <p className="text-sm font-medium text-emerald-700 dark:text-emerald-400">{t('revert.success')}</p>
              <p className="text-sm text-gray-600 dark:text-gray-300">{t('revert.successText')}</p>
              <Link to="/" className="block">
                <Button className="w-full">{t('revert.openApp')}</Button>
              </Link>
            </div>
          ) : (
            <div className="space-y-4">
              <p className="text-sm font-medium text-red-600 dark:text-red-400">{t('revert.failure')}</p>
              <p className="text-sm text-gray-600 dark:text-gray-300">{t('revert.failureText')}</p>
              {error ? <p className="text-xs text-gray-500 dark:text-gray-400">{error}</p> : null}
              <Link to="/login" className="block">
                <Button variant="secondary" className="w-full">
                  {t('revert.backToLogin')}
                </Button>
              </Link>
            </div>
          )}
        </Card>
      </div>
    </div>
  )
}
