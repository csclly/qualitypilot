import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  ArrowLeft,
  ArrowRight,
  BookOpen,
  FileText,
  RefreshCw,
  Upload,
  X,
} from "lucide-react";
import { api } from "./api";
import { formatDate, formatSize, validateUpload } from "./lib";
import type { Chunk, KnowledgeDocument, Page } from "./types";
import { Empty, Modal, Notice, Spinner, Status } from "./components";

export function Knowledge({ onCount }: { onCount: (n: number) => void }) {
  const [page, setPage] = useState<Page<KnowledgeDocument>>({
    items: [],
    total: 0,
  });
  const [offset, setOffset] = useState(0);
  const [version, setVersion] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [selected, setSelected] = useState<KnowledgeDocument | null>(null);
  const [message, setMessage] = useState("");
  useEffect(() => {
    let current = true;
    setLoading(true);
    setError("");
    void api
      .documents(offset)
      .then((data) => {
        if (current) {
          setPage(data);
          onCount(data.total);
        }
      })
      .catch((e) => {
        if (current) setError(e.message);
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [offset, version, onCount]);
  return (
    <>
      <div className="section-toolbar">
        <div className="library-total">
          <span className="title-icon">
            <BookOpen size={21} />
          </span>
          <div>
            <strong>
              {loading ? "正在读取…" : page.total + " 份知识文档"}
            </strong>
            <p>质量规范、复核流程与经验记录</p>
          </div>
        </div>
        <div className="toolbar-actions">
          <button
            className="button secondary"
            onClick={() => setVersion((v) => v + 1)}
            disabled={loading}
          >
            <RefreshCw size={16} />
            刷新
          </button>
          <button
            className="button primary"
            onClick={() => setUploadOpen(true)}
          >
            <Upload size={17} />
            上传文档
          </button>
        </div>
      </div>
      {error && <Notice tone="error">{error}</Notice>}
      {message && <Notice tone="success">{message}</Notice>}
      <div className="panel library-panel">
        {loading ? (
          <div className="loading-panel">
            <Spinner />
            正在读取知识库…
          </div>
        ) : page.items.length ? (
          <>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>文档名称</th>
                    <th>状态</th>
                    <th>分块</th>
                    <th>文件大小</th>
                    <th>添加时间</th>
                    <th>
                      <span className="sr-only">操作</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((doc) => (
                    <tr key={doc.id}>
                      <td>
                        <button
                          className="document-link"
                          onClick={() => setSelected(doc)}
                        >
                          <span className="file-icon">
                            <FileText size={20} />
                          </span>
                          <span>
                            <strong>{doc.title}</strong>
                            <small>
                              {doc.original_filename || "手动登记的文档信息"}
                            </small>
                          </span>
                        </button>
                      </td>
                      <td>
                        <Status value={doc.status} />
                      </td>
                      <td>{doc.chunk_count}</td>
                      <td>{formatSize(doc.file_size)}</td>
                      <td className="date-cell">
                        {formatDate(doc.created_at)}
                      </td>
                      <td>
                        <button
                          className="text-button"
                          onClick={() => setSelected(doc)}
                        >
                          查看 <ArrowRight size={14} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="pagination">
              <span>
                共 {page.total} 份 · 第 {Math.floor(offset / 10) + 1} 页
              </span>
              <button
                className="button secondary small"
                disabled={offset === 0}
                onClick={() => setOffset((v) => Math.max(0, v - 10))}
              >
                <ArrowLeft size={14} />
                上一页
              </button>
              <button
                className="button secondary small"
                disabled={offset + page.items.length >= page.total}
                onClick={() => setOffset((v) => v + 10)}
              >
                下一页
                <ArrowRight size={14} />
              </button>
            </div>
          </>
        ) : (
          <Empty title={error ? "知识库暂时无法读取" : "知识库还没有文档"}>
            {error
              ? "连接恢复后点击刷新，重新读取已有资料。"
              : "上传第一份质量规范或复核流程，让分析有据可依。"}
          </Empty>
        )}
      </div>
      <div className="library-note">
        <FileText size={18} />
        <div>
          <strong>让知识保持可追溯</strong>
          <p>
            支持 TXT、Markdown、PDF 与 DOCX，单文件不超过 20
            MB。上传会提取文字并生成检索向量，需要百炼向量服务可用。扫描 PDF
            暂不支持文字识别。
          </p>
        </div>
      </div>
      {uploadOpen && (
        <UploadDialog
          onClose={() => setUploadOpen(false)}
          onUploaded={(doc) => {
            setUploadOpen(false);
            setOffset(0);
            setVersion((v) => v + 1);
            setMessage("「" + doc.title + "」已入库，可查看分块并进行检索。");
          }}
        />
      )}
      {selected && (
        <DocumentDialog document={selected} onClose={() => setSelected(null)} />
      )}
    </>
  );
}
function UploadDialog({
  onClose,
  onUploaded,
}: {
  onClose: () => void;
  onUploaded: (d: KnowledgeDocument) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const choose = (next?: File) => {
    if (!next || busy) return;
    const invalid = validateUpload(next);
    setError(invalid || "");
    if (invalid) {
      setFile(null);
      return;
    }
    setFile(next);
    setTitle(next.name.replace(/\.[^.]+$/, "").slice(0, 255));
  };
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!file || busy) return;
    setBusy(true);
    setError("");
    try {
      onUploaded(await api.upload(file, title));
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      title="上传知识文档"
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      <form onSubmit={submit}>
        <div
          className={"upload-zone " + (dragging ? "dragging" : "")}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            choose(e.dataTransfer.files[0]);
          }}
        >
          <Upload size={30} strokeWidth={1.5} />
          <strong>{file ? file.name : "拖放文档到这里"}</strong>
          <span>
            {file
              ? formatSize(file.size)
              : "TXT · Markdown · PDF · DOCX / 最大 20 MB"}
          </span>
          <button
            type="button"
            className="button secondary small"
            disabled={busy}
            onClick={() => input.current?.click()}
          >
            {file ? "重新选择" : "选择文件"}
          </button>
          <input
            ref={input}
            className="sr-only"
            type="file"
            accept=".txt,.md,.pdf,.docx"
            aria-label="选择知识文档"
            onChange={(e) => choose(e.target.files?.[0])}
          />
        </div>
        <label className="field">
          文档标题
          <input
            value={title}
            maxLength={255}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="例如：AOI 桥连缺陷复核规范"
            disabled={busy}
          />
        </label>
        <p className="hint">
          原文件、文字分块与向量将一同关联到这份文档。支持可提取文字的文件，不支持扫描件
          OCR。
        </p>
        {error && <Notice tone="error">{error}</Notice>}
        <div className="modal-actions">
          <button
            type="button"
            className="button secondary"
            disabled={busy}
            onClick={onClose}
          >
            取消
          </button>
          <button className="button primary" disabled={!file || busy}>
            {busy ? (
              <>
                <Spinner />
                解析并入库中…
              </>
            ) : (
              <>
                <Upload size={16} />
                上传并入库
              </>
            )}
          </button>
        </div>
      </form>
    </Modal>
  );
}
function DocumentDialog({
  document: doc,
  onClose,
}: {
  document: KnowledgeDocument;
  onClose: () => void;
}) {
  const [page, setPage] = useState<Page<Chunk>>({ items: [], total: 0 });
  const [offset, setOffset] = useState(0);
  const [version, setVersion] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => {
    let current = true;
    setLoading(true);
    setError("");
    void api
      .chunks(doc.id, offset)
      .then((data) => {
        if (current) setPage(data);
      })
      .catch((e) => {
        if (current) setError(e.message);
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [doc.id, offset, version]);
  const backfill = async () => {
    setBusy(true);
    setError("");
    try {
      const result = await api.backfill(doc.id);
      setMessage(
        "新增向量 " +
          result.embedded_chunks +
          " 条，已有向量跳过 " +
          result.skipped_chunks +
          " 条。",
      );
      setVersion((v) => v + 1);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={doc.title} wide onClose={onClose}>
      <div className="document-meta">
        <Status value={doc.status} />
        <span>{doc.original_filename || "手动登记"}</span>
        <span>{formatSize(doc.file_size)}</span>
      </div>
      <div className="section-toolbar compact">
        <h3>
          原文分块 <span className="count-tag">{page.total}</span>
        </h3>
        <button
          className="button secondary small"
          disabled={busy || loading || page.total === 0}
          onClick={() => void backfill()}
        >
          {busy ? <Spinner /> : <RefreshCw size={14} />}补全缺失向量
        </button>
      </div>
      <p className="hint">补全向量会调用百炼向量服务；已有向量不会重复生成。</p>
      {error && <Notice tone="error">{error}</Notice>}
      {message && <Notice tone="success">{message}</Notice>}
      {loading ? (
        <div className="loading-panel">
          <Spinner />
          正在读取分块…
        </div>
      ) : page.items.length ? (
        page.items.map((chunk) => (
          <section key={chunk.id} className="chunk-card">
            <div>
              <strong>分块 {chunk.chunk_index + 1}</strong>
              <span
                className={
                  "badge " +
                  (chunk.has_embedding ? "status-ready" : "status-created")
                }
              >
                {chunk.has_embedding
                  ? "已向量化 · " + chunk.embedding_dimension + " 维"
                  : "尚无向量"}
              </span>
            </div>
            <p className="preserve">{chunk.content}</p>
          </section>
        ))
      ) : (
        <Empty title="此文档尚无文字分块">
          仅登记文档信息不会自动生成内容。请上传有文字内容的文件。
        </Empty>
      )}
      {page.total > 20 && (
        <div className="pagination">
          <span>共 {page.total} 个分块</span>
          <button
            className="button secondary small"
            disabled={offset === 0 || loading}
            onClick={() => setOffset((v) => Math.max(0, v - 20))}
          >
            上一页
          </button>
          <button
            className="button secondary small"
            disabled={offset + page.items.length >= page.total || loading}
            onClick={() => setOffset((v) => v + 20)}
          >
            下一页
          </button>
        </div>
      )}
    </Modal>
  );
}
