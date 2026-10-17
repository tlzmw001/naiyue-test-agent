import { useState } from 'react';
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

function setup(read = true) {
  const api = new WebApi('fixture');
  vi.spyOn(api, 'sessionControlOptions').mockResolvedValue({ tools: [], default_active_tools: read ? ['read'] : [],
    permission_modes: ['Default'], default_permission_mode: 'Default' });
  vi.spyOn(api, 'skills').mockResolvedValue(catalog);
  const send = vi.fn();
  const compact = vi.fn();
  function Harness({ sessionId, busy = false, pendingTexts = [] }: { sessionId?: string; busy?: boolean; pendingTexts?: string[] }) {
    const [text, setText] = useState('');
    const session = sessionId ? { session_id: sessionId, active_tools: read ? ['read'] : [] } as SessionView : null;
    return <Composer text={text} setText={setText} pending={pendingTexts} setPending={vi.fn()} pendingMode={!!pendingTexts.length}
      busy={busy} sending={false} stopping={false} blocked={false} stop={vi.fn()}
      api={api} session={session} controlsDisabled={false} selectTools={vi.fn()} selectMode={vi.fn()}
      send={follow => send(text, follow)} models={['A']} model="A" selectModel={vi.fn()} selectThinking={vi.fn()}
      compact={compact} openContext={vi.fn()} openAgents={vi.fn()} />;
  }
  const view = render(<Harness />);
  return { api, send, compact, ...view, Harness };
}

async function openSkills() {
  fireEvent.click(await screen.findByRole('button', { name: '指令与功能' }));
  fireEvent.click(screen.getByRole('menuitem', { name: /技能.*skills/ }));
  return screen.findByRole('dialog', { name: '技能' });
}

