import rehypeHighlight from 'rehype-highlight';
import type { ElementContent, Root } from 'hast';

export interface HighlightPart { text: string; classes: string }
export interface HighlightRequest { id: number; code: string; language: string }
export interface HighlightReply {
  id: number; parts?: HighlightPart[]; reason?: 'render_budget' | 'failed';
}
export const MAX_HIGHLIGHT_PARTS = 20_000;
const transform = rehypeHighlight({ detect: false, plainText: ['text', 'plaintext', 'txt'] });

// Worker-only computation. Return text and class names, never generated HTML.
export function highlightCode({ id, code, language }: HighlightRequest): HighlightReply {
  try {
    const node: Root['children'][number] = { type: 'element', tagName: 'code',
      properties: { className: ['language-' + language] }, children: [{ type: 'text', value: code }] };
    const tree: Root = { type: 'root', children: [{ type: 'element', tagName: 'pre', properties: {}, children: [node] }] };
    let missing = false;
    // This installed plugin uses file.message only to report an unknown language.
    transform(tree, { message() { missing = true; } } as unknown as Parameters<typeof transform>[1]);
    if (missing) return { id };
    const parts: HighlightPart[] = [];
    let overBudget = false;
    function visit(children: ElementContent[], inherited = '') {
      for (const child of children) {
        if (overBudget) return;
        if (child.type === 'text') {
          const last = parts.at(-1);
          if (last?.classes === inherited) last.text += child.value;
          else parts.push({ text: child.value, classes: inherited });
          if (parts.length > MAX_HIGHLIGHT_PARTS) overBudget = true;
        } else if (child.type === 'element') {
          const classes = child.properties.className;
          const own = Array.isArray(classes) ? classes.filter(value => /^[\w-]+$/.test(String(value))).join(' ') : '';
          visit(child.children, [inherited, own].filter(Boolean).join(' '));
        }
      }
    }
    visit(node.children);
    if (overBudget) return { id, reason: 'render_budget' };
    return parts.some(part => part.classes) ? { id, parts } : { id };
  } catch {
    return { id, reason: 'failed' };
  }
}
