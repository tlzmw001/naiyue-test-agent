import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { installDialogMock } from '../testDialogs';
import CloudNetworks from './CloudNetworks';
import type { CloudApi } from '../cloudApi';
afterEach(cleanup);
it('edits targets without changing assigned ports and sends the current revision', async () => {
  installDialogMock();
  const value={endpoints:{bind_host:'172.18.0.1',ports:{http:21001,grpc:21002,cache:21003}},state:{revision:3,slots:{},switching:null},local:{},status:{http:'disconnected',grpc:'disconnected'}};
  const api={network:vi.fn().mockResolvedValue(value),changeNetwork:vi.fn().mockResolvedValue({...value,state:{...value.state,revision:4}})} as unknown as CloudApi;
  render(<CloudNetworks api={api} profiles={[{profile_id:'work',network_id:'alice',template_ids:[],default_template:'',tunnel:'owned',connection:''}]} close={vi.fn()}/>);
  await screen.findByText(/21001/);
  fireEvent.change(screen.getByRole('textbox',{name:'连接1 目标地址'}),{target:{value:'https://api.test:8443'}});
  fireEvent.click(screen.getByRole('button',{name:'应用 连接1 配置'}));
  await waitFor(()=>expect(api.changeNetwork).toHaveBeenCalledWith('work','http',{address:'https://api.test:8443',enabled:true},3));
});

it('shows only configured profiles and lets any entry use a TCP target and a name', async () => {
  installDialogMock();
  const value={endpoints:{bind_host:'172.18.0.1',ports:{first:21001,second:21002,third:21003}},state:{revision:0,slots:{},switching:null},local:{},status:{}};
  const api={network:vi.fn().mockResolvedValue(value),changeNetwork:vi.fn().mockResolvedValue(value)} as unknown as CloudApi;
  render(<CloudNetworks api={api} profiles={[{profile_id:'mine',network_id:'alice',template_ids:[],default_template:'',tunnel:'owned',connection:''}]} close={vi.fn()}/>);
  await screen.findByText(/21003/);
  expect(within(screen.getByRole('combobox', { name: '云端连接' })).getAllByRole('option').map(o=>o.textContent)).toEqual(['mine']);
  fireEvent.change(screen.getByRole('textbox',{name:'连接3 名称'}),{target:{value:'缓存服务'}});
  fireEvent.change(screen.getByRole('textbox',{name:'缓存服务 目标地址'}),{target:{value:'tcp://cache.test:6379'}});
  fireEvent.click(screen.getByRole('button',{name:'应用 缓存服务 配置'}));
  await waitFor(()=>expect(api.changeNetwork).toHaveBeenCalledWith('mine','third',{name:'缓存服务',address:'tcp://cache.test:6379',enabled:true},0));
});

it('accepts a bare gRPC address with TLS and infers controls from a pasted URL', async () => {
  installDialogMock();
  const value = { endpoints: { bind_host: '172.18.0.1', ports: { api: 21001 } }, state: { revision: 2, slots: {}, switching: null }, local: {}, status: {} };
  const api = { network: vi.fn().mockResolvedValue(value), changeNetwork: vi.fn().mockResolvedValue(value) } as unknown as CloudApi;
  render(<CloudNetworks api={api} profiles={[{profile_id:'mine',network_id:'alice',template_ids:[],default_template:'',tunnel:'owned',connection:''}]} close={vi.fn()} />);
  await screen.findByText(/21001/);
  fireEvent.change(screen.getByRole('combobox', { name: '连接1 类型' }), { target: { value: 'grpc' } });
  fireEvent.change(screen.getByRole('textbox', { name: '连接1 目标地址' }), { target: { value: '10.75.192.3:5000' } });
  fireEvent.click(screen.getByRole('checkbox', { name: '连接1 TLS' }));
  fireEvent.click(screen.getByRole('button', { name: '应用 连接1 配置' }));
  await waitFor(() => expect(api.changeNetwork).toHaveBeenCalledWith('mine', 'api', { address: 'grpcs://10.75.192.3:5000', enabled: true }, 2));
  await screen.findByText('配置已应用，后续任务连接使用当前目标。');
  fireEvent.change(screen.getByRole('textbox', { name: '连接1 目标地址' }), { target: { value: 'http://api.test/path' } });
  expect((screen.getByRole('combobox', { name: '连接1 类型' }) as HTMLSelectElement).value).toBe('http');
  expect((screen.getByRole('checkbox', { name: '连接1 TLS' }) as HTMLInputElement).checked).toBe(false);
});
it('treats prototype-like entry ids as empty configured entries', async () => {
  installDialogMock();
  const value={endpoints:{bind_host:'172.18.0.1',ports:{constructor:21001,toString:21002}},state:{revision:0,slots:{},switching:null},local:{},status:{}};
  const api={network:vi.fn().mockResolvedValue(value)} as unknown as CloudApi;
  render(<CloudNetworks api={api} profiles={[{profile_id:'mine',network_id:'alice',template_ids:[],default_template:'',tunnel:'owned',connection:''}]} close={vi.fn()}/>);
  await screen.findByText(/21001/);
  expect((screen.getByRole('textbox',{name:'连接1 名称'}) as HTMLInputElement).value).toBe('');
  expect((screen.getByRole('textbox',{name:'连接1 目标地址'}) as HTMLInputElement).value).toBe('');
  expect((screen.getByRole('button',{name:'应用 连接1 配置'}) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole('button',{name:'应用 连接2 配置'}) as HTMLButtonElement).disabled).toBe(true);
});


