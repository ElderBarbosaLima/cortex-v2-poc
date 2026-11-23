<script setup lang="ts">
import { useCortexUserStore } from '~/stores/cortexUser'
import { useDecisionDeskStore } from '~/stores/decisionDesk'

definePageMeta({ middleware: 'auth' })

const { t } = useI18n()
const desk = useDecisionDeskStore()
const user = useCortexUserStore()

useHead({ title: () => t('cortex.desk.title') })

async function reload() {
  desk.resetView()
  await desk.loadCases()
}

onMounted(async () => {
  user.load()
  await desk.loadSeed()
  if (user.user) desk.signedInAs(user.user.setKey, user.user.personId)
  await desk.loadCases()
})
</script>

<template>
  <div class="mx-auto flex max-w-6xl flex-col gap-6 px-4 py-8">
    <header class="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 class="font-display text-h2 text-highlighted">
          {{ t('cortex.desk.title') }}
        </h1>
        <p class="max-w-2xl text-body text-muted">
          {{ t('cortex.desk.intro') }}
        </p>
      </div>
      <CortexDecisionDeskToolbar @change="reload" />
    </header>

    <CortexDecisionDeskDevTools
      v-if="user.devMode"
      @change="reload"
    />

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
      class="rounded-lg border border-default bg-default p-3 text-body"
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
    <UAlert
      v-if="desk.notice"
      color="success"
      variant="subtle"
      icon="i-lucide-check-circle"
      :title="t(`cortex.desk.notice.${desk.notice}`)"
      role="status"
      data-test-id="cortex-decision-desk-notice-text"
    />

    <div class="grid gap-6 lg:grid-cols-[minmax(18rem,1fr)_2fr]">
      <div class="flex flex-col gap-6">
        <CortexDecisionDeskCaseList />
        <CortexDecisionDeskExpiring />
      </div>
      <CortexDecisionDeskCaseDetail
        v-if="desk.selected"
        :detail="desk.selected"
      />
      <div
        v-else
        class="grid min-h-64 place-items-center rounded-xl border border-dashed border-default bg-default p-8 text-center"
      >
        <p class="text-body text-muted">
          <UIcon
            name="i-lucide-mouse-pointer-click"
            class="mb-2 block size-6 justify-self-center"
          />
          {{ t('cortex.desk.pick') }}
        </p>
      </div>
    </div>
  </div>
</template>
