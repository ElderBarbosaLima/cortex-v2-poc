<script setup lang="ts">
import { useCortexSessionStore } from '~/stores/cortexSession'
import { useCortexUserStore } from '~/stores/cortexUser'

const { t } = useI18n()
const session = useCortexSessionStore()
const user = useCortexUserStore()

useHead({ title: () => t('cortex.home.title') })
onMounted(() => {
  user.load()
  session.load()
})

const cards = [
  { key: 'desk', icon: 'i-lucide-scale' },
  { key: 'rules', icon: 'i-lucide-book-check' },
  { key: 'audit', icon: 'i-lucide-file-search' }
] as const
</script>

<template>
  <div class="mx-auto max-w-6xl px-4 py-12">
    <section class="max-w-3xl">
      <p class="text-caption font-semibold uppercase tracking-widest text-primary">
        arbitr · Cortex
      </p>
      <h1 class="mt-2 font-display text-4xl font-bold leading-tight text-highlighted">
        {{ t('cortex.home.title') }}
      </h1>
      <p class="mt-3 text-md text-muted">
        {{ t('cortex.home.intro') }}
      </p>
      <div class="mt-6 flex flex-wrap items-center gap-3">
        <UButton
          v-if="user.user"
          size="lg"
          to="/cortex/decision-desk"
          trailing-icon="i-lucide-arrow-right"
          data-test-id="cortex-home-decision-desk-link"
        >
          {{ t('cortex.home.openDesk') }}
        </UButton>
        <UButton
          v-else
          size="lg"
          to="/login"
          trailing-icon="i-lucide-arrow-right"
          data-test-id="cortex-home-signin-link"
        >
          {{ t('cortex.home.signIn') }}
        </UButton>
        <span
          v-if="session.loading"
          class="text-caption text-muted"
          data-test-id="cortex-home-status-text"
        >{{ t('cortex.home.connecting') }}</span>
        <span
          v-else-if="session.whoami"
          class="inline-flex items-center gap-1.5 text-caption text-muted"
          data-test-id="cortex-home-status-text"
        ><span class="size-2 rounded-full bg-success" />{{ t('cortex.home.connected', { consumer: session.whoami.consumer_id }) }}</span>
        <span
          v-else
          class="inline-flex items-center gap-2"
        >
          <span
            class="text-caption text-error"
            data-test-id="cortex-home-status-text"
          >{{ t('cortex.home.unavailable', { reason: session.error ?? '' }) }}</span>
          <UButton
            size="xs"
            color="neutral"
            variant="outline"
            data-test-id="cortex-home-retry-button"
            @click="session.load()"
          >
            {{ t('cortex.home.retry') }}
          </UButton>
        </span>
      </div>
    </section>

    <section class="mt-12 grid gap-4 md:grid-cols-3">
      <UCard
        v-for="card in cards"
        :key="card.key"
        class="rounded-xl"
      >
        <UIcon
          :name="card.icon"
          class="size-6 text-primary"
        />
        <h2 class="mt-3 font-display text-md font-semibold text-highlighted">
          {{ t(`cortex.home.cards.${card.key}.title`) }}
        </h2>
        <p class="mt-1 text-body text-muted">
          {{ t(`cortex.home.cards.${card.key}.body`) }}
        </p>
      </UCard>
    </section>
  </div>
</template>
