import { mountSuspended } from '@nuxt/test-utils/runtime'
import { flushPromises } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import Login from '~/pages/login.vue'
import { useCortexUserStore } from '~/stores/cortexUser'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('sign-in (issue 54)', () => {
  it('signs in as a person of the chosen company, with no password', async () => {
    localStorage.clear()
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url === '/api/dev/seed') {
        return new Response(JSON.stringify({
          people: [
            { key: 'R-001', label: '請求書レビュアー 1', role: 'reviewer', id: 'r1', tenantId: 't-jp' },
            { key: 'OWN-001', label: 'ルール責任者', role: 'final_validator', id: 'o1', tenantId: 't-jp' },
            { key: 'EN-OWN-001', label: 'Rule owner', role: 'final_validator', id: 'e1', tenantId: 't-en' }
          ],
          scopes: [{ key: 'jp', name: '架空グループ日本', id: 't-jp', type: 'tenant', tenantId: 't-jp' },
            { key: 'en', name: 'Fictitious Group UK (example)', id: 't-en', type: 'tenant', tenantId: 't-en' }]
        }))
      }
      return new Response(JSON.stringify({ sets: [
        { key: 'ja', label: '日本語 (Japanese)', note: '', tenantId: 't-jp', scopeId: 's' },
        { key: 'en', label: 'English', note: '', tenantId: 't-en', scopeId: 's' }
      ] }))
    }))
    const page = await mountSuspended(Login)
    await vi.waitFor(() => expect(page.findAll('[data-test-id="cortex-login-person-input"]').length).toBe(2))

    expect(page.text()).toContain('架空グループ日本')
    expect(page.text()).not.toContain('password:')
    await page.get('[data-test-id="cortex-login-company-en-input"]').setValue(true)
    await flushPromises()
    expect(page.findAll('[data-test-id="cortex-login-person-input"]').length).toBe(1)
    await page.get('form').trigger('submit')
    await flushPromises()

    expect(useCortexUserStore().user).toMatchObject({ setKey: 'en', personId: 'e1', personKey: 'EN-OWN-001' })
  })
})
