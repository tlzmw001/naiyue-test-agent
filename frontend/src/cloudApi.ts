import type { CloudArchive, CloudBinding, CloudDetail, CloudDetailPage, CloudDraft, CloudModels, CloudOperation, CloudProfile, CloudSnapshot, CloudThread, CloudUsage, DeletePreview } from './cloudTypes';
import { decodeSSE, responseBytes } from './sse';
import type { SseFrame } from './sse';

import type { ResultSummary, CloudResult, ResultChanges, ResultPreview, ResultFile } from './cloudResultTypes';

type Request = (path: string, init?: RequestInit) => Promise<Response>;
export class CloudApi {
  constructor(readonly projectId: string, private request: Request) {}
  private query(sessionId?: string, values: Record<string, string | number | undefined> = {}) {
    const params = new URLSearchParams();
    if (sessionId) { params.set('local_project_id', this.projectId); params.set('local_session_id', sessionId); }
    for (const [key, value] of Object.entries(values)) if (value !== undefined) params.set(key, String(value));
    return params.size ? '?' + params : '';
  }
  private async read<T>(path: string, signal?: AbortSignal): Promise<T> { return (await this.request(path, { signal })).json(); }
  private async write<T>(path: string, body: object, method = 'POST'): Promise<T> {
    return (await this.request(path, { method, body: JSON.stringify(body) })).json();
  }
  private task(bid: string) { return '/cloud-tasks/' + encodeURIComponent(bid); }
  profiles(signal?: AbortSignal) { return this.read<CloudProfile[]>('/cloud-profiles', signal); }
  list(sid: string, offset = 0, signal?: AbortSignal) { return this.read<CloudBinding[]>('/cloud-tasks' + this.query(sid, { offset, limit: 50 }), signal); }
  binding(sid: string, bid: string, signal?: AbortSignal) { return this.read<CloudBinding>(this.task(bid) + this.query(sid), signal); }
  source(sid: string, value: object) { return this.write<{ source_handle: string; input_id: string; size: number }>('/cloud-sources?local_project_id=' + encodeURIComponent(this.projectId), { local_session_id: sid, ...value }); }
  create(body: object) { return this.write<{ binding: CloudBinding; operation: CloudOperation }>('/cloud-tasks?local_project_id=' + encodeURIComponent(this.projectId), body); }
  snapshot(sid: string, bid: string, thread?: string, signal?: AbortSignal) { return this.read<CloudSnapshot>(this.task(bid) + '/snapshot' + this.query(sid, { thread_id: thread }), signal); }
  history(sid: string, bid: string, thread?: string, cursor?: string, signal?: AbortSignal) { return this.read<CloudArchive>(this.task(bid) + '/history' + this.query(sid, { thread_id: thread, cursor, limit: 30 }), signal); }
  threads(sid: string, bid: string, cursor?: string, signal?: AbortSignal) { return this.read<{ threads: CloudThread[]; next_cursor: string | null }>(this.task(bid) + '/history/threads' + this.query(sid, { cursor }), signal); }
  usage(sid: string, bid: string, signal?: AbortSignal) { return this.read<CloudUsage>(this.task(bid) + '/usage' + this.query(sid), signal); }
  models(sid: string, bid: string, signal?: AbortSignal) { return this.read<CloudModels>(this.task(bid) + '/models' + this.query(sid), signal); }
  detail(sid: string, bid: string, row: CloudDetail, field: string, offset: number, signal?: AbortSignal) {
    return this.read<CloudDetailPage>(this.task(bid) + '/history/messages/' + encodeURIComponent(row.message_id) + this.query(sid, { thread_id: row.thread_id, revision: row.revision, field, offset }), signal);
  }
  draft(sid: string, bid: string, signal?: AbortSignal) { return this.read<CloudDraft>(this.task(bid) + '/draft' + this.query(sid), signal); }
  saveDraft(sid: string, bid: string, draft: { text: string; model_id: string | null; thinking_level?: string; expected_draft_revision: number }) { return this.write<CloudDraft>(this.task(bid) + '/draft' + this.query(sid), draft, 'PUT'); }
  message(sid: string, bid: string, body: object) { return this.write<CloudOperation>(this.task(bid) + '/messages?local_project_id=' + encodeURIComponent(this.projectId), { local_session_id: sid, ...body }); }
  command(sid: string, bid: string, kind: 'control' | 'environment' | 'delete', body: object) { return this.write<CloudOperation>(this.task(bid) + '/' + kind + '?local_project_id=' + encodeURIComponent(this.projectId), { local_session_id: sid, ...body }); }
  operations(sid: string, bid: string, signal?: AbortSignal) { return this.read<CloudOperation[]>(this.task(bid) + '/operations' + this.query(sid), signal); }
  operation(sid: string, cid: string, signal?: AbortSignal) { return this.read<CloudOperation>('/cloud-operations/' + encodeURIComponent(cid) + this.query(sid), signal); }
  retry(sid: string, cid: string) { return this.write<CloudOperation>('/cloud-operations/' + encodeURIComponent(cid) + '/retry' + this.query(sid), {}); }
  preview(sid: string, bid: string, signal?: AbortSignal) { return this.read<DeletePreview>(this.task(bid) + '/delete-preview' + this.query(sid), signal); }
  results(sid: string, bid: string, offset = 0, signal?: AbortSignal) { return this.read<ResultSummary[]>(this.task(bid) + '/results' + this.query(sid, { offset, limit: 20 }), signal); }
  result(sid: string, bid: string, rid: string, signal?: AbortSignal) { return this.read<CloudResult>(this.task(bid) + '/results/' + encodeURIComponent(rid) + this.query(sid), signal); }
  changes(sid: string, bid: string, rid: string, offset = 0, signal?: AbortSignal) { return this.read<ResultChanges>(this.task(bid) + '/results/' + encodeURIComponent(rid) + '/changes' + this.query(sid, { offset, limit: 50 }), signal); }
  resultPreview(sid: string, bid: string, rid: string, fid: string, offset = 0, signal?: AbortSignal) { return this.read<ResultPreview>(this.task(bid) + '/results/' + encodeURIComponent(rid) + '/files/' + encodeURIComponent(fid) + '/preview' + this.query(sid, { offset, limit: 16384 }), signal); }
  resultAction(sid: string, bid: string, rid: string, body: object) { return this.write<CloudOperation>(this.task(bid) + '/results/' + encodeURIComponent(rid) + '/actions?local_project_id=' + encodeURIComponent(this.projectId), { local_session_id: sid, result_id: rid, ...body }); }
  async downloadResult(sid: string, bid: string, rid: string, file: ResultFile) {
    const a = file.artifact;
    const response = await this.request(this.task(bid) + '/results/' + encodeURIComponent(rid) + '/files/' + encodeURIComponent(a.artifact_id) + '/bytes' + this.query(sid));
    if (response.headers.get('etag') !== '"' + a.sha256 + '"' || Number(response.headers.get('content-length')) !== a.size) throw new Error('result_identity_conflict');
    const data = await response.blob();
    const sha = [...new Uint8Array(await crypto.subtle.digest('SHA-256', await data.arrayBuffer()))].map(v => v.toString(16).padStart(2, '0')).join('');
    if (data.size !== a.size || sha !== a.sha256) throw new Error('artifact_content_mismatch');
    const url = URL.createObjectURL(data); const link = document.createElement('a'); link.href = url; link.download = a.relative_name.split('/').pop()!;
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async observe(sid: string, bid: string, thread: string | undefined, cursor: string | undefined, signal: AbortSignal, accept: (value: SseFrame) => void) {
    const response = await this.request(this.task(bid) + '/stream' + this.query(sid, { thread_id: thread }), { signal, headers: cursor ? { 'Last-Event-ID': cursor } : {} });
    if (!response.body) throw new Error('cloud_stream_unavailable');
    for await (const frame of decodeSSE(responseBytes(response.body))) { if (signal.aborted) return; accept(frame); }
  }
}
