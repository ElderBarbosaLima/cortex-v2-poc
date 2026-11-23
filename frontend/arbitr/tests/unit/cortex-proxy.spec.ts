import { describe, expect, it } from 'vitest'
import { CortexProxyError, upstreamHeaders, upstreamUrl } from '../../server/utils/cortexProxy'

describe('the Cortex dev proxy', () => {
  it('always targets /v1/cortex and keeps the query', () => {
    expect(upstreamUrl('http://api:5008/', 'review-cases/abc', '?purpose=rule_review'))
      .toBe('http://api:5008/v1/cortex/review-cases/abc?purpose=rule_review')
  })

  it('refuses a path that climbs out of /v1/cortex, encoded or not', () => {
    for (const path of ['../admin', 'a/./b', '..%2F..%2Fhealth', '%2E%2E', 'a%2Fb', 'a%5Cb', '%E0%A4%A']) {
      expect(() => upstreamUrl('http://api:5008', path, ''), path).toThrow(CortexProxyError)
    }
  })

  it('forwards an encoded id segment as it was sent', () => {
    expect(upstreamUrl('http://api:5008', 'review-cases/%E6%A1%88', ''))
      .toBe('http://api:5008/v1/cortex/review-cases/%E6%A1%88')
  })

  it('adds the server key and drops what the browser sends as identity', () => {
    const headers = upstreamHeaders(
      {
        'authorization': 'Bearer stolen-from-the-browser',
        'cookie': 'session=1',
        'x-end-user-id': '62f15719-9a62-5783-966e-9b69a1a14dde',
        'content-type': 'application/json'
      },
      'server-side-key'
    )

    expect(headers).toEqual({
      'authorization': 'Bearer server-side-key',
      'x-end-user-id': '62f15719-9a62-5783-966e-9b69a1a14dde',
      'content-type': 'application/json'
    })
  })

  it('fails closed without a configured key or URL', () => {
    expect(() => upstreamHeaders({}, '')).toThrow('not configured')
    expect(() => upstreamUrl('', 'whoami', '')).toThrow('not configured')
  })
})
