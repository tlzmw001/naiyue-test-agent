import { useEffect, useState } from 'react';

export const themes = { system: '跟随系统', light: '浅色', dark: '深色' } as const;
export type Theme = keyof typeof themes;

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      const value = localStorage.getItem('naiyue.theme');
      return value && Object.hasOwn(themes, value) ? value as Theme : 'system';
    } catch { console.warn('theme_preference_read_failed'); return 'system'; }
  });
  useEffect(() => {
    const root = document.documentElement;
    const previous = root.dataset.theme;
    root.dataset.theme = theme;
    return () => { if (previous === undefined) delete root.dataset.theme; else root.dataset.theme = previous; };
  }, [theme]);
  const selectTheme = (value: Theme) => {
    setTheme(value);
    try { localStorage.setItem('naiyue.theme', value); }
    catch { console.warn('theme_preference_write_failed'); }
  };
  return { theme, selectTheme };
}
