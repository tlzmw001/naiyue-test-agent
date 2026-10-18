import { useLayoutEffect, useRef, useState, type ComponentPropsWithoutRef } from 'react';
import type { ExtraProps } from 'react-markdown';
import styles from './CodeBlock.module.css';

export default function CodeBlock({ node, children, ...props }: ComponentPropsWithoutRef<'pre'> & ExtraProps) {
  const pre = useRef<HTMLPreElement>(null);
  const previousText = useRef<string | null>(null);
  const copyAttempt = useRef(0);
  const [copied, setCopied] = useState<'idle' | 'copied' | 'failed'>('idle');
  const code = node?.children.find(child => child.type === 'element' && child.tagName === 'code');
  const classes = code?.type === 'element' ? code.properties.className : undefined;
  const languageClass = Array.isArray(classes) ? classes.find(value => String(value).startsWith('language-')) : undefined;
  const language = languageClass ? String(languageClass).slice(9) : 'text';

  useLayoutEffect(() => {
    const text = pre.current?.textContent ?? '';
    if (text !== previousText.current) {
      copyAttempt.current++;
      previousText.current = text;
      setCopied('idle');
    }
  });

  async function copy() {
    const text = pre.current?.textContent ?? '';
    const attempt = ++copyAttempt.current;
    try {
      await navigator.clipboard.writeText(text);
      if (attempt === copyAttempt.current && pre.current?.textContent === text) setCopied('copied');
    } catch {
      if (attempt === copyAttempt.current && pre.current?.textContent === text) setCopied('failed');
    }
  }

  return <div className={styles.block}>
    <div className={styles.toolbar}>
      <span className={styles.language} title={language}>{language}</span>
      <button type="button" aria-label="复制代码" onClick={() => void copy()} aria-live="polite">
        {copied === 'copied' ? '已复制' : copied === 'failed' ? '复制失败，重试' : '复制代码'}
      </button>
    </div>
    <pre {...props} ref={pre} className={styles.code} role="region" aria-label="代码，可横向滚动" tabIndex={0}>{children}</pre>
  </div>;
}
