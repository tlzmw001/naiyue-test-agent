/** Exact display for cumulative integer strings; absent timing stays unknown. */
export function formatDuration(value: string | number | null | undefined): string {
  if (value == null || (typeof value === 'number' && (!Number.isSafeInteger(value) || value < 0)) ||
      (typeof value === 'string' && !/^\d+$/.test(value))) return '未提供';
  const amount = BigInt(value);
  if (amount < 1000n) return `${amount} 毫秒`;
  const fraction = (amount % 1000n).toString().padStart(3, '0').replace(/0+$/, '');
  return `${amount / 1000n}${fraction ? '.' + fraction : ''} 秒`;
}
