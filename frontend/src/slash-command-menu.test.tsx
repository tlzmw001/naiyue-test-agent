import { useState } from 'react';
import type { ComponentProps } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import Composer from './components/Composer';
import { ApiError, WebApi } from './api';
import type { SessionView, SkillCatalogView } from './types';

afterEach(cleanup);

const catalog: SkillCatalogView = { entries: [
  { source_id: 'agents', name: 'case-review', description: '检查失败用例', command: '/case-review' },
  { source_id: 'claude', name: 'case-review', description: '另一来源的审查技能', command: '/claude:case-review' },
  { source_id: 'agents', name: 'compact', description: '同名技能', command: '/agents:compact' },
], diagnostics: [], default_source: 'agents', read_registered: true };

async function setup() {
  const api = new WebApi('slash-menu-fixture');
  vi.spyOn(api, 'sessionControlOptions').mockResolvedValue({ tools: [], default_active_tools: ['read'],
    permission_modes: ['Default', 'NoAsk'], default_permission_mode: 'Default' });
  vi.spyOn(api, 'skills').mockResolvedValue(catalog);
  const send = vi.fn();
  const compact = vi.fn();
  const selectMode = vi.fn();
  const session = { session_id: 'one', can_run: true, active_tools: ['read'], permission_mode: 'Default' } as SessionView;
  function Harness(overrides: Partial<ComponentProps<typeof Composer>>) {
    const [text, setText] = useState('');
    return <Composer text={text} setText={setText} api={api} session={session} pending={[]} setPending={vi.fn()}
      pendingMode={false} busy={false} sending={false} stopping={false} blocked={false} stop={vi.fn()}
      controlsDisabled={false} selectTools={vi.fn()} selectMode={selectMode} models={['A']} model="A"
      selectModel={vi.fn()} selectThinking={vi.fn()} send={follow => send(text, follow)}
      compact={compact} openContext={vi.fn()} openAgents={vi.fn()} {...overrides} />;
  }
  const view = render(<Harness />);
  await screen.findByRole('button', { name: '权限模式：Default' });
  const input = screen.getByRole('textbox', { name: '消息' }) as HTMLTextAreaElement;
  fireEvent.focus(input);
  return { api, input, send, compact, selectMode, session, update: (props: Partial<ComponentProps<typeof Composer>>) =>
    view.rerender(<Harness {...props} />), unmount: view.unmount };
}

function type(input: HTMLElement, value: string) { fireEvent.change(input, { target: { value } }); }

it('opens built-ins and actual skills, filtering locally without executing anything', async () => {
  const { api, input, send, compact } = await setup();
  type(input, '/');
  const list = screen.getByRole('listbox', { name: '指令建议' });
  expect(within(list).getByRole('option', { name: /\/compact 压缩上下文/ })).toBeTruthy();
  await within(list).findByRole('option', { name: /\/case-review/ });
  expect(input.getAttribute('aria-controls')).toBe(list.id);
  type(input, '/ca');
  expect(within(list).getAllByRole('option')).toHaveLength(2);
  expect(within(list).queryByRole('option', { name: /压缩上下文/ })).toBeNull();
  type(input, '/claude:ca');
  expect(within(list).getAllByRole('option')).toHaveLength(1);
  expect(api.skills).toHaveBeenCalledTimes(1);
  expect(send).not.toHaveBeenCalled();
  expect(compact).not.toHaveBeenCalled();
});

it('completes by mouse without sending, then validates a skill with arguments in the existing mode', async () => {
  const { api, input, send } = await setup();
  type(input, '/ca');
  fireEvent.mouseDown(await screen.findByRole('option', { name: /\/case-review 检查失败用例/ }));
  fireEvent.click(screen.getByRole('option', { name: /\/case-review 检查失败用例/ }));
  expect(input.value).toBe('/case-review ');
  expect(document.activeElement).toBe(input);
  expect(screen.queryByRole('listbox')).toBeNull();
  expect(send).not.toHaveBeenCalled();
  type(input, '/case-review 检查本次失败\n给出建议');
  fireEvent.keyDown(input, { key: 'Enter' });
  await waitFor(() => expect(send).toHaveBeenCalledExactlyOnceWith('/case-review 检查本次失败\n给出建议', false));
  expect(api.skills).toHaveBeenCalledTimes(2);
});

it('supports arrows and Tab, while complete built-ins still execute on Enter', async () => {
  const { input, compact, send } = await setup();
  type(input, '/co');
  await screen.findByRole('option', { name: /\/context/ });
  fireEvent.keyDown(input, { key: 'ArrowDown' });
  const context = screen.getByRole('option', { name: /\/context/ });
  expect(context.getAttribute('aria-selected')).toBe('true');
  expect(input.getAttribute('aria-activedescendant')).toBe(context.id);
  fireEvent.keyDown(input, { key: 'ArrowUp' });
  fireEvent.keyDown(input, { key: 'Tab' });
  expect(input.value).toBe('/compact ');
  expect(compact).not.toHaveBeenCalled();
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(compact).toHaveBeenCalledOnce();
  expect(send).not.toHaveBeenCalled();
  type(input, '/compact');
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(compact).toHaveBeenCalledTimes(2);
});

