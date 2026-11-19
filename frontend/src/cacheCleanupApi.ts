import { cleanupCategories } from './cacheCleanupTypes';
import type { CleanupCategory, CleanupPolicy, CleanupReceipt, CleanupResult } from './cacheCleanupTypes';

const path = '/maintenance/cache-cleanup';

export class CacheCleanupApi {
  constructor(private response: (path: string, init?: RequestInit) => Promise<Response>) {}

  async policies(signal?: AbortSignal): Promise<{ scope: string; categories: CleanupPolicy[] }> {
    return (await this.response(path, { signal })).json();
  }
  async execute(requestId: string, selected: CleanupCategory[]): Promise<CleanupResult> {
    const categories = cleanupCategories.filter(category => selected.includes(category));
    return (await this.response(path, { method: 'POST', body: JSON.stringify({ request_id: requestId, categories }) })).json();
  }
  async lookup(requestId: string, signal?: AbortSignal): Promise<CleanupReceipt> {
    return (await this.response(path + '/' + encodeURIComponent(requestId), { signal })).json();
  }
}
