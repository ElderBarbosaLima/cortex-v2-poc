<script setup lang="ts">
import { useCortexSessionStore } from '~/stores/cortexSession'

const { t } = useI18n()
const session = useCortexSessionStore()

useHead({ title: () => t('cortex.home.title') })
onMounted(() => session.load())
</script>

<template>
  <main class="mx-auto max-w-3xl px-6 py-10">
    <h1 class="font-display text-h2 text-highlighted">
      {{ t('cortex.home.title') }}
    </h1>
    <p class="mt-2 text-body text-muted">
      {{ t('cortex.home.intro') }}
    </p>

    <UCard class="mt-6 rounded-xl">
      <p
        v-if="session.loading"
        class="text-body"
        data-test-id="cortex-home-status-text"
      >
        {{ t('cortex.home.connecting') }}
      </p>
      <p
        v-else-if="session.whoami"
        class="text-body"
        data-test-id="cortex-home-status-text"
      >
        {{ t('cortex.home.connected', { consumer: session.whoami.consumer_id }) }}
      </p>
      <div v-else>
        <p
          class="text-body text-error"
          data-test-id="cortex-home-status-text"
        >
          {{ t('cortex.home.unavailable', { reason: session.error ?? '' }) }}
        </p>
        <UButton
          class="mt-4"
          data-test-id="cortex-home-retry-button"
          @click="session.load()"
        >
          {{ t('cortex.home.retry') }}
        </UButton>
      </div>
    </UCard>
  </main>
</template>
