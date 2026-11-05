import { expect, it } from 'vitest';
import { targetAddress, targetForm } from './cloudTargetForm';

it('round trips existing addresses without losing HTTP paths or explicit ports', () => {
  for (const address of ['https://api.test:443/path', 'grpc://10.0.0.1:5000', 'tls://[::1]:9000']) {
    expect(targetAddress(targetForm(address))).toBe(address);
  }
});
it('supports HTTP default ports but requires a port for gRPC and TCP', () => {
  expect(targetAddress({ type: 'http', tls: true, address: 'example.test' })).toBe('https://example.test');
  expect(() => targetAddress({ type: 'grpc', tls: false, address: 'example.test' })).toThrow();
  expect(() => targetAddress({ type: 'tcp', tls: false, address: 'example.test' })).toThrow();
});
it('rejects credentials, query strings, unsupported schemes and non-HTTP paths', () => {
  for (const address of ['http://a:b@host', 'http://host?token=x', 'ftp://host:21', 'grpc://host:5000/path', 'tcp://host:0', 'host:99999']) {
    expect(() => targetAddress(targetForm(address))).toThrow();
  }
});
