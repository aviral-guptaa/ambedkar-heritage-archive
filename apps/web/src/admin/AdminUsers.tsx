import { useState } from 'react'
import { ApiError, adminApi, type RoleDefinition } from '../lib/adminApi'
import { useAdminSession } from './AdminSession'
import { useLoader } from './useLoader'
import { Action, Empty, LoadFailure, Loading, Outcome, Panel } from './AdminShell'

/**
 * Accounts and what each role may do.
 *
 * The permissions shown are read from the archive, not written here, so this
 * screen cannot disagree with what the server will actually allow.
 */
export function AdminUsers() {
  const { user: me, role: myRole } = useAdminSession()
  const users = useLoader(() => adminApi.users())
  const roles = useLoader(() => adminApi.roles())
  const [notice, setNotice] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const [email, setEmail] = useState('')
  const [fullName, setFullName] = useState('')
  const [password, setPassword] = useState('')
  const [newRole, setNewRole] = useState('VIEWER')
  const [formError, setFormError] = useState<string | null>(null)

  async function create(event: React.FormEvent) {
    event.preventDefault()
    setFormError(null)
    setNotice(null)
    setFailure(null)
    if (password.length < 12) {
      setFormError('A new account needs a password of at least 12 characters.')
      return
    }
    setBusy('create')
    try {
      const created = await adminApi.createUser({
        email,
        password,
        role: newRole,
        ...(fullName ? { full_name: fullName } : {}),
      })
      setNotice(`Created ${created.email} as ${created.role}.`)
      setEmail('')
      setFullName('')
      setPassword('')
      users.reload()
    } catch (cause) {
      setFormError(cause instanceof ApiError ? cause.message : 'That account could not be created.')
    } finally {
      setBusy(null)
    }
  }

  async function deactivate(id: string, name: string) {
    if (!window.confirm(`Deactivate ${name}? They will not be able to sign in.`)) return
    setBusy(id)
    setNotice(null)
    setFailure(null)
    try {
      const result = await adminApi.deactivateUser(id)
      setNotice(result.detail)
      users.reload()
    } catch (cause) {
      setFailure(cause instanceof ApiError ? cause.message : 'That account could not be deactivated.')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Users and roles</h1>
        <p className="mt-1 text-sm text-stone-600">You are signed in as {myRole}.</p>
      </div>

      <Outcome notice={notice} failure={failure} />

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel title="Accounts">
          {users.loading ? (
            <Loading />
          ) : users.error ? (
            <LoadFailure error={users.error} requestId={users.requestId} />
          ) : (
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-stone-300 text-xs uppercase tracking-wide text-stone-500">
                  <th className="py-2 pr-3">Email</th>
                  <th className="py-2 pr-3">Role</th>
                  <th className="py-2" />
                </tr>
              </thead>
              <tbody>
                {users.data?.map((account) => (
                  <tr key={account.id} className="border-b border-stone-200">
                    <td className="py-2 pr-3">
                      {account.email}
                      {!account.is_active && (
                        <span className="block text-xs text-stone-500">deactivated</span>
                      )}
                      {account.id === me?.id && (
                        <span className="block text-xs text-stone-500">this account</span>
                      )}
                    </td>
                    <td className="py-2 pr-3">{account.role}</td>
                    <td className="py-2">
                      {account.is_active && account.id !== me?.id && (
                        <Action
                          disabled={busy === account.id}
                          tone="danger"
                          onClick={() => void deactivate(account.id, account.email)}
                        >
                          Deactivate
                        </Action>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>

        <Panel title="Create an account">
          <form className="space-y-3 text-sm" onSubmit={create}>
            <label className="block">
              <span className="font-medium text-stone-800">Email</span>
              <input
                type="email"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                className="mt-1 w-full rounded-md border border-stone-300 px-3 py-2"
              />
            </label>
            <label className="block">
              <span className="font-medium text-stone-800">Name</span>
              <input
                value={fullName}
                onChange={(event) => setFullName(event.target.value)}
                className="mt-1 w-full rounded-md border border-stone-300 px-3 py-2"
              />
            </label>
            <label className="block">
              <span className="font-medium text-stone-800">Password</span>
              <input
                type="password"
                required
                minLength={12}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className="mt-1 w-full rounded-md border border-stone-300 px-3 py-2"
              />
              <span className="mt-1 block text-xs text-stone-600">
                At least 12 characters. They can change it after signing in.
              </span>
            </label>
            <label className="block">
              <span className="font-medium text-stone-800">Role</span>
              <select
                value={newRole}
                onChange={(event) => setNewRole(event.target.value)}
                className="mt-1 w-full rounded-md border border-stone-300 px-3 py-2"
              >
                {(roles.data ?? []).map((role) => (
                  <option key={role.name} value={role.name}>
                    {role.name}
                    {role.name === 'VIEWER' ? ' — read only' : ''}
                  </option>
                ))}
              </select>
            </label>
            {formError && (
              <p role="alert" className="rounded-md border border-red-300 bg-red-50 p-2 text-sm text-red-900">
                {formError}
              </p>
            )}
            <button
              type="submit"
              disabled={busy === 'create'}
              className="rounded-md bg-stone-900 px-4 py-2 font-medium text-white disabled:opacity-50"
            >
              Create account
            </button>
          </form>
        </Panel>

        <Panel title="What each role may do">
          {roles.loading ? (
            <Loading />
          ) : roles.error ? (
            <LoadFailure error={roles.error} requestId={roles.requestId} />
          ) : roles.data?.length === 0 ? (
            <Empty>No roles were reported.</Empty>
          ) : (
            <ul className="space-y-3 text-sm">
              {roles.data?.map((role) => (
                <RoleCard key={role.name} role={role} />
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </div>
  )
}

function RoleCard({ role }: { role: RoleDefinition }) {
  return (
    <li>
      <p className="font-medium">{role.name}</p>
      {role.description && <p className="text-xs text-stone-600">{role.description}</p>}
      <p className="mt-1 flex flex-wrap gap-1">
        {role.permissions.includes('*') ? (
          <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs">every action</span>
        ) : (
          role.permissions.map((permission) => (
            <span key={permission} className="rounded bg-stone-100 px-1.5 py-0.5 font-mono text-xs">
              {permission}
            </span>
          ))
        )}
      </p>
    </li>
  )
}
