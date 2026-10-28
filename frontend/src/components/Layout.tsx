/**
 * Authenticated app shell: top nav (Groups / Overview), the notification
 * bell, the user chip with logout, and the routed content below.
 *
 * Also owns the socket lifecycle: the socket connects while the user is
 * logged in and is stopped on unmount (logout / session expiry).
 */

import { useEffect } from 'react'
import { Link, NavLink, Outlet, useNavigate } from 'react-router-dom'

import { AUTH_EXPIRED_EVENT, clearTokens, getTokens } from '../api/client'
import { authApi } from '../api/endpoints'
import { useI18n } from '../i18n'
import { useMe, bindSocket } from '../query/queries'
import { socket } from '../ws/socket'
import { LanguageSwitcher } from './LanguageSwitcher'
import { NotificationsBell } from './NotificationsBell'
import { IconLogOut } from './ui'

const navLinkClass = ({ isActive }: { isActive: boolean }) =>
  `px-3 py-1.5 rounded-md text-sm font-medium ${
    isActive ? 'bg-gray-100 text-gray-900' : 'text-gray-600 hover:text-gray-900 hover:bg-gray-50'
  }`

export function Layout() {
  const navigate = useNavigate()
  const me = useMe()
  const { t } = useI18n()

  useEffect(() => {
    bindSocket()
    socket.start()
    return () => socket.stop()
  }, [])

  useEffect(() => {
    const onExpired = () => {
      navigate('/login', { replace: true })
    }
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired)
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired)
  }, [navigate])

  // The 401 on /me means the session is gone even though a token string exists.
  useEffect(() => {
    if (me.isError && (me.error as { status?: number })?.status === 401) {
      clearTokens()
      navigate('/login', { replace: true })
    }
  }, [me.isError, me.error, navigate])

  const logout = async () => {
    const tokens = getTokens()
    if (tokens?.refresh) {
      try {
        await authApi.logout(tokens.refresh)
      } catch {
        // Revocation failed (e.g. network); the local session still ends.
      }
    }
    clearTokens()
    navigate('/login', { replace: true })
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b border-gray-200">
        <div className="max-w-5xl mx-auto px-4 h-14 flex items-center gap-4">
          <Link to="/" className="flex items-center gap-2 font-bold text-gray-900 tracking-tight">
            <img src="/icon.png" alt="" className="h-7 w-7 rounded-md" />
            <span>
              Easy<span className="text-indigo-600">Money</span>
            </span>
          </Link>
          <nav className="flex items-center gap-1">
            <NavLink to="/" end className={navLinkClass}>
              {t('nav.groups')}
            </NavLink>
            <NavLink to="/overview" className={navLinkClass}>
              {t('nav.overview')}
            </NavLink>
          </nav>
          <div className="ml-auto flex items-center gap-3">
            <LanguageSwitcher />
            <NotificationsBell />
            <span className="text-sm text-gray-700 hidden sm:block" title={me.data?.email}>
              {me.data?.display_name ?? '…'}
            </span>
            <button
              onClick={() => void logout()}
              className="inline-flex items-center gap-1 rounded-md border border-gray-300 bg-white px-2 py-1 text-xs font-medium leading-[17px] text-gray-700 shadow-sm transition-colors hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            >
              <IconLogOut className="h-3.5 w-3.5 shrink-0" />
              {t('nav.logOut')}
            </button>
          </div>
        </div>
      </header>
      <main className="max-w-5xl mx-auto px-4 py-6">
        <Outlet />
      </main>
    </div>
  )
}
