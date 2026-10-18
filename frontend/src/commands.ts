export const commands = [
  { name: 'compact', label: '压缩上下文' },
  { name: 'skills', label: '技能' },
  { name: 'context', label: '查看上下文构成' },
  { name: 'model', label: '模型与推理强度' },
  { name: 'tools', label: '启用或停用工具' },
  { name: 'permissions', label: '权限模式' },
  { name: 'agents', label: '查看子智能体' },
] as const;
export type CommandName = typeof commands[number]['name'];
export type ComposerMenu = 'commands' | 'modes' | 'tools' | 'skills' | null;
