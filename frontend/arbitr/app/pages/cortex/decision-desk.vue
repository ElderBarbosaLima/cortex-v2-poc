<script setup lang="ts">
import { useDecisionDeskStore } from '~/stores/decisionDesk'

const { t } = useI18n()
const desk = useDecisionDeskStore()

useHead({ title: () => t('cortex.desk.title') })

async function reload() {
  desk.resetView()
  await desk.loadCases()
}

onMounted(async () => {
  await desk.loadSeed()
  await desk.loadCases()
})
</script>

<template>
  <main class="mx-auto flex max-w-6xl flex-col gap-6 px-6 py-8">
    <header>
      <h1 class="font-display text-h2 text-highlighted">
        {{ t('cortex.desk.title') }}
      </h1>
      <p class="text-body text-muted">
        {{ t('cortex.desk.intro') }}
      </p>
    </header>

    <CortexDecisionDeskToolbar @change="reload" />

    <UAlert
      v-if="desk.problem"
      color="error"
      variant="subtle"
      :title="t('cortex.desk.problem', { code: desk.problem.code })"
      :description="desk.problem.message"
      data-test-id="cortex-decision-desk-problem-alert"
    />
    <div
      v-if="desk.bulkResult"
      class="text-body"
      role="status"
      data-test-id="cortex-decision-desk-bulk-result-text"
    >
      <p class="text-success">
        {{ t('cortex.desk.bulk.done', { count: desk.bulkResult.approved }) }}
      </p>
      <p
        v-for="item in desk.bulkResult.refused"
        :key="item.title"
        class="text-error"
      >
        {{ t('cortex.desk.bulk.refused', { title: item.title, reason: item.reason }) }}
      </p>
    </div>
    <p
      v-if="desk.notice"
      class="text-body text-success"
      role="status"
      data-test-id="cortex-decision-desk-notice-text"
    >
      {{ t(`cortex.desk.notice.${desk.notice}`) }}
    </p>

    <div class="grid gap-6 md:grid-cols-[minmax(16rem,1fr)_2fr]">
      <div class="flex flex-col gap-6">
        <CortexDecisionDeskCaseList />
        <CortexDecisionDeskExpiring />
      </div>
      <CortexDecisionDeskCaseDetail
        v-if="desk.selected"
        :detail="desk.selected"
      />
      <p
        v-else
        class="text-body text-muted"
      >
        {{ t('cortex.desk.pick') }}
      </p>
    </div>
  </main>
</template>
