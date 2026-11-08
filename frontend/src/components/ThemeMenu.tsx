import { useCallback, useState } from 'react';
import * as DropdownMenu from '@radix-ui/react-dropdown-menu';
import { themes, type Theme } from '../useTheme';
import styles from './ThemeMenu.module.css';

export default function ThemeMenu({ theme, select }: { theme: Theme; select: (value: Theme) => void }) {
  const [open, setOpen] = useState(false);
  const contentRef = useCallback((element: HTMLDivElement | null) => {
    // 非模态 Radix 不自动聚焦；挂载时聚焦当前选中项（稳定 ref，避免重渲染抢焦点）。
    element?.querySelector<HTMLElement>('[aria-checked="true"]')?.focus();
  }, []);
  return <div className={styles.root}>
    <DropdownMenu.Root open={open} onOpenChange={setOpen} modal={false}>
      <DropdownMenu.Trigger asChild>
        <button type="button" className={styles.trigger} aria-label={`主题：${themes[theme]}`} title={`主题：${themes[theme]}`}>
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
            <circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5" />
          </svg>
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content ref={contentRef} className={styles.menu} align="end" sideOffset={10} aria-label="主题选择"
          aria-labelledby={undefined} loop>
          <DropdownMenu.RadioGroup value={theme} onValueChange={value => select(value as Theme)}>
            {(Object.keys(themes) as Theme[]).map(value => <DropdownMenu.RadioItem key={value} value={value}
              className={styles.item}>{themes[value]}</DropdownMenu.RadioItem>)}
          </DropdownMenu.RadioGroup>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  </div>;
}
