import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import {
  adminApi,
  ApiError,
  clearTokens,
  login as apiLogin,
  readTokens,
  type AdminUser,
} from '../lib/adminApi'

/**
 * Who is signed in, and what they may do.
 *
 * Permissions are read from the signed-in user so the interface can hide an
 * action a role cannot perform. That is a convenience, not a security control:
 * every endpoint checks the permission again on the server.
 */
interface AdminSession {
  user: AdminUser | null
  checking: boolean
  signIn: (email: string, password: string) => Promise<void>
  signOut: () => void
  can: (permission: string) => boolean
  role: string | null
  /** The signed-in role's permissions, exactly as the archive reports them. */
  permissions: string[]
}

const SessionContext = createContext<AdminSession | null>(null)

/**
 * Whether a role grants a permission.
 *
 * A '*' entry means the role is unrestricted. Anything the archive does not
 * recognise grants nothing: an unknown role must hide controls rather than
 * reveal them, since the server would refuse the action anyway.
 */
export function roleGrants(permissions: string[], permission: string): boolean {
  return permissions.includes('*') || permissions.includes(permission)
}

export function AdminSessionProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AdminUser | null>(null)
  const [permissions, setPermissions] = useState<string[]>([])
  const [checking, setChecking] = useState(true)

  useEffect(() => {
    // An existing token survives a page refresh, so the identity is confirmed
    // rather than assumed from storage.
    let cancelled = false
    async function resolve() {
      if (!readTokens().access) {
        if (!cancelled) setChecking(false)
        return
      }
      try {
        const me = await adminApi.me()
        if (cancelled) return
        setUser(me)
        setPermissions(await permissionsFor(me.role))
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) clearTokens()
      } finally {
        if (!cancelled) setChecking(false)
      }
    }
    void resolve()
    return () => {
      cancelled = true
    }
  }, [])

  const signIn = useCallback(async (email: string, password: string) => {
    const signedIn = await apiLogin(email, password)
    setUser(signedIn)
    setPermissions(await permissionsFor(signedIn.role))
  }, [])

  const signOut = useCallback(() => {
    clearTokens()
    setUser(null)
    setPermissions([])
  }, [])

  const value = useMemo<AdminSession>(
    () => ({
      user,
      checking,
      signIn,
      signOut,
      can: (permission: string) => roleGrants(permissions, permission),
      role: user?.role ?? null,
      permissions,
    }),
    [user, permissions, checking, signIn, signOut],
  )

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

/**
 * Look up a role's permissions from the archive rather than restating them here.
 *
 * A hand-written copy of this table is exactly the sort of thing that drifts:
 * the first version of this file invented role names that the backend has never
 * used, which would have hidden controls for the wrong people and shown them to
 * the wrong ones. The server is the only authority.
 */
export async function permissionsFor(role: string): Promise<string[]> {
  try {
    const roles = await adminApi.roles()
    return roles.find((entry) => entry.name === role)?.permissions ?? []
  } catch {
    // Failing closed: no permissions means no buttons, and every endpoint
    // checks again regardless.
    return []
  }
}

export function useAdminSession(): AdminSession {
  const context = useContext(SessionContext)
  if (!context) throw new Error('useAdminSession must be used inside AdminSessionProvider')
  return context
}
