import { useRef } from 'react';
import * as DropdownMenu from '@radix-ui/react-dropdown-menu';
import styles from './ThemeMenu.module.css';
import sidebar from './Sidebar.module.css';

export default function LocalSettingsMenu({ openCleanup }: { openCleanup: (trigger: HTMLButtonElement) => void }) {
  const trigger = useRef<HTMLButtonElement>(null);
  const activating = useRef(false);
  return <DropdownMenu.Root modal={false}>
    <DropdownMenu.Trigger asChild>
      <button ref={trigger} type="button" className={styles.trigger} aria-label="本机设置" title="本机设置">
        <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
          <path strokeLinejoin="round" d="m10 3-.6 2.5-2 .9L5 5.7 3 9.2l1.8 1.8v2L3 14.8l2 3.5 2.4-.7 2 .9.6 2.5h4l.6-2.5 2-.9 2.4.7 2-3.5-1.8-1.8v-2L21 9.2l-2-3.5-2.4.7-2-.9L14 3Z" />
          <circle cx="12" cy="12" r="3" />
        </svg>
      </button>
    </DropdownMenu.Trigger>
    <DropdownMenu.Portal>
      <DropdownMenu.Content className={`${styles.menu} ${sidebar.settingsMenu}`} side="top" sideOffset={8} align="start" loop
        onCloseAutoFocus={event => {
          if (activating.current) { event.preventDefault(); activating.current = false; }
        }}>
        <DropdownMenu.Item className={styles.item} onSelect={() => {
          if (trigger.current) { activating.current = true; openCleanup(trigger.current); }
        }}>清理缓存</DropdownMenu.Item>
      </DropdownMenu.Content>
    </DropdownMenu.Portal>
  </DropdownMenu.Root>;
}
