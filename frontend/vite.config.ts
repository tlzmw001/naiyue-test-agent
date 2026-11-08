import { defineConfig, configDefaults } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  // 预打包 Radix，避免 dev server 运行时发现新依赖触发页面重载（会重置菜单等瞬态 UI）。
  optimizeDeps: { include: ['@radix-ui/react-dropdown-menu'] },
  test: {
    environment: 'jsdom',
    restoreMocks: true,
    // Radix 组件的 Popper/floating-ui 需要 ResizeObserver（jsdom 未实现），统一在 setup 提供空实现。
    setupFiles: ['./src/testDom.ts'],
    // e2e/ 下的 .spec.ts 由 Playwright 运行器收集（.spec 后缀命中 vitest 默认 include），
    // 必须排除，避免两套运行器重复收集同一批浏览器用例。
    exclude: [...configDefaults.exclude, 'e2e/**'],
  },
});
