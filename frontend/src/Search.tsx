import { useState, type FormEvent } from "react";
import { ArrowRight, Search as SearchIcon } from "lucide-react";
import { api } from "./api";
import { modeLabels } from "./lib";
import type { Evidence, SearchMode } from "./types";
import { Empty, EvidenceCard, Notice, Spinner } from "./components";

export function Search({ onAnalyze }: { onAnalyze: (query: string) => void }) {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<SearchMode>("keyword");
  const [topK, setTopK] = useState(5);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [results, setResults] = useState<Evidence[] | null>(null);
  const [executed, setExecuted] = useState<{
    query: string;
    mode: SearchMode;
  } | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!query.trim() || busy) return;
    setBusy(true);
    setError("");
    setResults(null);
    setExecuted(null);
    const search = { query: query.trim(), mode };
    try {
      const data = await api.search(search.query, search.mode, topK);
      setResults(data);
      setExecuted(search);
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="search-layout">
      <form className="panel search-form" onSubmit={submit}>
        <div className="panel-title">
          <SearchIcon size={19} />
          <h2>查找知识证据</h2>
        </div>
        <label className="field">
          问题或关键词
          <div className="search-input-wrap">
            <SearchIcon size={20} />
            <input
              required
              maxLength={2000}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="输入缺陷、工序或需要查证的问题，例如 AOI 误报复核"
            />
          </div>
        </label>
        <div className="search-controls">
          <label className="field">
            检索方式
            <select
              value={mode}
              onChange={(e) => setMode(e.target.value as SearchMode)}
            >
              {Object.entries(modeLabels).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="field narrow">
            结果上限
            <select
              value={topK}
              onChange={(e) => setTopK(Number(e.target.value))}
            >
              {[5, 10, 20, 50].map((n) => (
                <option key={n} value={n}>
                  {n} 条
                </option>
              ))}
            </select>
          </label>
          <button className="button primary" disabled={busy || !query.trim()}>
            {busy ? <Spinner /> : <SearchIcon size={16} />}
            {busy ? "正在检索…" : "检索证据"}
          </button>
        </div>
        <p className="hint">
          {mode === "keyword"
            ? "关键词检索直接查询本地知识库，不调用向量或生成模型。"
            : "语义和混合检索需要百炼向量服务，不会调用草稿生成模型。"}
        </p>
      </form>
      {error && <Notice tone="error">{error}</Notice>}
      {busy && (
        <div className="panel loading-panel">
          <Spinner />
          正在寻找相关证据…
        </div>
      )}
      {!busy && results === null && (
        <div className="panel">
          <Empty title="从一个具体问题开始">
            检索会返回知识库原文，便于先核查证据，再开展质量分析。
          </Empty>
        </div>
      )}
      {results !== null && executed && (
        <section className="panel search-results">
          <div className="panel-title">
            <h2>
              检索结果 <span className="count-tag">{results.length}</span>
            </h2>
            <small>{modeLabels[executed.mode]}</small>
            <button
              className="text-button"
              onClick={() => onAnalyze(executed.query)}
            >
              用此问题创建分析
              <ArrowRight size={14} />
            </button>
          </div>
          <p className="result-query">“{executed.query}”</p>
          {results.length ? (
            results.map((item, index) => (
              <EvidenceCard key={item.chunk_id} item={item} index={index} />
            ))
          ) : (
            <Empty title="没有找到相关证据">
              试着缩短问题、替换关键词，或先上传相关知识文档。
            </Empty>
          )}
        </section>
      )}
    </div>
  );
}
