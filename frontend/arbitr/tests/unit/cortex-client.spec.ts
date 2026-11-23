import { afterEach, describe, expect, it, vi } from 'vitest'
import { CortexApiError, cortexRequest, cortexUrl, errorMessage } from '~/utils/api/client'
import { approveReviewCase, listReviewCases } from '~/utils/api/cortex'

function reply(status: number, body: unknown) {
  return vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }))
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the Cortex client', () => {
  it('calls the same-origin proxy and never sends an Authorization header', async () => {
    const fetchMock = reply(200, { items: [], next_cursor: null, policy_revision: null })
    vi.stubGlobal('fetch', fetchMock)

    await listReviewCases({ scope: 'node-1' }, 'user-1')

    const [url, init] = fetchMock.mock.calls[0]!
    expect(url).toBe('/api/cortex/review-cases?purpose=rule_review&scope=node-1')
    expect(Object.keys(init.headers)).not.toContain('authorization')
    expect(init.headers['x-end-user-id']).toBe('user-1')
  })

  it('posts a decision as JSON for the named person', async () => {
    const fetchMock = reply(200, { decision_id: 'd' })
    vi.stubGlobal('fetch', fetchMock)

    await approveReviewCase('case-1', { authority: 30, enforcement: 'mandatory' }, 'owner-1')

    const [url, init] = fetchMock.mock.calls[0]!
    expect(url).toBe('/api/cortex/review-cases/case-1/approve')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ authority: 30, enforcement: 'mandatory' })
  })

  it('turns the API error shape into a CortexApiError a person can read', async () => {
    vi.stubGlobal('fetch', reply(403, {
      error: 'access_denied',
      detail: 'separation of duties: a contributor to this record cannot decide on it',
      policy_revision: 'sha256:abc'
    }))

    const failure = await cortexRequest('GET', '/whoami').catch((e: unknown) => e)

    expect(failure).toBeInstanceOf(CortexApiError)
    expect(failure).toMatchObject({ status: 403, code: 'access_denied', policyRevision: 'sha256:abc' })
    expect((failure as Error).message).toContain('separation of duties')
  })

  it('reads a list of problems as one message', () => {
    expect(errorMessage('invalid_proposal', [{ code: 'invalid_condition', detail: 'bad CEL' }, { code: 'x' }], 422))
      .toBe('bad CEL (+1 more)')
    expect(errorMessage('not_found', null, 404)).toBe('not_found')
  })

  it('leaves undefined query values out', () => {
    expect(cortexUrl('/review-cases', { scope: 's', cursor: undefined })).toBe('/api/cortex/review-cases?scope=s')
  })
})
