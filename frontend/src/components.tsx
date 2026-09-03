import { useEffect, useRef, type ReactNode } from "react";
import {
  AlertCircle,
  ChevronDown,
  FileText,
  Inbox,
  LoaderCircle,
  X,
} from "lucide-react";
import type { Evidence } from "./types";
import { modeLabels, statusLabels } from "./lib";

export const Spinner = () => (
  <LoaderCircle size={17} className="spin" aria-hidden="true" />
);
export function Notice({
  children,
  tone = "warning",
}: {
  children: ReactNode;
  tone?: "warning" | "error" | "info" | "success";
}) {
  return (
    <div
      className={"notice " + tone}
      role={tone === "error" ? "alert" : "status"}
    >
      <AlertCircle size={18} />
      <div>{children}</div>
    </div>
  );
}
export function Empty({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <Inbox size={28} />
      </div>
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}
export function Status({ value }: { value: string }) {
  return (
    <span className={"badge status-" + value}>
      <span className="dot" />
      {statusLabels[value] || value}
    </span>
  );
}
export function Modal({
  title,
  onClose,
  children,
  wide = false,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const el = dialog.current!;
    el.showModal();
    return () => {
      el.close();
    };
  }, []);
  return (
    <dialog
      ref={dialog}
      className={wide ? "modal wide" : "modal"}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      aria-label={title}
    >
      <div className="modal-header">
        <h2>{title}</h2>
        <button
          type="button"
          className="icon-button"
          onClick={onClose}
          aria-label="关闭窗口"
        >
          <X size={20} />
        </button>
      </div>
      <div className="modal-body">{children}</div>
    </dialog>
  );
}
export function EvidenceCard({
  item,
  index,
  cited = false,
  focusId,
}: {
  item: Evidence;
  index: number;
  cited?: boolean;
  focusId?: string;
}) {
  const el = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    if (focusId === item.chunk_id && el.current) {
      el.current.open = true;
      el.current.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [focusId, item.chunk_id]);
  return (
    <details ref={el} className={"evidence-card " + (cited ? "cited" : "")}>
      <summary>
        <span className="evidence-number">
          {String(index + 1).padStart(2, "0")}
        </span>
        <div className="grow">
          <strong>{item.document_title}</strong>
          <span className="meta">
            <FileText size={12} /> 分块 {item.chunk_index + 1} ·{" "}
            {modeLabels[item.match_type]}
            {cited && <b className="cited-label">已引用</b>}
          </span>
        </div>
        <ChevronDown size={16} />
      </summary>
      <div className="evidence-content">
        <p className="preserve">{item.content}</p>
        <div className="meta">
          匹配分 {item.score.toFixed(3)} · 匹配分不代表结论正确率
        </div>
      </div>
    </details>
  );
}
