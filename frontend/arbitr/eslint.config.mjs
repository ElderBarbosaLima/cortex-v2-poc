// @ts-check
// Same base as PROD:frontend/arbitr/eslint.config.mjs (@nuxt/eslint + the
// vuejs-accessibility recommended rules), without its file-scoped deferrals:
// nothing here is deferred.
import withNuxt from './.nuxt/eslint.config.mjs'
import vuejsAccessibility from 'eslint-plugin-vuejs-accessibility'

export default withNuxt(
  {
    files: ['**/*.vue'],
    plugins: {
      'vuejs-accessibility': vuejsAccessibility
    },
    rules: {
      ...vuejsAccessibility.configs['flat/recommended'][1].rules
    }
  }
)
