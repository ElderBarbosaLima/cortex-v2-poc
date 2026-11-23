import { defineVitestConfig } from '@nuxt/test-utils/config'

export default defineVitestConfig({
  test: {
    globals: true,
    environment: 'nuxt',
    hookTimeout: 30_000,
    include: ['tests/**/*.{spec,test}.ts']
  }
})
