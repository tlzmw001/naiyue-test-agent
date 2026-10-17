import { useEffect, useRef } from 'react';
import type { ThinkingOptions } from '../types';
import styles from './ModelMenu.module.css';

export default function ModelMenu({ models, model, level, options, open, disabled, toggle, close, selectModel, selectThinking }: {
  models: string[]; model: string; level: string; options?: ThinkingOptions; open: boolean; disabled: boolean;
  toggle: () => void; close: () => void; selectModel: (id: string) => void; selectThinking: (level: string) => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    root.current?.querySelector('select')?.focus();
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) close(); };
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [open, close]);
  const label = level === 'model_default' ? (options?.default_level === 'model_default' ? undefined : options?.default_level) ?? '配置默认' : level;
  return <div className={styles.root} ref={root} onKeyDown={event => {
    if (event.key === 'Escape') { event.preventDefault(); close(); trigger.current?.focus(); }
  }}>
    <button ref={trigger} type="button" className={styles.trigger} aria-label={`模型与推理强度：${model || '加载中'} · ${label}`}
      title={`${model || '加载模型…'} · ${label}`} aria-expanded={open} onClick={toggle} disabled={disabled}>
      <span className={styles.modelName}>{model || '加载模型…'}</span><span className={styles.level}>{label}</span>
    </button>
    {open && <section role="dialog" aria-label="模型与推理强度" className={styles.popover}>
      <div className={styles.heading}>模型与推理强度<button type="button" aria-label="关闭模型菜单" onClick={() => { close(); trigger.current?.focus(); }}>×</button></div>
      <label>模型<select aria-label="模型" value={model} disabled={disabled} onChange={event => selectModel(event.target.value)}>
        {!models.includes(model) && <option value={model}>{model || '加载模型…'}</option>}
        {models.map(id => <option key={id} value={id}>{id}</option>)}
      </select></label>
      <label>推理强度<select aria-label="推理强度" value={level} disabled={disabled || !options?.supported_levels.length}
        onChange={event => selectThinking(event.target.value)}>
        <option value="model_default">配置默认{options?.default_level && options.default_level !== 'model_default' ? `（${options.default_level}）` : ''}</option>
        {options?.supported_levels.map(item => <option key={item} value={item}>{item}</option>)}
      </select></label>
      {!options?.supported_levels.length && <p>当前模型未配置可切换的推理档位。</p>}
      <p>下一次模型请求生效。切换模型后使用该模型的配置默认值。</p>
    </section>}
  </div>;
}
