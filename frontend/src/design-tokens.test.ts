import { describe, expect, it } from 'vitest';

// Spec113: component styles take type and shape from theme tokens, never ad-hoc values.
// Vitest replaces CSS imports (even ?raw), so read the source files themselves.
const { readFileSync } = await import(/* @vite-ignore */ 'node:' + 'fs') as {
  readFileSync: (path: URL, encoding: 'utf8') => string };
const read = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8');
const theme = read('./theme.css');
const sheets = Object.fromEntries(Object.keys(import.meta.glob(['./**/*.css', '!./theme.css']))
  .map(path => [path, read(path)]));

const rules: Record<string, (part: string) => boolean> = {
  'font-size': value => /^var\(--fs-[a-z]+\)$/.test(value) || value === 'inherit' || /^\d*\.?\d+(em|%)$/.test(value),
  'font-weight': value => /^var\(--fw-[a-z]+\)$/.test(value) || value === 'inherit',
  'line-height': value => /^var\(--lh-[a-z]+\)$/.test(value) || value === 'inherit',
  'border-radius': value => value.split(/\s+/).every(part => /^var\(--r-[a-z]+\)$/.test(part) || part === '0' || part === 'inherit'),
};

const strip = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, '');

describe('design tokens', () => {
  it('reads every component stylesheet', () => {
    expect(Object.keys(sheets).length).toBeGreaterThan(10);
  });

  it('uses theme tokens for type and shape', () => {
    const violations: string[] = [];
    for (const [file, css] of Object.entries(sheets)) {
      for (const match of strip(css).matchAll(/(?<![-\w])(font-size|font-weight|line-height|border-radius)\s*:\s*([^;}]+)/g)) {
        const value = match[2].trim().replace(/\s*!important$/, '');
        if (!rules[match[1]](value)) violations.push(`${file} ${match[1]}: ${value}`);
      }
    }
    expect(violations).toEqual([]);
  });

  it('defines every referenced scale token with a readable minimum', () => {
    const defined = new Set([...strip(theme).matchAll(/(--(?:fs|fw|lh|r|font)-[a-z]+)\s*:/g)].map(match => match[1]));
    const missing = new Set<string>();
    for (const css of Object.values(sheets))
      for (const match of strip(css).matchAll(/var\((--(?:fs|fw|lh|r|font)-[a-z]+)\)/g))
        if (!defined.has(match[1])) missing.add(match[1]);
    expect([...missing]).toEqual([]);
    const caption = /--fs-caption\s*:\s*(\d+)px/.exec(theme);
    expect(Number(caption?.[1])).toBeGreaterThanOrEqual(12);
  });
});

it('lets the delivery grid control checkbox spacing without adding dialog margins', () => {
  const style = document.createElement('style');
  style.textContent = sheets['./components/CloudTasks.module.css'] + sheets['./components/CloudCodeDelivery.module.css'];
  const container = document.createElement('div');
  container.innerHTML = '<section class="delivery"><label class="check"><input type="checkbox">覆盖同名文件</label><label class="check"><input type="checkbox">核对保存位置</label></section><label class="check">其他对话框</label>';
  document.head.append(style); document.body.append(container);
  try {
    const grid = container.querySelector('.delivery')!;
    expect(getComputedStyle(grid).gap).toBe('8px');
    for (const label of grid.querySelectorAll('label')) {
      expect(getComputedStyle(label).marginTop).toBe('0px');
      expect(getComputedStyle(label).marginBottom).toBe('0px');
    }
    expect(getComputedStyle(container.lastElementChild!).marginTop).toBe('16px');
  } finally { style.remove(); container.remove(); }
});