it('marks stale connections unconfirmed on read failure and restores them without losing edits', async () => {
  vi.useFakeTimers(); installDialogMock();
  const value = { endpoints: { bind_host: '172.18.0.1', ports: { api: 21001, off: 21002 } },
    state: { revision: 1, slots: { api: { address: 'http://example.test', enabled: true } }, switching: null },
    local: {}, status: { api: 'connected', off: 'disconnected' } };
  const api = { network: vi.fn().mockResolvedValueOnce(value).mockRejectedValueOnce({ code: 'cloud_connection_failed' }).mockResolvedValue(value) } as unknown as CloudApi;
  try {
    await act(async () => { render(<CloudNetworks api={api} profiles={[{profile_id:'work',network_id:'alice',template_ids:[],default_template:'',tunnel:'owned',connection:''}]} close={vi.fn()} />); });
    expect(screen.getByText('隧道已建立')).toBeTruthy();
    fireEvent.change(screen.getByRole('textbox', { name: '连接1 目标地址' }), { target: { value: 'edited.test:8080' } });
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(screen.queryByText('隧道已建立')).toBeNull();
    expect(screen.getByText('连接待确认，正在恢复')).toBeTruthy();
    expect(screen.getByText('未连接')).toBeTruthy();
    expect(screen.getByRole('alert').textContent).toContain('云端连接不可达');
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(screen.getByText('隧道已建立')).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
    expect((screen.getByRole('textbox', { name: '连接1 目标地址' }) as HTMLInputElement).value).toBe('edited.test:8080');
  } finally { cleanup(); vi.useRealTimers(); }
});

it('names legacy ports numerically and shows the test result beside its action', async () => {
  installDialogMock();
  const value = { endpoints: { bind_host: '127.0.0.1', ports: { http: 21001, grpc: 21002 } },
    state: { revision: 1, slots: {}, switching: null }, local: {}, status: {} };
  const api = { network: vi.fn().mockResolvedValue(value), testNetwork: vi.fn().mockResolvedValue({ tcp_reachable: true }) } as unknown as CloudApi;
  const close = vi.fn();
  render(<CloudNetworks api={api} profiles={[{profile_id:'work',network_id:'alice',template_ids:[],default_template:'',tunnel:'owned',connection:''}]} close={close} />);
  const first = await screen.findByRole('group', { name: '连接1' });
  const second = screen.getByRole('group', { name: '连接2' });
  fireEvent.change(within(second).getByRole('textbox', { name: '连接2 目标地址' }), { target: { value: 'http://example.test' } });
  fireEvent.click(within(second).getByRole('button', { name: '测试 连接2 端口' }));
  expect(await within(second).findByText(/TCP 连接成功/)).toBeTruthy();
  expect(within(first).queryByText(/TCP 连接成功/)).toBeNull();
  const button = screen.getByRole('button', { name: '关闭' });
  expect(button.closest('header')).not.toBeNull();
  fireEvent.click(button);
  expect(close).toHaveBeenCalledOnce();
});
