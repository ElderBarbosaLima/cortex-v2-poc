import { defineStore } from 'pinia'
import { CortexApiError } from '~/utils/api/client'
import { getWhoAmI, type CortexWhoAmI } from '~/utils/api/cortex'

/** Who the app is talking to Cortex as (the consumer behind the dev proxy). */
export const useCortexSessionStore = defineStore('cortexSession', {
  state: () => ({
    whoami: null as CortexWhoAmI | null,
    error: null as string | null,
    loading: false
  }),
  actions: {
    async load() {
      this.loading = true
      this.error = null
      try {
        this.whoami = await getWhoAmI()
      } catch (error) {
        this.whoami = null
        this.error = error instanceof CortexApiError ? error.message : String(error)
      } finally {
        this.loading = false
      }
    }
  }
})
