import type { AgentRun, SearchMode } from "./types";
export const modeLabels: Record<SearchMode, string> = {
  keyword: "关键词检索",
  vector: "语义检索",
  hybrid: "混合检索",
};
export const statusLabels: Record<string, string> = {
  created: "已创建",
  retrieving: "检索中",
  querying_business_context: "查询业务记录",
  drafting: "草稿生成中",
  pending_approval: "待人工审批",
  completed: "已批准",
  rejected: "已拒绝",
  ready: "可检索",
  processing: "处理中",
  failed: "处理失败",
};
export const isUUID = (s: string) =>
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s);
export const formatDate = (s: string) =>
  new Date(s).toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
export const formatSize = (n: number | null) =>
  n == null
    ? "—"
    : n < 1024
      ? n + " B"
      : n < 1048576
        ? (n / 1024).toFixed(1) + " KB"
        : (n / 1048576).toFixed(1) + " MB";
export function validateUpload(
  file: Pick<File, "name" | "size">,
): string | null {
  if (!/\.(txt|md|pdf|docx)$/i.test(file.name))
    return "请选择 TXT、Markdown、PDF 或 DOCX 文件。";
  if (!file.size) return "文件为空，请选择有内容的文件。";
  if (file.size > 20 * 1024 * 1024) return "文件不能超过 20 MB。";
  return null;
}
const historyKey = "qualitypilot.recent-run-ids.v1";
export function readRecentIds(): string[] {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(historyKey) || "[]");
    return Array.isArray(raw)
      ? [
          ...new Set(
            raw.filter((v): v is string => typeof v === "string" && isUUID(v)),
          ),
        ].slice(0, 20)
      : [];
  } catch {
    return [];
  }
}
export function rememberRun(id: string) {
  if (!isUUID(id)) return;
  try {
    localStorage.setItem(
      historyKey,
      JSON.stringify(
        [id, ...readRecentIds().filter((v) => v !== id)].slice(0, 20),
      ),
    );
  } catch {
    /* Restricted storage must not block the API. */
  }
}
export function approvalKey(
  run: AgentRun,
  approved: boolean,
  actor: string,
  comment: string,
) {
  return JSON.stringify([run.run_id, approved, actor.trim(), comment.trim()]);
}
