import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { EXAMPLE_KEYS, type ExampleSet } from '../../../utils/examples'

// The example sets the Desk can switch between (issue 39). Dev-only.
export default defineEventHandler(async (event) => {
  const { devExamplesDir, devSeedFile } = useRuntimeConfig(event)
  if (!devExamplesDir || !devSeedFile) {
    setResponseStatus(event, 404)
    return { error: 'not_found', detail: 'the example sets are not configured' }
  }
  const nodes = (JSON.parse(await readFile(devSeedFile, 'utf8')) as { nodes: Array<{ key: string, obj_id: string }> }).nodes
  const sets = await Promise.all(EXAMPLE_KEYS.map(async key =>
    JSON.parse(await readFile(join(devExamplesDir, `${key}.json`), 'utf8')) as ExampleSet))
  return {
    sets: sets.map(s => ({
      key: s.key,
      label: s.label,
      note: s.note,
      tenantId: nodes.find(n => n.key === s.tenant)?.obj_id ?? '',
      scopeId: nodes.find(n => n.key === s.scope)?.obj_id ?? ''
    }))
  }
})
