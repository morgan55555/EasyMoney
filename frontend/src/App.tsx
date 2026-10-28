/**
 * App router (F2): public auth + invite + email-confirm routes, and the
 * authenticated shell (Layout) for the group app.
 *
 * Guards:
 *  - RequireAuth     — bounce unauthenticated visitors to /login.
 *  - RedirectIfAuthed — bounce signed-in users away from the auth pages.
 *  - RequireVerified — while /me is pending, show a spinner; a not-yet-verified
 *                       account (register confirmation on, §2.13) sees the
 *                       full-screen VerifyEmailGate instead of the app.
 *  - RequireAdmin    — the /admin route is only reachable for is_admin users.
 */

import { useEffect } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation, useParams } from 'react-router-dom'

import { getTokens } from './api/client'
import { Layout } from './components/Layout'
import { VerifyEmailGate } from './components/VerifyEmailGate'
import { Spinner } from './components/ui'
import { useI18n } from './i18n'
import { useMe } from './query/queries'
import { AuthPage } from './pages/AuthPage'
import { AdminPage } from './pages/AdminPage'
import { ConfirmEmailPage } from './pages/ConfirmEmailPage'
import { GroupsPage } from './pages/GroupsPage'
import { GroupPage } from './pages/GroupPage'
import { InvitePage } from './pages/InvitePage'
import { OverviewPage } from './pages/OverviewPage'

function RequireAuth({ children }: { children: React.ReactNode }) {
  const location = useLocation()
  if (!getTokens()) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }
  return <>{children}</>
}

function RedirectIfAuthed({ children }: { children: React.ReactNode }) {
  if (getTokens()) {
    return <Navigate to="/" replace />
  }
  return <>{children}</>
}

function RequireVerified({ children }: { children: React.ReactNode }) {
  const me = useMe()
  if (me.isPending) return <FullScreenSpinner />
  if (me.data && !me.data.email_verified) return <VerifyEmailGate me={me.data} />
  return <>{children}</>
}

function RequireAdmin({ children }: { children: React.ReactNode }) {
  const me = useMe()
  if (me.isPending) return <FullScreenSpinner />
  if (!me.data?.is_admin) return <Navigate to="/" replace />
  return <>{children}</>
}

function FullScreenSpinner() {
  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-950 flex items-center justify-center">
      <Spinner />
    </div>
  )
}

/**
 * Inside the app the UI language is exactly the user's `preferred_language`
 * (§2.13): once /me resolves, adopt it for this session. `applyPreferred`
 * never writes to localStorage, so the stored public-page choice (used on
 * login/register) is not clobbered, and switching accounts re-applies the
 * new user's preference.
 */
function PreferredLanguageSync() {
  const me = useMe()
  const { applyPreferred } = useI18n()
  const preferred = me.data?.preferred_language
  useEffect(() => {
    if (preferred) applyPreferred(preferred)
  }, [preferred, applyPreferred])
  return null
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route
          path="/login"
          element={
            <RedirectIfAuthed>
              <AuthPage mode="login" />
            </RedirectIfAuthed>
          }
        />
        <Route
          path="/register"
          element={
            <RedirectIfAuthed>
              <AuthPage mode="register" />
            </RedirectIfAuthed>
          }
        />
        <Route path="/invite/:token" element={<InvitePage />} />
        <Route path="/auth/email/confirm" element={<ConfirmEmailPage />} />

        <Route
          path="/"
          element={
            <RequireAuth>
              <RequireVerified>
                <PreferredLanguageSync />
                <Layout />
              </RequireVerified>
            </RequireAuth>
          }
        >
          <Route index element={<GroupsPage />} />
          <Route path="groups/:groupId" element={<GroupPageParam />} />
          <Route path="overview" element={<OverviewPage />} />
          <Route
            path="admin"
            element={
              <RequireAdmin>
                <AdminPage />
              </RequireAdmin>
            }
          />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}

function GroupPageParam() {
  // Resolve the path param inside the routed subtree.
  const { groupId } = useParams()
  if (!groupId) return <Navigate to="/" replace />
  return <GroupPage groupId={Number(groupId)} />
}
