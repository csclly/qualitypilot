export type SearchMode = "keyword" | "vector" | "hybrid";
export interface KnowledgeDocument {
  id: string;
  title: string;
  source_type: string;
  status: string;
  original_filename: string | null;
  file_size: number | null;
  chunk_count: number;
  created_at: string;
  processed_at: string | null;
}
export interface Chunk {
  id: string;
  document_id: string;
  chunk_index: number;
  content: string;
  has_embedding: boolean;
  embedding_dimension: number | null;
}
export interface Evidence {
  chunk_id: string;
  document_id: string;
  document_title: string;
  original_filename: string | null;
  chunk_index: number;
  content: string;
  score: number;
  match_type: SearchMode;
  vector_score: number | null;
  keyword_score: number | null;
}
export interface Draft {
  summary: string;
  suggested_actions: string[];
  risk_notes: string[];
  citations: string[];
  business_record_references: { tool_name: string; record_id: string }[];
  generation_mode: "model" | "deterministic_fallback";
}
export interface AuditEvent {
  id: string;
  actor_id: string;
  actor_authenticated: boolean;
  auth_method: string | null;
  approved: boolean;
  comment: string | null;
  occurred_at: string;
}
export interface AgentRun {
  run_id: string;
  question: string;
  search_mode: SearchMode;
  top_k: number;
  status: string;
  evidence: Evidence[];
  draft: Draft | null;
  final_response: Draft | null;
  approval_required: boolean;
  approved: boolean | null;
  approval_event: AuditEvent | null;
  business_records: {
    tool_name: string;
    system: string;
    record_id: string;
    summary: string;
  }[];
  business_tool_failures: { tool_name: string; message: string }[];
}
export interface RunError {
  id: string;
  stage: string;
  message: string;
  error_kind: string;
}
export interface Page<T> {
  items: T[];
  total: number;
}
