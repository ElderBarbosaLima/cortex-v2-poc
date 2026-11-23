import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { upstreamUrl } from '../../../utils/cortexProxy'
import { exampleKey, loadExampleSet, type CortexCall, type ExampleSet } from '../../../utils/examples'

// "Load example cases" (issue 39): runs the chosen set through the public API
// with that set's agent and Desk keys, read here on the server. Dev-only.
export default defineEventHandler(async (event) => {
  const config = useRuntimeConfig(event)
  if (!config.devExamplesDir || !config.devSeedFile) {
    setResponseStatus(event, 404)
    return { error: 'not_found', detail: 'the example sets are not configured' }
  }
  const key = exampleKey((await readBody<{ set?: unknown }>(event))?.set)
  const keys = {
    agent: key === 'en' ? config.cortexExamplesAgentKeyEn : config.cortexExamplesAgentKeyJa,
    desk: key === 'en' ? config.cortexApiKeyEn : config.cortexApiKey
  }
  if (!keys.agent || !keys.desk) {
    setResponseStatus(event, 503)
    return { error: 'proxy_unavailable', detail: `the ${key} example keys are not configured (make init-env, then make dev)` }
  }
  const set = JSON.parse(await readFile(join(config.devExamplesDir, `${key}.json`), 'utf8')) as ExampleSet
  const seed = JSON.parse(await readFile(config.devSeedFile, 'utf8')) as {
    nodes: Array<{ key: string, obj_id: string }>
    people: Array<{ key: string, obj_id: string }>
  }
  const ids = {
    node: (k: string) => seed.nodes.find(n => n.key === k)?.obj_id ?? '',
    person: (k: string) => seed.people.find(p => p.key === k)?.obj_id ?? ''
  }
  const call: CortexCall = async (method, path, options) => {
    const headers: Record<string, string> = { 'authorization': `Bearer ${keys[options.as]}`, 'content-type': 'application/json' }
    if (options.user) headers['x-end-user-id'] = options.user
    const response = await fetch(upstreamUrl(config.cortexApiUrl, path, ''), {
      method,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body)
    })
    return { status: response.status, body: await response.json().catch(() => null) }
  }
  return { set: key, steps: await loadExampleSet(set, ids, call) }
})
