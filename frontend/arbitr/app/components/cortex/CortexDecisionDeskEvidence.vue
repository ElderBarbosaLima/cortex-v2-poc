<script setup lang="ts">
import { useDecisionDeskStore } from '~/stores/decisionDesk'
import type { CortexCaseEvidence } from '~/utils/api/cortex'
import { businessDate } from '~/utils/businessDates'

const props = defineProps<{ evidence: CortexCaseEvidence[] }>()
const { t } = useI18n()
const desk = useDecisionDeskStore()

type Json = Record<string, unknown>

function note(e: CortexCaseEvidence): string {
  const c = (e.correction ?? {}) as Json
  return typeof c.note_ja === 'string' ? c.note_ja : ''
}

/** Corrections that list changes (invoices) or a single target (simple packs). */
function changes(e: CortexCaseEvidence): Array<{ target: string, before: string, after: string }> {
  const c = (e.correction ?? {}) as Json
  const list = Array.isArray(c.changes) ? (c.changes as Json[]) : (typeof c.target === 'string' ? [c] : [])
  return list.map(ch => ({
    target: String(ch.target ?? ''),
    before: ch.before == null ? '—' : String(ch.before),
    after: ch.after == null ? '—' : String(ch.after)
  }))
}

/** A payment's resolution (cash application): the exception, the result, the invoices chosen. */
function outcome(e: CortexCaseEvidence): string[] {
  const c = (e.correction ?? {}) as Json
  const out: string[] = []
  if (typeof c.exception === 'string') out.push(t('cortex.desk.evidence.exception', { exception: c.exception }))
  if (typeof c.result === 'string') out.push(t(`cortex.desk.evidence.result.${c.result === 'confirmed' ? 'confirmed' : 'corrected'}`))
  if (Array.isArray(c.selected_invoices) && c.selected_invoices.length) {
    out.push(t('cortex.desk.evidence.selected', { list: (c.selected_invoices as string[]).join(', ') }))
  }
  return out
}

/** The case's facts a person reads: top-level values, and one level down (payment). */
function facts(e: CortexCaseEvidence): Array<[string, string]> {
  const rows: Array<[string, string]> = []
  for (const [k, v] of Object.entries((e.case ?? {}) as Json)) {
    if (v === null || v === undefined) continue
    if (typeof v === 'object' && !Array.isArray(v)) {
      for (const [k2, v2] of Object.entries(v as Json)) {
        if (v2 !== null && typeof v2 !== 'object') rows.push([`${k}.${k2}`, String(v2)])
      }
    } else if (Array.isArray(v)) {
      rows.push([k, t('cortex.desk.evidence.items', { count: v.length })])
    } else {
      rows.push([k, String(v)])
    }
  }
  return rows
}

const items = computed(() => props.evidence)
</script>

<template>
  <section
    v-if="items.length"
    class="mt-4 border-t border-default pt-4"
    :aria-label="t('cortex.desk.evidence.title')"
    data-test-id="cortex-decision-desk-evidence-panel"
  >
    <h4 class="text-caption font-semibold uppercase tracking-wide text-muted">
      {{ t('cortex.desk.evidence.title') }}
    </h4>
    <article
      v-for="e in items"
      :key="e.decision_record_id"
      class="mt-2 rounded-lg bg-muted/60 p-3"
    >
      <p class="text-caption text-muted">
        {{ t('cortex.desk.evidence.from', { case: e.case_ref ?? '—', person: e.decided_by ? desk.labelFor(e.decided_by) : '—', date: businessDate(e.decided_at) ?? '' }) }}
      </p>
      <blockquote
        v-if="note(e)"
        class="mt-2 border-l-2 border-primary pl-3 text-body text-highlighted"
        data-test-id="cortex-decision-desk-evidence-note-text"
      >
        {{ note(e) }}
      </blockquote>
      <ul
        v-if="changes(e).length"
        class="mt-2 flex flex-col gap-1 text-body"
      >
        <li
          v-for="c in changes(e)"
          :key="c.target"
        >
          <code class="font-mono text-caption">{{ c.target }}</code>:
          <span class="text-muted line-through">{{ c.before }}</span>
          → <span class="text-highlighted">{{ c.after }}</span>
        </li>
      </ul>
      <p
        v-for="line in outcome(e)"
        :key="line"
        class="mt-1 text-body"
      >
        {{ line }}
      </p>
      <details
        v-if="e.case"
        class="mt-2"
      >
        <summary class="cursor-pointer text-caption text-primary">
          {{ t('cortex.desk.evidence.case', { case: e.case_ref ?? '' }) }}
        </summary>
        <dl class="mt-2 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-0.5 text-caption">
          <template
            v-for="[k, v] in facts(e)"
            :key="k"
          >
            <dt class="font-mono text-muted">
              {{ k }}
            </dt>
            <dd class="text-highlighted">
              {{ v }}
            </dd>
          </template>
        </dl>
      </details>
    </article>
  </section>
</template>