it('uses Enter to complete an unfinished command without executing it', async () => {
  const { input, compact, send } = await setup();
  type(input, '/comp');
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(input.value).toBe('/compact ');
  expect(screen.queryByRole('listbox')).toBeNull();
  expect(compact).not.toHaveBeenCalled();
  expect(send).not.toHaveBeenCalled();
});

it('closes on Escape, outside interaction or another control without losing the draft', async () => {
  const { input } = await setup();
  type(input, '/co');
  fireEvent.keyDown(input, { key: 'Escape' });
  expect(input.value).toBe('/co');
  expect(screen.queryByRole('listbox')).toBeNull();
  type(input, '/con');
  expect(screen.getByRole('listbox')).toBeTruthy();
  fireEvent.pointerDown(document.body);
  expect(screen.queryByRole('listbox')).toBeNull();
  type(input, '/cont');
  fireEvent.click(screen.getByRole('button', { name: '指令与功能' }));
  expect(screen.queryByRole('listbox')).toBeNull();
  expect(screen.getByRole('menu', { name: '指令与功能' })).toBeTruthy();
  expect(input.value).toBe('/cont');
});

it('does not open for paths, prose or arguments, and keeps IME and Shift shortcuts', async () => {
  const { input, send, selectMode } = await setup();
  for (const text of ['/tmp/report.txt', '解释 /compact', '/case-review task']) {
    type(input, text);
    expect(screen.queryByRole('listbox')).toBeNull();
  }
  type(input, '/co');
  fireEvent.compositionStart(input);
  fireEvent.keyDown(input, { key: 'Enter', isComposing: true });
  expect(screen.queryByRole('listbox')).toBeNull();
  expect(input.value).toBe('/co');
  fireEvent.compositionEnd(input);
  expect(screen.getByRole('listbox')).toBeTruthy();
  fireEvent.keyDown(input, { key: 'Enter', shiftKey: true });
  expect(input.value).toBe('/co');
  fireEvent.keyDown(input, { key: 'Tab', shiftKey: true });
  expect(selectMode).toHaveBeenCalledExactlyOnceWith('NoAsk');
  expect(send).not.toHaveBeenCalled();
});

it('shows unavailable reasons and cannot select unavailable operations or skills', async () => {
  const { input, session, update, compact, send } = await setup();
  update({ busy: true, session: { ...session, active_tools: [] } });
  type(input, '/comp');
  const item = screen.getByRole('option', { name: /\/compact/ }) as HTMLButtonElement;
  expect(item.disabled).toBe(true);
  expect(item.textContent).toMatch(/需要空闲会话/);
  fireEvent.click(item);
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(input.value).toBe('/comp');
  type(input, '/ca');
  const skill = await screen.findByRole('option', { name: /\/case-review/ }) as HTMLButtonElement;
  expect(skill.disabled).toBe(true);
  expect(skill.textContent).toMatch(/启用 read/);
  fireEvent.click(skill);
  expect(input.value).toBe('/ca');
  expect(compact).not.toHaveBeenCalled();
  expect(send).not.toHaveBeenCalled();
});

it('keeps built-ins on catalog failure and retries without losing the prefix', async () => {
  const { api, input, send } = await setup();
  vi.mocked(api.skills).mockRejectedValueOnce(new ApiError('skill_catalog_unavailable'));
  type(input, '/');
  await screen.findByText(/技能列表读取失败/);
  expect(screen.getByRole('option', { name: /\/model/ })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '重试加载技能建议' }));
  await screen.findByRole('option', { name: /\/case-review/ });
  expect(input.value).toBe('/');
  expect(send).not.toHaveBeenCalled();
  type(input, '/not-found');
  expect(screen.getByText('没有匹配的指令或技能')).toBeTruthy();
  fireEvent.keyDown(input, { key: 'Enter' });
  await screen.findByText(/不支持的指令/);
  expect(input.value).toBe('/not-found');
  expect(send).not.toHaveBeenCalled();
});

it('discards an old catalog after closing or switching sessions', async () => {
  const { api, input, session, update, unmount } = await setup();
  let resolve!: (value: SkillCatalogView) => void;
  vi.mocked(api.skills).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  type(input, '/');
  expect(screen.getByText('正在查找可用技能…')).toBeTruthy();
  update({ session: { ...session, session_id: 'two' } });
  expect(screen.queryByRole('listbox')).toBeNull();
  type(input, '/ca');
  await screen.findByRole('option', { name: /\/case-review/ });
  await act(async () => resolve({ ...catalog, entries: [] }));
  expect(screen.getByRole('option', { name: /\/case-review/ })).toBeTruthy();
  type(input, 'ordinary');
  vi.mocked(api.skills).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  type(input, '/');
  fireEvent.keyDown(input, { key: 'Escape' });
  await act(async () => resolve(catalog));
  expect(screen.queryByRole('listbox')).toBeNull();
  type(input, '/ca');
  unmount();
});
