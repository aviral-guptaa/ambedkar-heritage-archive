import { useCallback, useEffect, useState } from 'react'
import { ApiError } from '../lib/adminApi'

/**
 * A small data-loading hook for the admin screens.
 *
 * Every admin page needs the same three things — the data, whether it is still
 * loading, and a message a curator can act on when it failed. Centralising that
 * is what keeps the pages down to the part that differs between them.
 */
export interface Loadable<T> {
  data: T | null
  error: string | null
  requestId: string | null
  loading: boolean
  reload: () => void
}

export function useLoader<T>(load: () => Promise<T>, deps: unknown[] = []): Loadable<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [requestId, setRequestId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [nonce, setNonce] = useState(0)

  const reload = useCallback(() => setNonce((value) => value + 1), [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    load()
      .then((result) => {
        if (!cancelled) setData(result)
      })
      .catch((cause: unknown) => {
        if (cancelled) return
        // A failed request is reported, never rendered as empty data: an
        // archive screen that silently shows nothing looks like a healthy
        // archive with no content.
        setError(cause instanceof ApiError ? cause.message : 'Could not reach the archive.')
        setRequestId(cause instanceof ApiError ? (cause.requestId ?? null) : null)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nonce, ...deps])

  return { data, error, requestId, loading, reload }
}
