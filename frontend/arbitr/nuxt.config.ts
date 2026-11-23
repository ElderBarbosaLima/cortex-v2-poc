// https://nuxt.com/docs/api/configuration/nuxt-config
// The PoC's Nuxt 4 app, shaped like PROD:frontend/arbitr (discovery 2.3):
// ssr:false, Nuxt UI, Tailwind 4, Pinia, i18n (en), the brand v2 tokens and the
// v2 style guard. Cortex screens live under app/pages/cortex/.
import { fileURLToPath } from 'node:url'

export default defineNuxtConfig({
  modules: ['@nuxt/eslint', '@nuxt/ui', '@nuxtjs/i18n', '@pinia/nuxt'],
  ssr: false,

  devtools: { enabled: false },

  app: {
    head: {
      meta: [{ name: 'color-scheme', content: 'only light' }]
    }
  },

  css: ['~/assets/css/main.css'],

  ui: {
    colorMode: false,
    // No font downloads (Nuxt UI would fetch them from a font CDN): the CSS
    // stacks in main.css fall back to system fonts.
    fonts: false
  },

  runtimeConfig: {
    // Server-only (never `public`): read by the Nitro proxy in server/api/cortex.
    // Both are left empty here and set at RUNTIME from NUXT_CORTEX_API_URL and
    // NUXT_CORTEX_API_KEY, so a key in the build environment is never baked
    // into any output, server or browser (checked by `npm run check:bundle`).
    cortexApiUrl: '',
    cortexApiKey: '',
    // Dev-only: the seed file behind the Decision Desk's user selector
    // (NUXT_DEV_SEED_FILE). Empty: /api/dev/seed answers 404.
    devSeedFile: ''
  },

  // The generated contract (libraries/cortex-contract, D15): the only source of
  // Cortex API types in this app.
  alias: {
    '#cortex-contract': fileURLToPath(new URL('../../libraries/cortex-contract/ts', import.meta.url))
  },

  devServer: { host: '0.0.0.0', port: 5001 },

  compatibilityDate: '2025-01-15',

  // Bind mounts from the host (Colima, Docker Desktop) deliver no file events
  // into the container, so the dev container polls (NUXT_DEV_POLL=true).
  vite: {
    server: {
      watch: { usePolling: process.env.NUXT_DEV_POLL === 'true', interval: 300 }
    }
  },
  telemetry: false,

  eslint: {
    config: {
      stylistic: {
        commaDangle: 'never',
        braceStyle: '1tbs'
      }
    }
  },

  i18n: {
    strategy: 'no_prefix',
    defaultLocale: 'en',
    locales: [{ code: 'en', name: 'English', files: ['en/common.json', 'en/cortex.json'] }],
    langDir: 'locales',
    detectBrowserLanguage: false
  },

  icon: {
    // Icons come from the installed @iconify-json collections, never from the
    // public Iconify API at runtime.
    serverBundle: { collections: ['lucide'] },
    clientBundle: { scan: true }
  }
})
