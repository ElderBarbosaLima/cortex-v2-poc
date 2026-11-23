// Dev proxy: /api/cortex/* → org-brain-service /v1/cortex/*, with the
// consumer API key added HERE, on the server. The key comes from
// runtimeConfig.cortexApiKey (NUXT_CORTEX_API_KEY, read at runtime) and never
// reaches the browser bundle. In production this hop is the API gateway's job.
import { CortexProxyError, upstreamHeaders, upstreamUrl } from '../../utils/cortexProxy'

export default defineEventHandler(async (event) => {
  const { cortexApiUrl, cortexApiKey } = useRuntimeConfig(event)
  try {
    const target = upstreamUrl(
      cortexApiUrl,
      getRouterParam(event, 'path') ?? '',
      getRequestURL(event).search
    )
    const headers = upstreamHeaders(getRequestHeaders(event), cortexApiKey)
    return await proxyRequest(event, target, {
      headers,
      fetchOptions: { redirect: 'manual' },
      onResponse: (e) => {
        removeResponseHeader(e, 'set-cookie')
      }
    })
  } catch (error) {
    if (error instanceof CortexProxyError) {
      setResponseStatus(event, error.status)
      return { error: error.status === 400 ? 'invalid_request' : 'proxy_unavailable', detail: error.message }
    }
    // Cortex unreachable: the API's error shape, never a stack trace.
    setResponseStatus(event, 502)
    return { error: 'cortex_unreachable', detail: 'the Cortex API did not answer' }
  }
})
