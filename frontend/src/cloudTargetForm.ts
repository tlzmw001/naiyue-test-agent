export interface TargetForm { type: 'http' | 'grpc' | 'tcp'; tls: boolean; address: string }

export function targetForm(address: string): TargetForm {
  const match = /^(http|https|grpc|grpcs|tcp|tls):\/\//i.exec(address);
  if (!match) return { type: 'http', tls: false, address };
  const scheme = match[1].toLowerCase();
  return { type: scheme.startsWith('http') ? 'http' : scheme.startsWith('grpc') ? 'grpc' : 'tcp',
    tls: ['https', 'grpcs', 'tls'].includes(scheme), address: address.slice(match[0].length) };
}

export function targetAddress(form: TargetForm): string {
  const scheme = form.type === 'http' ? (form.tls ? 'https' : 'http') : form.type === 'grpc' ? (form.tls ? 'grpcs' : 'grpc') : (form.tls ? 'tls' : 'tcp');
  const address = `${scheme}://${form.address}`;
  const fail = () => { throw new Error('请填写有效的主机地址；gRPC 和 TCP 需填写端口，地址不能含凭证或查询参数。'); };
  if (/[\s\x00-\x1f\x7f\\]/.test(address) || form.address.includes('://')) return fail();
  let parsed: URL;
  try { parsed = new URL(address); } catch { return fail(); }
  const host = parsed.hostname.replace(/^\[|\]$/g, '');
  const authority = form.address.split('/')[0];
  const port = /:(\d+)$/.exec(authority)?.[1];
  if (!host || !/^[a-zA-Z0-9_.:-]+$/.test(host) || host.startsWith('-') || authority.includes('@') ||
      form.address.includes('?') || form.address.includes('#') ||
      (port !== undefined && (+port < 1 || +port > 65535)) ||
      (form.type !== 'http' && (!port || !['', '/'].includes(parsed.pathname)))) return fail();
  return address;
}
