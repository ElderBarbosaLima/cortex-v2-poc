// Pure helpers for the Cortex dev proxy (server/api/cortex/[...path].ts), kept
// free of h3 so they are unit-tested directly.

/** Client headers the proxy forwards to org-brain-service; everything else is dropped. */
const FORWARDED = ['accept', 'content-type', 'x-end-user-id', 'x-request-id']

export class CortexProxyError extends Error {
  constructor(readonly status: number, message: string) {
    super(message)
  }
}

/** The upstream URL for `/api/cortex/<path>?<query>`: always under /v1/cortex. */
export function upstreamUrl(base: string, path: string, search: string): string {
  if (!base) throw new CortexProxyError(503, 'the Cortex API URL is not configured')
  const segments = path.split('/').filter(Boolean)
  // Judge each segment as the API will read it: an encoded `..`, `/` or `\\`
  // must not climb out of /v1/cortex either. Segments are forwarded as sent.
  for (const segment of segments) {
    let decoded: string
    try {
      decoded = decodeURIComponent(segment)
    } catch {
      throw new CortexProxyError(400, 'invalid path')
    }
    if (decoded === '..' || decoded === '.' || /[/\\]/.test(decoded)) {
      throw new CortexProxyError(400, 'invalid path')
    }
  }
  return `${base.replace(/\/+$/, '')}/v1/cortex/${segments.join('/')}${search}`
}

/**
 * Headers for the upstream request: the server's API key as the bearer, and
 * only the allow-listed client headers. A browser-sent Authorization or Cookie
 * never reaches the API, so the key is the only identity the API sees.
 */
export function upstreamHeaders(
  incoming: Record<string, string | string[] | undefined>,
  apiKey: string
): Record<string, string> {
  if (!apiKey) throw new CortexProxyError(503, 'the Cortex API key is not configured')
  const headers: Record<string, string> = { authorization: `Bearer ${apiKey}` }
  for (const name of FORWARDED) {
    const value = incoming[name]
    if (typeof value === 'string' && value) headers[name] = value
  }
  return headers
}
