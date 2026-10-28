/**
 * /invite/:token accept page (F3, AGENTS.md §2.10).
 *
 * Accepting requires an account, so unauthenticated visitors are offered the
 * register-then-join flow: auth lands back here (?next=/invite/:token) and
 * the Join button posts the token. Link invites are reusable; email invites
 * are bound to the addressed email (the server validates).
 *
 * The language switcher is here too (top-right) — the page is reachable
 * without authentication.
 */

import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { ApiError, getTokens } from '../api/client'
import { invitesApi } from '../api/endpoints'
import { LanguageSwitcher } from '../components/LanguageSwitcher'
import { Button, Card, ErrorText, Spinner } from '../components/ui'
import { useI18n } from '../i18n'

export function InvitePage() {
  const { token = '' } = useParams()
  const navigate = useNavigate()
  const { t } = useI18n()
  const [state, setState] = useState<'idle' | 'joining' | 'done' | 'error'>('idle')
  const [error, setError] = useState('')
  const [, setGroupId] = useState<number | null>(null)

  const authenticated = !!getTokens()?.access

  const join = async () => {
    setState('joining')
    setError('')
    try {
      const group = await invitesApi.accept(token)
      setGroupId(group.id)
      setState('done')
      // Give the button a beat to show success, then go.
      setTimeout(() => navigate(`/groups/${group.id}`, { replace: true }), 400)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : t('invite.joinError'))
      setState('error')
    }
  }

  return (
    <div className="relative min-h-screen bg-gray-50 dark:bg-gray-950 flex items-center justify-center p-4">
      <div className="absolute right-4 top-4">
        <LanguageSwitcher />
      </div>
      <Card className="p-6 w-full max-w-md space-y-4 text-center">
        <h1 className="text-lg font-bold text-gray-900 dark:text-gray-50">{t('invite.title')}</h1>
        <p className="text-sm text-gray-600 dark:text-gray-300">
          {t('invite.text')}
          {authenticated ? t('invite.textAuthed') : ''}
        </p>

        {authenticated ? (
          <>
            {state === 'joining' ? (
              <Spinner label={t('invite.joining')} />
            ) : state === 'done' ? (
              <p className="text-sm text-emerald-700 dark:text-emerald-400 font-medium">{t('invite.joined')}</p>
            ) : (
              <Button onClick={() => void join()} className="w-full">
                {t('invite.join')}
              </Button>
            )}
            <ErrorText>{state === 'error' ? error : ''}</ErrorText>
          </>
        ) : (
          <div className="space-y-2">
            <p className="text-xs text-gray-500 dark:text-gray-400">{t('invite.needAccount')}</p>
            <Link to={`/register?next=${encodeURIComponent(`/invite/${token}`)}`} className="block">
              <Button className="w-full">{t('invite.registerJoin')}</Button>
            </Link>
            <Link to={`/login?next=${encodeURIComponent(`/invite/${token}`)}`} className="block">
              <Button variant="secondary" className="w-full">
                {t('invite.loginJoin')}
              </Button>
            </Link>
          </div>
        )}

        <p className="text-xs text-gray-400 dark:text-gray-500">
          {authenticated && state !== 'done' && state !== 'joining' ? (
            <Link to="/" className="hover:underline">
              {t('overview.back')}
            </Link>
          ) : null}
        </p>
      </Card>
    </div>
  )
}
