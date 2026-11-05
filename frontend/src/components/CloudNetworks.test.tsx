import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
  fireEvent.change(screen.getByRole('textbox',{name:'http 目标地址'}),{target:{value:'https://api.test:8443'}});
  fireEvent.click(screen.getByRole('button',{name:'应用 http 配置'}));
  await waitFor(()=>expect(api.changeNetwork).toHaveBeenCalledWith('work','http',{address:'https://api.test:8443',enabled:true},3));
});

it('shows only configured profiles and lets any entry use a TCP target and a name', async () => {
  installDialogMock();
  const value={endpoints:{bind_host:'172.18.0.1',ports:{first:21001,second:21002,third:21003}},state:{revision:0,slots:{},switching:null},local:{},status:{}};
  const api={network:vi.fn().mockResolvedValue(value),changeNetwork:vi.fn().mockResolvedValue(value)} as unknown as CloudApi;
  render(<CloudNetworks api={api} profiles={[{profile_id:'mine',network_id:'alice',template_ids:[],default_template:'',tunnel:'owned',connection:''}]} close={vi.fn()}/>);
  await screen.findByText(/21003/);
  expect(within(screen.getByRole('combobox', { name: '云端连接' })).getAllByRole('option').map(o=>o.textContent)).toEqual(['mine']);
  fireEvent.change(screen.getByRole('textbox',{name:'third 名称'}),{target:{value:'缓存服务'}});
  fireEvent.change(screen.getByRole('textbox',{name:'third 目标地址'}),{target:{value:'tcp://cache.test:6379'}});
  fireEvent.click(screen.getByRole('button',{name:'应用 缓存服务 配置'}));
  await waitFor(()=>expect(api.changeNetwork).toHaveBeenCalledWith('mine','third',{name:'缓存服务',address:'tcp://cache.test:6379',enabled:true},0));
});

it('accepts a bare gRPC address with TLS and infers controls from a pasted URL', async () => {
  installDialogMock();
  const value = { endpoints: { bind_host: '172.18.0.1', ports: { api: 21001 } }, state: { revision: 2, slots: {}, switching: null }, local: {}, status: {} };
  const api = { network: vi.fn().mockResolvedValue(value), changeNetwork: vi.fn().mockResolvedValue(value) } as unknown as CloudApi;
  render(<CloudNetworks api={api} profiles={[{profile_id:'mine',network_id:'alice',template_ids:[],default_template:'',tunnel:'owned',connection:''}]} close={vi.fn()} />);
  await screen.findByText(/21001/);
  fireEvent.change(screen.getByRole('combobox', { name: 'api 类型' }), { target: { value: 'grpc' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'api 目标地址' }), { target: { value: '10.75.192.3:5000' } });
  fireEvent.click(screen.getByRole('checkbox', { name: 'api TLS' }));
  fireEvent.click(screen.getByRole('button', { name: '应用 api 配置' }));
  await waitFor(() => expect(api.changeNetwork).toHaveBeenCalledWith('mine', 'api', { address: 'grpcs://10.75.192.3:5000', enabled: true }, 2));
  await screen.findByText('配置已应用，后续任务连接使用当前目标。');
  fireEvent.change(screen.getByRole('textbox', { name: 'api 目标地址' }), { target: { value: 'http://api.test/path' } });
  expect((screen.getByRole('combobox', { name: 'api 类型' }) as HTMLSelectElement).value).toBe('http');
  expect((screen.getByRole('checkbox', { name: 'api TLS' }) as HTMLInputElement).checked).toBe(false);
});
it('treats prototype-like entry ids as empty configured entries', async () => {
  installDialogMock();
  const value={endpoints:{bind_host:'172.18.0.1',ports:{constructor:21001,toString:21002}},state:{revision:0,slots:{},switching:null},local:{},status:{}};
  const api={network:vi.fn().mockResolvedValue(value)} as unknown as CloudApi;
  render(<CloudNetworks api={api} profiles={[{profile_id:'mine',network_id:'alice',template_ids:[],default_template:'',tunnel:'owned',connection:''}]} close={vi.fn()}/>);
  await screen.findByText(/21001/);
  expect((screen.getByRole('textbox',{name:'constructor 名称'}) as HTMLInputElement).value).toBe('');
  expect((screen.getByRole('textbox',{name:'constructor 目标地址'}) as HTMLInputElement).value).toBe('');
  expect((screen.getByRole('button',{name:'应用 constructor 配置'}) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole('button',{name:'应用 toString 配置'}) as HTMLButtonElement).disabled).toBe(true);
});
