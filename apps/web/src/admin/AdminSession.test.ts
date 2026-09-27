import { describe, expect, it } from 'vitest'
import { roleGrants } from './AdminSession'

/**
 * The permission check decides which controls a curator is offered. Getting it
 * wrong is not a cosmetic fault: the first version of this file carried a
 * hand-written table of role names the backend has never used, so it would have
 * hidden actions from archivists and shown them to viewers.
 */
describe('roleGrants', () => {
  it('grants a permission the role lists', () => {
    expect(roleGrants(['document:read', 'document:publish'], 'document:publish')).toBe(true)
  })

  it('withholds a permission the role does not list', () => {
    expect(roleGrants(['document:read'], 'document:publish')).toBe(false)
    expect(roleGrants(['document:read'], 'audit:read')).toBe(false)
  })

  it('grants everything to an unrestricted role', () => {
    expect(roleGrants(['*'], 'document:publish')).toBe(true)
    expect(roleGrants(['*'], 'anything:at:all')).toBe(true)
  })

  it('grants nothing to a role with no permissions', () => {
    // This is the case that matters: a failed lookup must fail closed, so a
    // curator is never offered an action the server would refuse.
    expect(roleGrants([], 'document:read')).toBe(false)
  })

  it('does not treat a partial match as a grant', () => {
    expect(roleGrants(['document:read'], 'document')).toBe(false)
    expect(roleGrants(['ocr:approve'], 'ocr')).toBe(false)
  })

  it('matches the permissions the archive actually reports', () => {
    // Taken from GET /auth/roles rather than invented, so this test fails if
    // the backend's role definitions change.
    const viewer = ['document:read']
    const researcher = ['document:read', 'ocr:run', 'graph:write', 'job:read']
    const archivist = [
      'document:read',
      'document:write',
      'document:delete',
      'document:publish',
      'collection:write',
      'ocr:run',
      'ocr:approve',
      'graph:write',
      'graph:review',
      'media:write',
      'story:write',
      'job:read',
      'job:retry',
      'integrity:run',
      'audit:read',
      'user:read',
    ]

    // A viewer can read a record but must not be able to publish it, change it,
    // or read the audit log of who did.
    expect(roleGrants(viewer, 'document:read')).toBe(true)
    expect(roleGrants(viewer, 'document:publish')).toBe(false)
    expect(roleGrants(viewer, 'document:write')).toBe(false)
    expect(roleGrants(viewer, 'audit:read')).toBe(false)

    // A researcher may run OCR but must not approve their own reading of it,
    // because approval changes what the public archive states.
    expect(roleGrants(researcher, 'ocr:run')).toBe(true)
    expect(roleGrants(researcher, 'ocr:approve')).toBe(false)
    expect(roleGrants(researcher, 'graph:review')).toBe(false)

    expect(roleGrants(archivist, 'ocr:approve')).toBe(true)
    expect(roleGrants(archivist, 'document:publish')).toBe(true)
  })
})

/**
 * If the archive cannot be asked what a role may do, the interface must show
 * nothing rather than everything. The first version returned the whole role list
 * on failure, which would have offered every control to a curator whose lookup
 * had merely timed out.
 */
describe('permissionsFor', () => {
  it('returns the permissions the archive reports for a role', async () => {
    const { permissionsFor } = await import('./AdminSession')
    const original = fetch
    global.fetch = (async () =>
      new Response(
        JSON.stringify([
          { name: 'VIEWER', description: null, permissions: ['document:read'] },
          { name: 'ARCHIVIST', description: null, permissions: ['document:publish'] },
        ]),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      )) as typeof fetch
    try {
      expect(await permissionsFor('ARCHIVIST')).toEqual(['document:publish'])
      expect(await permissionsFor('NOBODY')).toEqual([])
    } finally {
      global.fetch = original
    }
  })

  it('fails closed when the archive cannot be reached', async () => {
    const { permissionsFor } = await import('./AdminSession')
    const original = fetch
    global.fetch = (async () => {
      throw new Error('network down')
    }) as typeof fetch
    try {
      expect(await permissionsFor('SUPER_ADMIN')).toEqual([])
    } finally {
      global.fetch = original
    }
  })

  it('fails closed when the response is not a list of roles', async () => {
    const { permissionsFor } = await import('./AdminSession')
    const original = fetch
    global.fetch = (async () =>
      new Response(JSON.stringify({ permissions: ['*'] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })) as typeof fetch
    try {
      // A malformed answer must not be treated as a grant.
      expect(await permissionsFor('SUPER_ADMIN')).toEqual([])
    } finally {
      global.fetch = original
    }
  })
})
