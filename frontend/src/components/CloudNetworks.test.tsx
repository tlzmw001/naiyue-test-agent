import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { installDialogMock } from '../testDialogs';
import CloudNetworks from './CloudNetworks';
import type { CloudApi } from '../cloudApi';
afterEach(cleanup);
it('edits targets without changing assigned ports and sends the current revision', async () => {
  installDialogMock();
  const value={endpoints:{bind_host:'172.18.0.1',http_port:21001,grpc_port:21002},state:{revision:3,slots:{},switching:null},local:{},status:{http:'disconnected',grpc:'disconnected'}};
  const api={network:vi.fn().mockResolvedValue(value),changeNetwork:vi.fn().mockResolvedValue({...value,state:{...value.state,revision:4}})} as unknown as CloudApi;
  render(<CloudNetworks api={api} profiles={[{profile_id:'work',network_id:'alice',template_ids:[],default_template:'',tunnel:'owned',connection:''}]} close={vi.fn()}/>);
  await screen.findByText(/21001/);
  fireEvent.change(screen.getByRole('textbox',{name:'HTTP / HTTPS 地址'}),{target:{value:'https://api.test:8443'}});
  fireEvent.click(screen.getByRole('button',{name:'应用 HTTP 配置'}));
  await waitFor(()=>expect(api.changeNetwork).toHaveBeenCalledWith('work','http',{address:'https://api.test:8443',enabled:true},3));
});
