import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

/**
 * 督战官（Duzhan Supervisor）配置台前端。
 * 独立可跑：静态页面由 Vite 提供，/api 反向代理到本机后端（默认 127.0.0.1:8767，与 pdca-workbench/.env.example 一致）。
 */
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: false,
    proxy: {
      '/api': {
        target: process.env.DUZHAN_API_TARGET || 'http://127.0.0.1:8767',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
