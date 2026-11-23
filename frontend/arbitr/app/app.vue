<script setup lang="ts">
const { t } = useI18n()

useHead({
  titleTemplate: (title?: string) => (title ? `${title} | arbitr` : 'arbitr'),
  meta: [{ name: 'viewport', content: 'width=device-width, initial-scale=1' }],
  htmlAttrs: { lang: 'en' }
})
useSeoMeta({ description: () => t('common.description') })
</script>

<template>
  <UApp>
    <NuxtPage />
  </UApp>
</template>
