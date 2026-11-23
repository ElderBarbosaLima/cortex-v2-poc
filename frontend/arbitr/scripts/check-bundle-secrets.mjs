// `npm run check:bundle`: build the browser bundle with a canary API key in the
// environment and prove the key appears nowhere in what is shipped to a browser
// (.output/public) — nor in the server output, since the key is read only at
// runtime. Fails loudly on any match.
import { execFileSync } from 'node:child_process'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

const CANARY = `canary-key-${process.pid}-${Date.now()}`
const ROOT = new URL('..', import.meta.url).pathname

execFileSync('npx', ['nuxt', 'build'], {
  cwd: ROOT,
  stdio: 'inherit',
  env: { ...process.env, NUXT_CORTEX_API_KEY: CANARY, CORTEX_DESK_API_KEY: CANARY, NUXT_TELEMETRY_DISABLED: '1' }
})

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    return statSync(path).isDirectory() ? files(path) : [path]
  })
}

const output = join(ROOT, '.output')
const scanned = files(output)
const leaks = scanned.filter(path => readFileSync(path).includes(CANARY))
if (leaks.length) {
  console.error(`the API key leaked into the build output:\n${leaks.join('\n')}`)
  process.exit(1)
}
const browser = scanned.filter(path => path.includes(`${join('.output', 'public')}`)).length
console.log(`check:bundle: the key is in none of ${scanned.length} built files (${browser} browser files)`)
