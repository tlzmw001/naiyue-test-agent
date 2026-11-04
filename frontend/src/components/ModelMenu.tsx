import { useEffect, useId, useRef } from 'react';
import type { ThinkingOptions } from '../types';
import styles from './ModelMenu.module.css';
import { Icon } from './Icons';

export default function ModelMenu({ models, model, level, options, open, disabled, toggle, close, selectModel, selectThinking, placement = 'below' }: {
  placement?: 'above' | 'below';
  models: string[]; model: string; level: string; options?: ThinkingOptions; open: boolean; disabled: boolean;
  toggle: () => void; close: () => void; selectModel: (id: string) => void; selectThinking: (level: string) => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    const current = root.current?.querySelector<HTMLInputElement>('input:checked:not(:disabled)') ??
      root.current?.querySelector<HTMLInputElement>('input:not(:disabled)');
    current?.focus();
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) close(); };
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [open, close]);
  const placeholder = models.length ? '请选择模型' : '加载模型…';
  const label = level === 'model_default' ? (options?.default_level === 'model_default' ? undefined : options?.default_level) ?? '配置默认' : level;
  return <div className={styles.root} ref={root} onKeyDown={event => {
    if (event.key === 'Escape') { event.preventDefault(); close(); trigger.current?.focus(); }
  }}>
    <button ref={trigger} type="button" className={styles.trigger} aria-label={`模型与推理强度：${model || placeholder} · ${label}`}
      title={`${model || placeholder} · ${label}`} aria-expanded={open} onClick={toggle} disabled={disabled}>
      <span className={styles.modelName}>{model || placeholder}</span><span className={styles.level}>{label}</span>
    </button>
    {open && <section role="dialog" aria-label="模型与推理强度" className={`${styles.popover} ${placement === 'above' ? styles.above : ''}`}>
      <div className={styles.heading}>模型与推理强度<button type="button" aria-label="关闭模型菜单" onClick={() => { close(); trigger.current?.focus(); }}><Icon name="close"/></button></div>
      <fieldset className={styles.group} disabled={disabled}><legend>模型</legend><div className={styles.options}>
        {(!models.includes(model) ? [model, ...models] : models).filter(Boolean).map(value => <label key={value} className={styles.option} title={value}>
          <input type="radio" name={`${id}-model`} value={value} disabled={!models.includes(value)} checked={model === value} onChange={() => selectModel(value)} />
          <span>{value}{!models.includes(value) && '（当前不可用）'}</span>
        </label>)}
      </div></fieldset>
      <fieldset className={styles.group} disabled={disabled || !options?.supported_levels.length}><legend>推理强度</legend>
        <label className={styles.option}><input type="radio" name={`${id}-thinking`} value="model_default" checked={level === 'model_default'} onChange={() => selectThinking('model_default')} />
          <span>配置默认{options?.default_level && options.default_level !== 'model_default' ? `（${options.default_level}）` : ''}</span></label>
        {options?.supported_levels.map(value => <label key={value} className={styles.option}>
          <input type="radio" name={`${id}-thinking`} value={value} checked={level === value} onChange={() => selectThinking(value)} /><span>{value}</span>
        </label>)}
      </fieldset>
      {!options?.supported_levels.length && <p>当前模型未配置可切换的推理档位。</p>}
      <p>下一次模型请求生效。切换模型后使用该模型的配置默认值。</p>
    </section>}
  </div>;
}
