import { useEffect, useId, useRef, useState } from 'react';
import type { CloudApi } from '../cloudApi';
import type { CloudMcpStatus } from '../cloudTypes';
import { cloudError } from '../cloudPresentation';
import { backgroundTaskPollMs } from '../uiSettings';
import controls from './ComposerControls.module.css';
import styles from './CloudMcpTools.module.css';

const labels = {
  available: '可用', refreshing: '正在刷新', waiting_run: '本轮结束后刷新',
  waiting_runner: '等待 Runner', error: '部分服务刷新失败', unconfirmed: '刷新结果待核对',
  unsupported: '当前云端环境不支持 MCP',
};

export default function CloudMcpTools({ api, sid, bid, active, supported, onPending }: {
  api: CloudApi; sid: string; bid: string; active: boolean; supported?: boolean; onPending?: (value: boolean) => void;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<CloudMcpStatus>();
  const [error, setError] = useState('');
  const [readError, setReadError] = useState('');
  const [sending, setSending] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const attempt = useRef<string>(null);
  const mounted = useRef(true);
  const refreshPending = status?.status === 'refreshing' || status?.status === 'waiting_run';
  const watch = active && supported !== false && (open || refreshPending || !!status?.refresh_id && status.status === 'waiting_runner');

  function close(restoreFocus = true) {
    setOpen(false);
    if (restoreFocus) trigger.current?.focus();
  }

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => { if (!active) setOpen(false); }, [active]);
  useEffect(() => { onPending?.(sending || refreshPending); }, [onPending, sending, refreshPending]);
  useEffect(() => {
    if (!open) return;
    panel.current?.focus();
    function outside(event: PointerEvent) {
      if (event.target instanceof Node && !panel.current?.contains(event.target) && !trigger.current?.contains(event.target)) close(false);
    }
    function escape(event: KeyboardEvent) {
      if (event.key !== 'Escape') return;
      event.stopPropagation();
      close();
    }
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape);
    };
  }, [open]);
  useEffect(() => {
    if (!watch || sending) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const read = async () => {
      try {
        const next = await api.mcp(sid, bid, controller.signal);
        if (controller.signal.aborted) return;
        setStatus(next);
        setReadError('');
        if (next.refresh_id === attempt.current) attempt.current = null;
      } catch (failure) {
        if (controller.signal.aborted) return;
        console.warn('cloud_mcp_status_failed');
        setReadError(cloudError(failure));
      }
      if (!controller.signal.aborted) timer = setTimeout(() => void read(), backgroundTaskPollMs);
    };
    void read();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [api, sid, bid, watch, sending]);

  async function refresh() {
    if (sending) return;
    const request = attempt.current ?? crypto.randomUUID();
    attempt.current = request;
    setError('');
    setSending(true);
    try {
      const next = await api.refreshMcp(sid, bid, request);
      if (mounted.current) setStatus(next);
      attempt.current = null;
    } catch (failure) {
      console.warn('cloud_mcp_refresh_failed');
      if (mounted.current) setError(cloudError(failure));
    } finally {
      if (mounted.current) setSending(false);
    }
  }

  const disabled = supported === false || status?.status === 'unsupported';
  const tools = status?.services.reduce((count, service) => count + service.tools.length, 0) ?? 0;
  return <>
    <button ref={trigger} type="button" className={controls.trigger} aria-label="MCP 工具"
      aria-expanded={open} aria-controls={open ? id : undefined} aria-haspopup="dialog"
      onClick={() => open ? close() : setOpen(true)}>
      MCP 工具{tools > 0 && <span className={controls.count} aria-hidden="true">{tools}</span>}
    </button>
    {open && <div ref={panel} id={id} role="dialog" aria-label="MCP 工具" tabIndex={-1}
      className={styles.panel}>
      <header className={styles.header}>
        <div><strong>MCP 工具</strong><small>供云端主智能体使用</small></div>
        <button type="button" className={styles.close} aria-label="关闭 MCP 工具" onClick={() => close()}>×</button>
      </header>
      <div className={styles.content}>
        <div role="status" className={styles.summary}>
          {error || readError || (disabled ? labels.unsupported : sending ? '正在提交刷新' : status ? labels[status.status] : '正在读取工具状态')}
        </div>
        {status?.services.length === 0 && status.status === 'available' &&
          <p className={styles.note}>此任务暂无 MCP 服务。</p>}
        {status?.services.map(service => <section className={styles.service} key={service.server_id} aria-label={service.server_id}>
          <div className={styles.serviceHeading}><strong>{service.server_id}</strong>
            <small>{service.route === 'direct' ? '开发机直连' : 'Windows 中转'}</small>
          </div>
          {service.status === 'disabled' && <p className={styles.note}>已停用</p>}
          {service.status === 'available' && service.tools.length === 0 && <p className={styles.note}>服务未提供工具</p>}
          {service.error && <p className={styles.failure}>{service.error === 'mcp_tunnel_target_unavailable'
            ? '中转入口不可用或目标不匹配' : service.tools.length ? '刷新失败，仍显示上次生效的工具' : '无法读取此服务的工具'}</p>}
          {service.tools.length > 0 && <ul className={styles.tools}>{service.tools.map(tool => <li key={tool}>{tool}</li>)}</ul>}
        </section>)}
      </div>
      <footer className={styles.footer}>
        <button type="button" className={controls.trigger} disabled={disabled || sending || refreshPending}
          onClick={() => void refresh()}>刷新工具</button>
      </footer>
    </div>}
  </>;
}
