import { readFile } from 'node:fs/promises'
import { devSeed } from '../../utils/devSeed'

export default defineEventHandler(async (event) => {
  const { devSeedFile } = useRuntimeConfig(event)
  if (!devSeedFile) {
    setResponseStatus(event, 404)
    return { error: 'not_found', detail: 'the dev seed is not configured' }
  }
  return devSeed(JSON.parse(await readFile(devSeedFile, 'utf8')))
})
