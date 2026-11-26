import Aura from '@primeuix/themes/aura'

// Single-page app. In development the API is proxied to the engine on :8765;
// in production FastAPI serves the generated files (.output/public) itself.
export default defineNuxtConfig({
  ssr: false,
  compatibilityDate: '2025-01-01',
  devtools: { enabled: false },
  modules: ['@primevue/nuxt-module'],
  components: [{ path: '~/components', pathPrefix: false }],
  css: ['primeicons/primeicons.css', '~/assets/main.css'],
  primevue: {
    options: {
      ripple: false,
      theme: { preset: Aura, options: { darkModeSelector: '.app-dark', cssLayer: false } },
    },
  },
  app: {
    head: {
      title: 'Coco',
      meta: [{ name: 'viewport', content: 'width=device-width, initial-scale=1' }],
      link: [{ rel: 'icon', href: 'data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 16 16%22><circle cx=%228%22 cy=%228%22 r=%227%22 fill=%22%2310b981%22/></svg>' }],
    },
  },
  nitro: {
    devProxy: {
      '/api': { target: 'http://127.0.0.1:8765/api', changeOrigin: true },
    },
  } as Record<string, unknown>,
  typescript: { strict: true },
})
