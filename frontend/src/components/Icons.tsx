export function Icon({ name }: { name: 'plus' | 'send' | 'stop' | 'menu' | 'close' | 'chat' }) {
  const paths = {
    plus: <path d="M12 5v14M5 12h14" />,
    send: <><path d="m6 11 6-6 6 6M12 5v14" /></>,
    stop: <rect x="7" y="7" width="10" height="10" rx="1" fill="currentColor" stroke="none" />,
    menu: <path d="M4 6h16M4 12h16M4 18h16" />,
    close: <path d="m6 6 12 12M18 6 6 18" />,
    chat: <path d="M5 5h14v11H9l-4 4V5Z" />,
  };
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}
