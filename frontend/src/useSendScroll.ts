import { useState } from 'react';

export const sendScrollOptions = { keep: '保持当前位置', latest: '回到最新消息' } as const;
export type SendScroll = keyof typeof sendScrollOptions;

export function useSendScroll() {
  const [preference, setPreference] = useState<SendScroll>(() => {
    try { return localStorage.getItem('naiyue.scroll-after-send') === 'latest' ? 'latest' : 'keep'; }
    catch { console.warn('send_scroll_preference_read_failed'); return 'keep'; }
  });
  function select(value: SendScroll) {
    setPreference(value);
    try { localStorage.setItem('naiyue.scroll-after-send', value); }
    catch { console.warn('send_scroll_preference_write_failed'); }
  }
  return { preference, select };
}
