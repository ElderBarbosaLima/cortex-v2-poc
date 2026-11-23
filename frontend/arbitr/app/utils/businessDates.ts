// Cortex stores business dates as midnight in Japan (JST, UTC+9), so a date
// shown or edited here is the JST calendar date (issue 38).
const JST_MS = 9 * 60 * 60 * 1000

/** The JST business date of an instant, or null for no bound (learned rules start "always", 1900). */
export function businessDate(iso: string | null | undefined): string | null {
  if (!iso) return null
  const ms = Date.parse(iso)
  if (Number.isNaN(ms)) return null
  const date = new Date(ms + JST_MS).toISOString().slice(0, 10)
  return date <= '1900-01-01' ? null : date
}
