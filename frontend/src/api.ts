import type {
  AgentRun,
  Chunk,
  Evidence,
  KnowledgeDocument,
  Page,
  RunError,
  SearchMode,
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    public status = 0,
    public runId: string | null = null,
  ) {
    super(message);
  }
}
export function errorDetail(body: unknown, status: number): string {
  if (status === 401)
    return "身份验证失败。请在「连接设置」中填写有效的审批凭据。";
  if (status === 403) return "当前身份没有执行此操作的权限，请联系管理员。";
  if (status === 409) return "该记录的审批状态已发生变化，请刷新后查看。";
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail))
      return detail
        .map((d) => (typeof d?.msg === "string" ? d.msg : "输入格式不正确"))
        .join("；");
  }
  if (status === 404) return "未找到记录，请检查编号是否正确。";
  if (status >= 500)
    return "服务暂时不可用，请确认后端与数据库已启动，然后重试。";
  return "请求未完成，请稍后重试。";
}
export async function request<T>(
  path: string,
  init: RequestInit = {},
  timeout = 15000,
): Promise<{ data: T; headers: Headers }> {
  const controller = new AbortController();
  const cancel = () => controller.abort();
  if (init.signal?.aborted) controller.abort();
  init.signal?.addEventListener("abort", cancel, { once: true });
  const timer = setTimeout(cancel, timeout);
  try {
    const response = await fetch(path, { ...init, signal: controller.signal });
    const body: unknown = await response.json().catch(() => null);
    if (!response.ok)
      throw new ApiError(
        errorDetail(body, response.status),
        response.status,
        response.headers.get("X-Agent-Run-Id"),
      );
    return { data: body as T, headers: response.headers };
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (init.signal?.aborted) throw error;
    throw new ApiError(
      controller.signal.aborted
        ? "等待响应超时。提交操作可能仍在处理，请先查询记录状态，避免重复提交。"
        : "无法连接后端，请确认服务已启动，然后重试。",
    );
  } finally {
    clearTimeout(timer);
    init.signal?.removeEventListener("abort", cancel);
  }
}
const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});
const base = "/api/v1";
export const api = {
  ready: () => request<{ status: string }>(base + "/ready"),
  capabilities: async () => {
    const { data } = await request<{
      components?: {
        schemas?: { AgentRunCreate?: { properties?: Record<string, unknown> } };
      };
    }>("/openapi.json");
    return Boolean(
      data.components?.schemas?.AgentRunCreate?.properties?.use_model,
    );
  },
  documents: async (offset = 0): Promise<Page<KnowledgeDocument>> => {
    const r = await request<KnowledgeDocument[]>(
      base + "/knowledge/documents?limit=10&offset=" + offset,
    );
    return {
      items: r.data,
      total: Number(r.headers.get("X-Total-Count") ?? r.data.length),
    };
  },
  chunks: async (id: string, offset = 0): Promise<Page<Chunk>> => {
    const r = await request<Chunk[]>(
      base +
        "/knowledge/documents/" +
        encodeURIComponent(id) +
        "/chunks?limit=20&offset=" +
        offset,
    );
    return {
      items: r.data,
      total: Number(r.headers.get("X-Total-Count") ?? r.data.length),
    };
  },
  upload: async (file: File, title: string) => {
    const body = new FormData();
    body.append("file", file);
    if (title.trim()) body.append("title", title.trim());
    return (
      await request<KnowledgeDocument>(
        base + "/knowledge/documents/upload",
        { method: "POST", body },
        600000,
      )
    ).data;
  },
  backfill: async (id: string) =>
    (
      await request<{ embedded_chunks: number; skipped_chunks: number }>(
        base + "/knowledge/documents/" + encodeURIComponent(id) + "/embeddings",
        { method: "POST" },
        600000,
      )
    ).data,
  search: async (query: string, mode: SearchMode, top_k: number) =>
    (
      await request<Evidence[]>(
        base + "/knowledge/search",
        json({ query, mode, top_k }),
        120000,
      )
    ).data,
  createRun: async (
    question: string,
    search_mode: SearchMode,
    top_k: number,
    use_model: boolean,
  ) =>
    (
      await request<AgentRun>(
        base + "/agent/runs",
        json({ question, search_mode, top_k, use_model }),
        600000,
      )
    ).data,
  run: async (id: string, signal?: AbortSignal) =>
    (
      await request<AgentRun>(base + "/agent/runs/" + encodeURIComponent(id), {
        signal,
      })
    ).data,
  errors: async (id: string) =>
    (
      await request<RunError[]>(
        base + "/agent/runs/" + encodeURIComponent(id) + "/errors",
      )
    ).data,
  approve: async (
    id: string,
    payload: {
      approved: boolean;
      actor_id: string;
      comment: string | null;
      request_id: string;
    },
    token: string,
  ) => {
    const init = json(payload);
    init.headers = {
      ...init.headers,
      ...(token.trim() ? { Authorization: "Bearer " + token.trim() } : {}),
    };
    return (
      await request<AgentRun>(
        base + "/agent/runs/" + encodeURIComponent(id) + "/approval",
        init,
        60000,
      )
    ).data;
  },
};
