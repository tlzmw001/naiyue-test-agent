/** jsdom models modal visibility here; native focus and Escape are browser checked. */
export function installDialogMock() {
  const show = Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, 'showModal');
  const close = Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, 'close');
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value() { this.setAttribute('open', ''); } });
  Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value() { this.removeAttribute('open'); } });
  return () => {
    if (show) Object.defineProperty(HTMLDialogElement.prototype, 'showModal', show); else Reflect.deleteProperty(HTMLDialogElement.prototype, 'showModal');
    if (close) Object.defineProperty(HTMLDialogElement.prototype, 'close', close); else Reflect.deleteProperty(HTMLDialogElement.prototype, 'close');
  };
}
