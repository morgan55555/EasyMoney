/**
 * App router (F2): public auth + invite routes, and the authenticated shell
 * (Layout) for the group app. A guard bounces unauthenticated visitors to
 * /login and already-authenticated users away from the auth pages.
 */

import { BrowserRouter, Navigate, Route, Routes, useLocation, useParams } from 'react-router-dom'

import { getTokens } from './api/client'
import { Layout } from './components/Layout'
import { AuthPage } from './pages/AuthPage'
import { InvitePage } from './pages/InvitePage'
import { GroupsPage } from './pages/GroupsPage'
import { GroupPage } from './pages/GroupPage'
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

        <Route
          path="/"
          element={
            <RequireAuth>
              <Layout />
            </RequireAuth>
          }
        >
          <Route index element={<GroupsPage />} />
          <Route path="groups/:groupId" element={<GroupPageParam />} />
          <Route path="overview" element={<OverviewPage />} />
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
