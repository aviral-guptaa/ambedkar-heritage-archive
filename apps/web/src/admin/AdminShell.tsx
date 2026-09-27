import type { ReactNode } from 'react'
import { useAdminSession } from './AdminSession'

/**
 * The frame around every archivist screen.
 *
 * It shows a curator who they are signed in as, which screens their role can
 * reach, and a way back to the public archive — so it is always obvious whether
 * they are looking at the public claims or the editorial record behind them.
 */
export function AdminShell({ children }: { children: ReactNode }) {
  const { user, signOut, role, can } = useAdminSession()

  const links: Array<{ label: string; href: string; permission: string | null }> = [
    { label: 'Overview', href: '/admin', permission: null },
    { label: 'Documents', href: '/admin/documents', permission: 'document:read' },
    { label: 'OCR review', href: '/admin/ocr', permission: 'ocr:approve' },
    { label: 'Relationships', href: '/admin/relationships', permission: 'graph:review' },
    { label: 'Background jobs', href: '/admin/jobs', permission: 'job:read' },
    { label: 'Audit log', href: '/admin/audit', permission: 'audit:read' },
    { label: 'Users', href: '/admin/users', permission: 'user:read' },
  ]

  return (
    <div className="min-h-screen bg-stone-100 text-stone-900">
      <header className="border-b border-stone-300 bg-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-4 gap-y-2 px-6 py-3">
          <span className="font-semibold">Archivist application</span>
          <nav className="flex flex-wrap gap-1 text-sm">
            {links.map((link) => {
              if (link.permission && !can(link.permission)) return null
              return (
                <a
                  key={link.href}
                  href={link.href}
                  className="rounded px-2.5 py-1 text-stone-700 hover:bg-stone-200"
                >
                  {link.label}
                </a>
              )
            })}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-sm">
            <a className="text-stone-600 underline" href="/">
              Public archive
            </a>
            <span className="text-stone-600">
              {user?.full_name || user?.email} · {role}
            </span>
            <button
              type="button"
              onClick={signOut}
              className="rounded border border-stone-300 px-2.5 py-1 hover:bg-stone-100"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-6 py-8">{children}</main>
    </div>
  )
}

/** A titled card, so every screen has the same visual structure. */
export function Panel({
  title,
  children,
  action,
}: {
  title: string
  children: ReactNode
  action?: ReactNode
}) {
  return (
    <section className="rounded-xl border border-stone-300 bg-white p-5 shadow-sm">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-4">
        <h2 className="text-lg font-semibold">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  )
}

/** A failed load is always shown, with the request ID needed to find its log. */
export function LoadFailure({ error, requestId }: { error: string; requestId: string | null }) {
  return (
    <div
      role="alert"
      className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-900"
    >
      <p>{error}</p>
      {requestId && <p className="mt-1 font-mono text-xs text-red-700">Request {requestId}</p>}
    </div>
  )
}

export function Loading() {
  return <p className="text-sm text-stone-600">Loading…</p>
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="text-sm text-stone-600">{children}</p>
}

/** A small button, disabled while its own action is in flight. */
export function Action({
  children,
  onClick,
  disabled,
  tone = 'neutral',
}: {
  children: ReactNode
  onClick: () => void
  disabled?: boolean
  tone?: 'neutral' | 'danger'
}) {
  const classes =
    tone === 'danger'
      ? 'border-red-300 text-red-800 hover:bg-red-50'
      : 'border-stone-300 hover:bg-stone-100'
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`rounded border bg-white px-2 py-1 text-xs font-medium disabled:opacity-50 ${classes}`}
    >
      {children}
    </button>
  )
}

/** A transient result of an action, announced to assistive technology. */
export function Outcome({ notice, failure }: { notice: string | null; failure: string | null }) {
  if (notice) {
    return (
      <p
        role="status"
        className="mb-3 rounded-md border border-emerald-300 bg-emerald-50 p-2 text-sm text-emerald-900"
      >
        {notice}
      </p>
    )
  }
  if (failure) {
    return (
      <p
        role="alert"
        className="mb-3 rounded-md border border-red-300 bg-red-50 p-2 text-sm text-red-900"
      >
        {failure}
      </p>
    )
  }
  return null
}
