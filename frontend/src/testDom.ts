// Radix Popper/floating-ui 依赖 ResizeObserver，jsdom 未实现。
// 提供空操作实现，仅让组件在 jsdom 中可挂载；真实定位与尺寸行为由浏览器测试覆盖。
if (typeof globalThis.ResizeObserver === 'undefined') {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
}
