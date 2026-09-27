import { useState } from 'react'
import { ApiError } from '../lib/adminApi'
import { useAdminSession } from './AdminSession'

/**
 * Sign in to the curator's application.
 *
 * The archive's public side needs no account. This form exists because the
 * verification, publishing and OCR actions do, and each of those changes what
 * the public archive is allowed to claim.
 */
export function AdminLogin() {
  const { signIn } = useAdminSession()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [requestId, setRequestId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    setRequestId(null)
    try {
      await signIn(email, password)
    } catch (cause) {
      // The backend deliberately does not say which half was wrong, and this
      // form does not guess either.
      const failure = cause instanceof ApiError ? cause : null
      setError(failure ? failure.message : 'Could not sign in.')
      setRequestId(failure?.requestId ?? null)
      setPassword('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-6 py-16">
      <div className="rounded-xl border border-stone-300 bg-white p-8 shadow-sm">
        <p className="text-xs font-semibold uppercase tracking-widest text-amber-700">
          Archivist application
        </p>
        <h1 className="mt-2 text-2xl font-semibold text-stone-900">Sign in</h1>
        <p className="mt-2 text-sm text-stone-600">
          This area can publish records, correct OCR and change what the public archive states
          about a source. Access is limited to staff roles.
        </p>

        <form className="mt-6 space-y-4" onSubmit={onSubmit}>
          <label className="block text-sm">
            <span className="font-medium text-stone-800">Email</span>
            <input
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className="mt-1 w-full rounded-md border border-stone-300 px-3 py-2 text-stone-900"
            />
          </label>
          <label className="block text-sm">
            <span className="font-medium text-stone-800">Password</span>
            <input
              type="password"
              autoComplete="current-password"
              required
              minLength={8}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="mt-1 w-full rounded-md border border-stone-300 px-3 py-2 text-stone-900"
            />
          </label>

          {error && (
            <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-900">
              <p>{error}</p>
              {requestId && (
                <p className="mt-1 font-mono text-xs text-red-700">Request {requestId}</p>
              )}
            </div>
          )}

          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-md bg-stone-900 px-4 py-2 font-medium text-white disabled:opacity-50"
          >
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </div>
    </main>
  )
}
