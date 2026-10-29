/**
 * /invite/:token accept page (F3, AGENTS.md §2.10).
 *
 * Accepting requires an account. Whether the visitor is signed in is detected
 * with the same self-healing /me query the rest of the app uses (see
 * `useMe`): a lapsed access token is renewed through the 401→refresh dance
 * before we decide they are signed out, so a logged-in user is never bounced
 * to the login/register prompt. Only a genuinely signed-out visitor (no valid
 * session) sees the register-then-join flow; auth lands back here
 * (?next=/invite/:token) and the Join button posts the token. Link invites are
 * reusable; email invites are bound to the addressed email (the server
 * validates).
 *
 * The language switcher is here too (top-right) — the page is reachable
 * without authentication.
 */

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { ApiError, getTokens, hasSession } from '../api/client'
import { invitesApi, meApi } from '../api/endpoints'
import { LanguageSwitcher } from '../components/LanguageSwitcher'
import { Button, Card, ErrorText, Spinner } from '../components/ui'
import { useI18n } from '../i18n'
import { keys } from '../query/queries'

export function InvitePage() {
  const { token = '' } = useParams()
  const navigate = useNavigate()
  const { t } = useI18n()
  // "Is there any session material at all?" — access and/or refresh token.
  const session = hasSession()
  // `me` is the source of truth for "is this visitor signed in?": it heals a
  // lapsed access token via the 401→refresh dance before resolving. It only
  // runs when there is something to heal or confirm — an anonymous visitor
  // has no token, is signed out by definition, and gets the register/login
  // prompt immediately (no network round-trip, no spinner). `retry: false`
  // keeps a dead refresh (401 twice) from retrying with backoff: the answer
  // is known after one round-trip.
  const me = useQuery({ queryKey: keys.me, queryFn: () => meApi.get(), enabled: session, retry: false })
  const [state, setState] = useState<'idle' | 'joining' | 'done' | 'error'>('idle')
  const [error, setError] = useState('')
  const [, setGroupId] = useState<number | null>(null)

  // We only show the Join UI once the session is confirmed. If /me errored but
  // tokens are still present (a transient network blip), we stay optimistic
  // and show Join — clicking it re-validates — rather than logging a signed-in
  // user out.
  const authenticated = session && (!!me.data || (me.isError && !!getTokens()))
  const checking = session && me.isPending

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

        {checking ? (
          <Spinner label={t('invite.checking')} />
        ) : authenticated ? (
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
          {authenticated && !checking && state !== 'done' && state !== 'joining' ? (
            <Link to="/" className="hover:underline">
              {t('overview.back')}
            </Link>
          ) : null}
        </p>
      </Card>
    </div>
  )
}
