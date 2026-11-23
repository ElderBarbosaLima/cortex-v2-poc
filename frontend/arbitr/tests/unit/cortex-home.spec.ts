import { mountSuspended } from '@nuxt/test-utils/runtime'
import { afterEach, describe, expect, it, vi } from 'vitest'
import CortexHome from '~/pages/cortex/index.vue'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the Cortex home page', () => {
  it('says who the app is connected as', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      consumer_id: 'decision-desk', tenant_id: 't', end_user_id: null, scopes: ['cortex:read']
    }), { status: 200 })))

    const page = await mountSuspended(CortexHome)
    await vi.waitFor(() => expect(page.get('[data-testid="cortex-home-status-text"]').text())
      .toBe('Connected as decision-desk'))
  })

  it('offers a retry when Cortex is unavailable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: 'access_gate_unavailable', detail: 'policy engine unavailable'
    }), { status: 503 })))

    const page = await mountSuspended(CortexHome)
    await vi.waitFor(() => expect(page.find('[data-testid="cortex-home-retry-button"]').exists()).toBe(true))
    expect(page.get('[data-testid="cortex-home-status-text"]').text()).toContain('policy engine unavailable')
  })
})
