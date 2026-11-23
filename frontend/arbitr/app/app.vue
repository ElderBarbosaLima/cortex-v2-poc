<script setup lang="ts">
import { useCortexUserStore } from '~/stores/cortexUser'

const { t, locale, setLocale } = useI18n()
const user = useCortexUserStore()

useHead({
  titleTemplate: (title?: string) => (title ? `${title} | Cortex` : 'Cortex'),
  meta: [{ name: 'viewport', content: 'width=device-width, initial-scale=1' }],
  htmlAttrs: { lang: () => locale.value }
})
useSeoMeta({ description: () => t('common.description') })

onMounted(async () => {
  user.load()
  try {
    const saved = localStorage.getItem('cortex.locale')
    if (saved === 'ja' || saved === 'en') await setLocale(saved)
  } catch { /* the default stays */ }
})
</script>

<template>
  <UApp>
    <div class="flex min-h-screen flex-col bg-muted/40">
      <AppHeader />
      <main class="flex-1">
        <NuxtPage />
      </main>
      <footer class="border-t border-default py-4 text-center text-2xs text-muted">
        {{ t('cortex.shell.footer') }}
      </footer>
    </div>
  </UApp>
</template>
