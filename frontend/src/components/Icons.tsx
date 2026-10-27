import styles from './Icons.module.css';

export function Chevron({ direction = 'down', className = '' }: { direction?: 'up' | 'down' | 'left' | 'right'; className?: string }) {
  return <svg className={`${styles.chevron} ${className}`} data-chevron={direction} width="14" height="14" viewBox="0 0 24 24"
    fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    <path d="m7 9.5 5 5 5-5" />
  </svg>;
}

export function Icon({ name }: { name: 'image' | 'plus' | 'send' | 'stop' | 'menu' | 'close' | 'chat' | 'pin' | 'sidebar' | 'folder' | 'computer' | 'sandbox' | 'changes' | 'refresh' }) {
  const paths = {
    image: <><rect x="3" y="3" width="18" height="18" rx="2" /><circle cx="8" cy="8" r="1.5" /><path d="m3 17 5-5 4 4 4-7 5 8" /></>,
    plus: <path d="M12 5v14M5 12h14" />,
    send: <><path d="m6 11 6-6 6 6M12 5v14" /></>,
    stop: <rect x="7" y="7" width="10" height="10" rx="1" fill="currentColor" stroke="none" />,
    menu: <path d="M4 6h16M4 12h16M4 18h16" />,
    close: <path d="m6 6 12 12M18 6 6 18" />,
    chat: <path d="M5 5h14v11H9l-4 4V5Z" />,
    pin: <path d="m16 3 5 5-4 1-3 5-1 4-7-7 4-1 5-3 1-4ZM9 15l-6 6" />,
    folder: <path d="M3 7V5h7l2 2h9v13H3V7Zm0 3h18" />,
    sidebar: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M14 4v16" /></>,
    computer: <><rect x="4" y="3" width="16" height="13" rx="2" /><path d="M2 20h20M8 16v4m8-4v4" /></>,
    sandbox: <><path d="m12 3 9 5-9 5-9-5 9-5Zm-9 5v10l9 5 9-5V8M12 13v10" /></>,
    changes: <><path d="M14 3H5v18h14V8l-5-5Zm0 0v5h5M8 12h8m-8 4h5" /></>,
    refresh: <><path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 7a7 7 0 0 1 12-1l2 3M4 15l2 3a7 7 0 0 0 12-1"/></>,
  };
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}
