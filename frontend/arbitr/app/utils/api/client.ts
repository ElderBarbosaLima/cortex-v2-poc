// The browser's HTTP client for Cortex. Requests go to the same-origin proxy
// (/api/cortex), which adds the API key on the server: this file never sets
// an Authorization header. Errors keep the API's one shape, {error, detail}.
import { z } from 'zod'

export const CORTEX_API_BASE = '/api/cortex'

/**
 * The example set the Desk is showing (issue 39): sent as X-Cortex-Examples, so
 * the dev proxy uses that set's tenant key. Never an identity: the proxy maps
 * only the known values to keys it holds, and never forwards the header.
 */
let examplesSet: 'ja' | 'en' = 'ja'
export function setExamplesSet(key: 'ja' | 'en') {
  examplesSet = key
}

const ProblemItem = z.object({ code: z.string().optional(), detail: z.string().optional(), msg: z.string().optional() })
const ErrorBody = z.looseObject({
  error: z.string(),
  detail: z.union([z.string(), z.array(ProblemItem), z.null()]).optional(),
  policy_revision: z.string().nullable().optional()
})
export type CortexErrorBody = z.infer<typeof ErrorBody>

export class CortexApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly detail: CortexErrorBody['detail'],
    readonly policyRevision: string | null = null
  ) {
    super(errorMessage(code, detail, status))
    this.name = 'CortexApiError'
  }
}

/** A message a person can act on, from a string detail or a list of problems. */
export function errorMessage(code: string, detail: CortexErrorBody['detail'], status: number): string {
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0]!
    const text = first.detail ?? first.msg ?? first.code ?? code
    return detail.length > 1 ? `${text} (+${detail.length - 1} more)` : text
  }
  return code || `Request failed: ${status}`
}

export interface CortexRequest {
  query?: Record<string, string | number | undefined>
  body?: unknown
  /** The person acting (X-End-User-Id); approvals need one. */
  endUser?: string
  signal?: AbortSignal
}

export function cortexUrl(path: string, query?: CortexRequest['query']): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined) params.set(key, String(value))
  }
  const search = params.toString()
  return `${CORTEX_API_BASE}${path}${search ? `?${search}` : ''}`
}

export async function cortexRequest<T>(method: 'GET' | 'POST', path: string, options: CortexRequest = {}): Promise<T> {
  const headers: Record<string, string> = { accept: 'application/json' }
  if (options.body !== undefined) headers['content-type'] = 'application/json'
  if (options.endUser) headers['x-end-user-id'] = options.endUser
  headers['x-cortex-examples'] = examplesSet
  const response = await fetch(cortexUrl(path, options.query), {
    method,
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: options.signal,
    credentials: 'same-origin'
  })
  const data: unknown = response.status === 204 ? null : await response.json().catch(() => null)
  if (!response.ok) {
    const parsed = ErrorBody.safeParse(data)
    if (parsed.success) {
      throw new CortexApiError(response.status, parsed.data.error, parsed.data.detail, parsed.data.policy_revision ?? null)
    }
    throw new CortexApiError(response.status, 'unexpected_response', null)
  }
  return data as T
}
