import type { WebApi } from './api';
import type { RunView } from './types';

export async function stopObservedSubagent(api: WebApi, live: RunView | null, agentPath: string,
  originRunId: string, agentRunId?: string, approvalBatchId?: string): Promise<boolean> {
  if (!live || live.status !== 'running' || live.run_id !== originRunId) return false;
  const target = live.agents?.[agentPath];
  if (agentRunId && target?.source.agent_run_id !== agentRunId) return false;
  await api.control(live.run_id, 'stop', undefined, agentPath, agentRunId ?? target?.source.agent_run_id, approvalBatchId);
  return true;
}
