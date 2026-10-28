/**
 * Authenticated app shell: top nav (Groups / Overview / Admin-for-admins),
 * the notification bell, the user menu (avatar → Settings / Log out), and the
 * routed content below.
 *
 * Also owns the socket lifecycle: the socket connects while the user is
 * logged in and is stopped on unmount (logout / session expiry).
 */

import { useEffect, useState } from 'react'
import { Link, NavLink, Outlet, useNavigate } from 'react-router-dom'

import { AUTH_EXPIRED_EVENT, clearTokens, getTokens } from '../api/client'
import { authApi } from '../api/endpoints'
import { useI18n } from '../i18n'
import { useMe, bindSocket } from '../query/queries'
import { socket } from '../ws/socket'
import { AppIcon } from './AppIcon'
import { NotificationsBell } from './NotificationsBell'
import { SettingsModal } from './SettingsModal'
import { UserMenu } from './UserMenu'

const navLinkClass = ({ isActive }: { isActive: boolean }) =>
  `px-2 sm:px-3 py-1.5 rounded-md text-sm font-medium ${
    isActive
      ? 'bg-gray-100 text-gray-900 dark:bg-gray-800 dark:text-gray-100'
      : 'text-gray-600 hover:text-gray-900 hover:bg-gray-50 dark:text-gray-300 dark:hover:text-white dark:hover:bg-gray-800'
  }`

export function Layout() {
  const navigate = useNavigate()
  const me = useMe()
  const { t } = useI18n()
  const [settingsOpen, setSettingsOpen] = useState(false)

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
    <div className="min-h-screen bg-gray-50 dark:bg-gray-950">
      <header className="bg-white border-b border-gray-200 dark:bg-gray-900 dark:border-gray-800">
        <div className="max-w-5xl mx-auto px-3 sm:px-4 h-14 flex items-center gap-2 sm:gap-4">
          <Link to="/" className="flex items-center gap-2 text-2xl font-bold text-gray-900 tracking-tight dark:text-gray-50">
            <AppIcon className="h-8 w-8 sm:h-12 sm:w-12 rounded-md" />
            {/* Wordmark hidden on narrow screens: with it, the nav row overflows
                the viewport (the icon alone keeps the brand visible). */}
            <span className="hidden sm:inline">
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
            {me.data?.is_admin ? (
              <NavLink to="/admin" className={navLinkClass}>
                {t('nav.admin')}
              </NavLink>
            ) : null}
          </nav>
          <div className="ml-auto flex items-center gap-2 sm:gap-3">
            <NotificationsBell />
            <UserMenu me={me.data} onSettings={() => setSettingsOpen(true)} onLogout={() => void logout()} />
          </div>
        </div>
      </header>
      <main className="max-w-5xl mx-auto px-4 py-6">
        <Outlet />
      </main>
      {settingsOpen ? <SettingsModal open onClose={() => setSettingsOpen(false)} /> : null}
    </div>
  )
}
