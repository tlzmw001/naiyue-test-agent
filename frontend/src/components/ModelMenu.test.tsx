import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import ModelMenu from './ModelMenu';

afterEach(cleanup);
function setup(disabled = false) {
  const close = vi.fn(), selectModel = vi.fn(), selectThinking = vi.fn();
  render(<ModelMenu models={['a-very-long-model-name', 'first']} model="first" level="low"
    options={{ default_level: 'low', supported_levels: ['low', 'high'] }} open disabled={disabled}
    toggle={vi.fn()} close={close} selectModel={selectModel} selectThinking={selectThinking} />);
  return { close, selectModel, selectThinking };
}
it('shows model and supported reasoning choices as selected rows and keeps focus on the current model', () => {
  const { selectModel, selectThinking } = setup();
  const models = screen.getByRole('group', { name: '模型' });
  const current = within(models).getByRole('radio', { name: 'first' }) as HTMLInputElement;
  expect(current.checked).toBe(true); expect(document.activeElement).toBe(current);
  fireEvent.click(within(models).getByRole('radio', { name: 'a-very-long-model-name' }));
  expect(selectModel).toHaveBeenCalledExactlyOnceWith('a-very-long-model-name');
  fireEvent.click(within(screen.getByRole('group', { name: '推理强度' })).getByRole('radio', { name: 'high' }));
  expect(selectThinking).toHaveBeenCalledExactlyOnceWith('high');
  expect(screen.queryByRole('radio', { name: 'max' })).toBeNull();
  expect(screen.queryByRole('combobox')).toBeNull();
});
it('closes on Escape and returns focus to the model trigger', () => {
  const { close } = setup();
  fireEvent.keyDown(screen.getByRole('group', { name: '模型' }), { key: 'Escape' });
  expect(close).toHaveBeenCalledOnce();
  expect(document.activeElement).toBe(screen.getByRole('button', { name: /模型与推理强度/ }));
});
it('disables selection while controls are changing', () => {
  const { selectModel, selectThinking } = setup(true);
  for (const input of screen.getAllByRole('radio')) expect(input.matches(':disabled')).toBe(true);
  expect(selectModel).not.toHaveBeenCalled(); expect(selectThinking).not.toHaveBeenCalled();
});
it('never offers a loading placeholder as a model when the saved selection is empty', () => {
  render(<ModelMenu models={['A', 'B']} model="" level="model_default" open disabled={false}
    toggle={vi.fn()} close={vi.fn()} selectModel={vi.fn()} selectThinking={vi.fn()} />);
  const choices = within(screen.getByRole('group', { name: '模型' })).getAllByRole('radio');
  expect(choices.map(input => (input as HTMLInputElement).value)).toEqual(['A', 'B']);
});
it('distinguishes a saved model missing from the current catalog from available choices', () => {
  render(<ModelMenu models={['A']} model="retired" level="model_default" open disabled={false}
    toggle={vi.fn()} close={vi.fn()} selectModel={vi.fn()} selectThinking={vi.fn()} />);
  expect(screen.getByRole('radio', { name: 'retired（当前不可用）' })).toHaveProperty('disabled', true);
  expect(screen.getByRole('radio', { name: 'A' })).toHaveProperty('disabled', false);
});