it('opens the same catalog from plus and /skills; selection prefixes drafts without sending', async () => {
  const { api, send } = setup();
  const input = screen.getByRole('textbox', { name: '消息' });
  fireEvent.change(input, { target: { value: '检查这批用例' } });
  const dialog = await openSkills();
  const row = await within(dialog).findByRole('button', { name: /检查失败用例.*\/case-review/ });
  expect(within(dialog).getByText('另一来源的审查技能')).toBeTruthy();
  fireEvent.click(row);
  expect((input as HTMLTextAreaElement).value).toBe('/case-review 检查这批用例');
  expect(document.activeElement).toBe(input);
  expect(send).not.toHaveBeenCalled();
  await openSkills();
  fireEvent.click(await screen.findByRole('button', { name: /另一来源的审查技能.*\/claude:case-review/ }));
  expect((input as HTMLTextAreaElement).value).toBe('/claude:case-review 检查这批用例');
  fireEvent.change(input, { target: { value: '/skills' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  await screen.findByRole('dialog', { name: '技能' });
  await waitFor(() => expect(api.skills).toHaveBeenCalledTimes(3));
  expect((input as HTMLTextAreaElement).value).toBe('');
  expect(send).not.toHaveBeenCalled();
  fireEvent.keyDown(screen.getByRole('dialog', { name: '技能' }), { key: 'Escape' });
  expect(screen.queryByRole('dialog', { name: '技能' })).toBeNull();
});

it('keeps path drafts and sends qualified or multiline skill commands through the existing send mode', async () => {
  const { api, send, compact, rerender, Harness } = setup();
  const input = screen.getByRole('textbox', { name: '消息' });
  fireEvent.change(input, { target: { value: '/tmp/report.txt' } });
  await openSkills();
  fireEvent.click(await screen.findByRole('button', { name: /同名技能.*\/agents:compact/ }));
  expect((input as HTMLTextAreaElement).value).toBe('/agents:compact /tmp/report.txt');
  fireEvent.keyDown(input, { key: 'Enter' });
  await waitFor(() => expect(send).toHaveBeenCalledExactlyOnceWith('/agents:compact /tmp/report.txt', false));
  expect(compact).not.toHaveBeenCalled();
  rerender(<Harness sessionId="s" busy />);
  fireEvent.change(screen.getByRole('combobox', { name: '发送方式' }), { target: { value: 'follow_up' } });
  const text = '/claude:case-review 先看失败\n再给建议';
  fireEvent.change(input, { target: { value: text } });
  fireEvent.keyDown(input, { key: 'Enter' });
  await waitFor(() => expect(send).toHaveBeenLastCalledWith(text, true));
  expect(api.skills).toHaveBeenCalledTimes(3);
  fireEvent.change(input, { target: { value: '/tmp/report.txt' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(send).toHaveBeenLastCalledWith('/tmp/report.txt', true);
  expect(api.skills).toHaveBeenCalledTimes(3);
});

it('reports an empty catalog, retries errors, and refreshes before selecting', async () => {
  const { api, send } = setup();
  vi.mocked(api.skills).mockRejectedValueOnce(new ApiError('skill_catalog_unavailable'))
    .mockResolvedValueOnce({ ...catalog, entries: [], diagnostics: [{ source_id: 'agents', reason: 'invalid_yaml' }] });
  await openSkills();
  await screen.findByText(/技能列表读取失败/);
  fireEvent.click(screen.getByRole('button', { name: '重试加载技能' }));
  await screen.findByText(/没有可用技能/);
  expect(screen.getByText(/1 项.*未能加载/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '刷新技能' }));
  await screen.findByRole('button', { name: /检查失败用例.*\/case-review/ });
  expect(send).not.toHaveBeenCalled();
});

it('keeps read-disabled and missing skills out of model submission without losing the draft', async () => {
  const { api, send } = setup(false);
  const input = screen.getByRole('textbox', { name: '消息' });
  await openSkills();
  expect((await screen.findByRole('button', { name: /检查失败用例.*\/case-review/ }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText(/请先在工具中启用 read/)).toBeTruthy();
  fireEvent.pointerDown(input);
  fireEvent.change(input, { target: { value: '/case-review task' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  await screen.findByRole('status');
  expect((input as HTMLTextAreaElement).value).toBe('/case-review task');
  vi.mocked(api.skills).mockResolvedValue({ ...catalog, entries: [] });
  fireEvent.change(input, { target: { value: '/missing-skill task' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  await screen.findByText(/不支持的指令/);
  expect(send).not.toHaveBeenCalled();
});

it('discards confirmation responses after editing or switching sessions and deduplicates submit', async () => {
  const { api, send, rerender, Harness } = setup();
  await screen.findByRole('button', { name: '权限模式：Default' });
  const input = screen.getByRole('textbox', { name: '消息' });
  let resolve!: (value: SkillCatalogView) => void;
  vi.mocked(api.skills).mockImplementation(() => new Promise(done => { resolve = done; }));
  fireEvent.change(input, { target: { value: '/case-review task' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(api.skills).toHaveBeenCalledTimes(1);
  fireEvent.change(input, { target: { value: 'new task' } });
  await act(async () => resolve(catalog));
  expect(send).not.toHaveBeenCalled();
  expect((input as HTMLTextAreaElement).value).toBe('new task');
  fireEvent.change(input, { target: { value: '/case-review task' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  rerender(<Harness sessionId="other" />);
  await act(async () => resolve(catalog));
  expect(send).not.toHaveBeenCalled();
  fireEvent.keyDown(input, { key: 'Enter' });
  await act(async () => resolve(catalog));
  expect(send).toHaveBeenCalledExactlyOnceWith('/case-review task', false);
});

it('validates numeric skill names with the same Read requirement', async () => {
  const { api, send } = setup(false);
  vi.mocked(api.skills).mockResolvedValue({ ...catalog, entries: [{ source_id: 'agents',
    name: '123-review', description: '数字开头技能', command: '/123-review' }] });
  await screen.findByRole('button', { name: '权限模式：Default' });
  const input = screen.getByRole('textbox', { name: '消息' });
  fireEvent.change(input, { target: { value: '/123-review task' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  await screen.findByText(/请先在工具中启用 read/);
  expect(api.skills).toHaveBeenCalledTimes(1);
  expect(send).not.toHaveBeenCalled();
});

it('preserves drafts on query failure and ignores a confirmation after unmount', async () => {
  const { api, send, unmount } = setup();
  await screen.findByRole('button', { name: '权限模式：Default' });
  const input = screen.getByRole('textbox', { name: '消息' });
  vi.mocked(api.skills).mockRejectedValueOnce(new ApiError('skill_catalog_unavailable'));
  fireEvent.change(input, { target: { value: '/case-review task' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  await screen.findByText(/技能列表读取失败/);
  expect((input as HTMLTextAreaElement).value).toBe('/case-review task');
  let resolve!: (value: SkillCatalogView) => void;
  vi.mocked(api.skills).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  fireEvent.keyDown(input, { key: 'Enter' });
  unmount();
  await act(async () => resolve(catalog));
  expect(send).not.toHaveBeenCalled();
});

it('does not reuse a closed catalog response and allows keyboard selection', async () => {
  const { api, send } = setup();
  let resolve!: (value: SkillCatalogView) => void;
  vi.mocked(api.skills).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  await openSkills();
  const input = screen.getByRole('textbox', { name: '消息' });
  fireEvent.pointerDown(input);
  await openSkills();
  const first = await screen.findByRole('button', { name: /检查失败用例.*\/case-review/ });
  await act(async () => resolve({ ...catalog, entries: [] }));
  expect(screen.getByRole('button', { name: /检查失败用例.*\/case-review/ })).toBe(first);
  first.focus();
  fireEvent.keyDown(first, { key: 'ArrowDown' });
  const second = screen.getByRole('button', { name: /另一来源的审查技能.*\/claude:case-review/ });
  expect(document.activeElement).toBe(second);
  fireEvent.click(second);
  expect((input as HTMLTextAreaElement).value).toBe('/claude:case-review ');
  expect(send).not.toHaveBeenCalled();
});

it('invalidates confirmation when a retained pending draft changes', async () => {
  const { api, send, rerender, Harness } = setup();
  rerender(<Harness sessionId="s" pendingTexts={['earlier task']} />);
  await screen.findByRole('button', { name: '权限模式：Default' });
  let resolve!: (value: SkillCatalogView) => void;
  vi.mocked(api.skills).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  const input = screen.getByRole('textbox', { name: '消息' });
  fireEvent.change(input, { target: { value: '/case-review task' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  rerender(<Harness sessionId="s" pendingTexts={['edited task after confirmation started']} />);
  await act(async () => resolve(catalog));
  expect(send).not.toHaveBeenCalled();
  expect((input as HTMLTextAreaElement).value).toBe('/case-review task');
});
