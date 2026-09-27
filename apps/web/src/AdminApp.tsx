import type { ReactNode } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { AdminSessionProvider, useAdminSession } from './admin/AdminSession'
import { AdminLogin } from './admin/AdminLogin'
import { AdminShell } from './admin/AdminShell'
import { AdminOverview } from './admin/AdminOverview'
import { AdminDocuments } from './admin/AdminDocuments'
import { AdminOcr } from './admin/AdminOcr'
import { AdminRelationships } from './admin/AdminRelationships'
import { AdminJobs } from './admin/AdminJobs'
import { AdminAudit } from './admin/AdminAudit'
import { AdminUsers } from './admin/AdminUsers'

/**
 * The archivist application.
 *
 * It is a separate set of routes from the public archive and shares nothing
 * with it except the API: no session, no cached pages, and no document data in
 * a bundle a reader can read without signing in. That separation is deliberate,
 * because a curator's session should never be handed to a visitor.
 */
export function AdminApp() {
  return (
    <AdminSessionProvider>
      <AdminGate />
    </AdminSessionProvider>
  )
}

function AdminGate() {
  const { user, checking } = useAdminSession()

  if (checking) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-stone-100 text-stone-600">
        Checking your session…
      </main>
    )
  }
  if (!user) return <AdminLogin />

  return (
    <AdminShell>
      <Routes>
        <Route index element={<AdminOverview />} />
        <Route path="documents" element={<AdminDocuments />} />
        <Route path="ocr" element={<AdminOcr />} />
        <Route path="relationships" element={<AdminRelationships />} />
        <Route path="jobs" element={<AdminJobs />} />
        <Route path="audit" element={<AdminAudit />} />
        <Route path="users" element={<AdminUsers />} />
        <Route path="*" element={<Navigate to="/admin" replace />} />
      </Routes>
    </AdminShell>
  )
}

/** A wrapper so a signed-out visitor reaches the sign-in form. */
export function RequireAdmin({ children }: { children: ReactNode }) {
  const { user, checking } = useAdminSession()
  if (checking) return null
  if (!user) return <AdminLogin />
  return <>{children}</>
}
