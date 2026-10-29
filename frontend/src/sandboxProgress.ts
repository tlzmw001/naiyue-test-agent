import type { PreparationView } from './types';

export function preparationText(progress?: PreparationView | null): string {
  const labels: Record<string,string> = {queued:'等待准备',snapshot:'正在准备项目快照',container:'正在创建沙箱',
    connection:'正在连接沙箱',upload:'正在传输项目',install:'正在安装项目快照',stopping:'正在停止，等待确认…',ready:'准备完成'};
  if (!progress) return '准备中';
  const label=labels[progress.phase]??'准备中';
  if (progress.phase==='upload'&&progress.completed!==null&&progress.total!==null)
    return `${label} · 已确认 ${(progress.completed/1048576).toFixed(1)} / ${(progress.total/1048576).toFixed(1)} MiB`;
  return label;
}
