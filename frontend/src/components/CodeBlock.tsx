import { memo, useEffect, useLayoutEffect, useRef, useState, type ComponentPropsWithoutRef } from 'react';
import type { ExtraProps } from 'react-markdown';
import { requestHighlight } from '../code-highlight-client';
import type { HighlightPart } from '../code-highlight';
import styles from './CodeBlock.module.css';

const ColoredCode = memo(function ColoredCode({ text, language, disabled }: { text: string; language: string; disabled: boolean }) {
  const [result, setResult] = useState<{ text: string; language: string; parts: HighlightPart[] }>();
  useEffect(() => {
    if (disabled || !language || ['text', 'plaintext', 'txt'].includes(language.toLowerCase())) return;
    const controller = new AbortController();
    void requestHighlight(text, language, controller.signal).then(parts => {
      if (parts && !controller.signal.aborted) setResult({ text, language, parts });
    });
    return () => controller.abort();
  }, [text, language, disabled]);
  const parts = !disabled && result?.text === text && result.language === language ? result.parts : undefined;
  return <code className={language ? 'language-' + language : undefined}>{parts
    ? parts.map((part, index) => part.classes ? <span className={part.classes} key={index}>{part.text}</span> : part.text)
    : text}</code>;
});

export default function CodeBlock({ node, children, ...props }: ComponentPropsWithoutRef<'pre'> & ExtraProps) {
  const pre = useRef<HTMLPreElement>(null);
  const previousText = useRef<string | null>(null);
  const copyAttempt = useRef(0);
  const [copied, setCopied] = useState<'idle' | 'copied' | 'failed'>('idle');
  const code = node?.children.find(child => child.type === 'element' && child.tagName === 'code');
  const classes = code?.type === 'element' ? code.properties.className : undefined;
  const languageClass = Array.isArray(classes) ? classes.find(value => String(value).startsWith('language-')) : undefined;
  const sourceLanguage = languageClass ? String(languageClass).slice(9) : '';
  const language = sourceLanguage || 'text';
  const text = code?.type === 'element' ? code.children.map(child => child.type === 'text' ? child.value : '').join('') : undefined;
  const disabled = Array.isArray(classes) && classes.some(value => value === 'no-highlight' || value === 'nohighlight');

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
    <pre {...props} ref={pre} className={styles.code} role="region" aria-label="代码，可横向滚动" tabIndex={0}>{text === undefined ? children :
      <ColoredCode text={text} language={sourceLanguage} disabled={disabled} />}</pre>
  </div>;
}
