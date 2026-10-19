import { expect, it } from 'vitest';
import { highlightCode, MAX_HIGHLIGHT_PARTS } from './code-highlight';

it.each(['python', 'js', 'json', 'bash'])('retains every character when highlighting %s', language => {
  const codes: Record<string, string> = { python: 'def check():\n\treturn "中文😀"  \n', js: 'const x = `<script>${"😀"}</script>`;\n',
    json: '{"x":"中文😀"}\n', bash: 'echo "中文😀"\n' };
  const code = codes[language];
  const result = highlightCode({ id: 7, code, language });
  expect(result.id).toBe(7);
  expect(result.reason).toBeUndefined();
  expect(result.parts?.map(part => part.text).join('')).toBe(code);
  expect(result.parts?.some(part => part.classes.includes('hljs-'))).toBe(true);
  expect(result.parts?.every(part => /^[\w -]*$/.test(part.classes))).toBe(true);
});

it.each(['text', 'plaintext', 'txt', 'unregistered-tool-language'])('does not guess the language for %s', language => {
  expect(highlightCode({ id: 1, code: 'return "unchanged"\n', language })).toEqual({ id: 1 });
});

it('limits rendered color fragments without returning partial or modified code', () => {
  const result = highlightCode({ id: 1, code: 'return 1\n'.repeat(MAX_HIGHLIGHT_PARTS), language: 'python' });
  expect(result).toEqual({ id: 1, reason: 'render_budget' });
});
