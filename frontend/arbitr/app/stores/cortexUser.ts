import { defineStore } from 'pinia'
import type { DevPerson } from '~/stores/decisionDesk'

const STORAGE = 'cortex.user'

export interface SignedIn {
  setKey: 'ja' | 'en'
  personId: string
  personKey: string
  label: string
  role: string
}

function read(): { user: SignedIn | null, devMode: boolean } {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE) ?? 'null') as { user?: SignedIn | null, devMode?: boolean } | null
    return { user: raw?.user ?? null, devMode: Boolean(raw?.devMode) }
  } catch {
    return { user: null, devMode: false }
  }
}

/**
 * Who is using the app (issue 54). PoC sign-in: a person chosen from the
 * synthetic seed, no password; in production this is IBM's single sign-on
 * (OIDC) and the identity comes from the session. Remembered in the browser.
 * Developer tools (acting as someone else, loading examples) stay hidden
 * unless switched on, so a demo shows the product, not the scaffolding.
 */
export const useCortexUserStore = defineStore('cortexUser', {
  state: () => ({ user: null as SignedIn | null, devMode: false, loaded: false }),
  actions: {
    load() {
      if (this.loaded) return
      const saved = read()
      this.user = saved.user
      this.devMode = saved.devMode
      this.loaded = true
    },
    save() {
      try {
        localStorage.setItem(STORAGE, JSON.stringify({ user: this.user, devMode: this.devMode }))
      } catch { /* remembering is a convenience */ }
    },
    signIn(setKey: 'ja' | 'en', person: DevPerson) {
      this.user = { setKey, personId: person.id, personKey: person.key, label: person.label, role: person.role }
      this.save()
    },
    signOut() {
      this.user = null
      this.save()
    },
    toggleDevMode() {
      this.devMode = !this.devMode
      this.save()
    }
  }
})
