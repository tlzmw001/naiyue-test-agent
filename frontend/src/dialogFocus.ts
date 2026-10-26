import type { KeyboardEvent } from 'react';

/** Keep modal edge navigation consistent, including native dialog on macOS. */
export function keepDialogFocus(event: KeyboardEvent, root: HTMLElement | null) {
  if (event.key !== 'Tab' || !root) return;
  // Walk DOM order explicitly; some DOM implementations group comma selectors.
  const items = Array.from(root.querySelectorAll<HTMLElement>('*')).filter(element => {
    if (!element.matches('button, input, select, textarea, a[href], summary, [tabindex]') ||
        element.matches(':disabled, [type="hidden"]') || element.closest('[hidden], [inert], [aria-hidden="true"]') ||
        element.tabIndex < 0 && !element.matches('summary')) return false;
    for (let parent = element.parentElement; parent && parent !== root; parent = parent.parentElement) {
      if (parent.matches('details:not([open])') && !parent.querySelector(':scope > summary')?.contains(element)) return false;
    }
    return true;
  });
  const first = items[0], last = items.at(-1);
  if (!first) { event.preventDefault(); root.focus(); return; }
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
}
