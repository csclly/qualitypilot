import { useEffect, useState } from "react";
import {
  ArrowUpRight,
  BookOpen,
  ChevronRight,
  CircuitBoard,
  FileSearch,
  LayoutDashboard,
  Settings2,
  ShieldCheck,
} from "lucide-react";
import { api } from "./api";
import { readRecentIds, rememberRun } from "./lib";
import type { AgentRun } from "./types";
import { Modal, Notice } from "./components";
import { Analysis } from "./Analysis";
import { Knowledge } from "./Knowledge";
import { Search } from "./Search";

const pages = [
  { id: "analysis", label: "质量分析", icon: LayoutDashboard },
  { id: "knowledge", label: "知识库", icon: BookOpen },
  { id: "search", label: "证据检索", icon: FileSearch },
];
export default function App() {
  const [hash, setHash] = useState(location.hash);
  const [connected, setConnected] = useState<boolean | null>(null);
  const [supportsRules, setSupportsRules] = useState(false);
  const [total, setTotal] = useState<number | null>(null);
  const [recents, setRecents] = useState<AgentRun[]>([]);
  const [settings, setSettings] = useState(false);
  const [token, setToken] = useState("");
  const [seed, setSeed] = useState("");
  const segments = hash.replace(/^#\/?/, "").split("/");
  const page = pages.find((p) => p.id === segments[0]) || pages[0];
  const runId = page.id === "analysis" ? segments[1] || "" : "";
  useEffect(() => {
    const changed = () => setHash(location.hash);
    window.addEventListener("hashchange", changed);
    return () => window.removeEventListener("hashchange", changed);
  }, []);
  const refreshConnection = async () => {
    setConnected(null);
    const results = await Promise.allSettled([
      api.ready(),
      api.capabilities(),
      api.documents(),
    ]);
    setConnected(results[0].status === "fulfilled");
    setSupportsRules(results[1].status === "fulfilled" && results[1].value);
    if (results[2].status === "fulfilled") setTotal(results[2].value.total);
  };
  useEffect(() => {
    void refreshConnection();
    let live = true;
    void Promise.allSettled(readRecentIds().map((id) => api.run(id))).then(
      (results) => {
        if (live)
          setRecents(
            results.flatMap((r) => (r.status === "fulfilled" ? [r.value] : [])),
          );
      },
    );
    return () => {
      live = false;
    };
  }, []);
  const updateRun = (run: AgentRun) => {
    rememberRun(run.run_id);
    setRecents((prev) =>
      [run, ...prev.filter((r) => r.run_id !== run.run_id)].slice(0, 20),
    );
  };
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#/analysis">
          <span className="brand-mark">
            <CircuitBoard size={24} />
          </span>
          <span>
            QualityPilot<small>PCB 质量工作台</small>
          </span>
        </a>
        <div className="workspace-tag">
          <span className="dot" /> 本地工作空间 <span className="tag">DEV</span>
        </div>
        <div className="nav-caption">工作台</div>
        <nav aria-label="主要导航">
          {pages.map(({ id, label, icon: Icon }) => (
            <a
              key={id}
              href={"#/" + id}
              className={page.id === id ? "nav-link active" : "nav-link"}
              aria-current={page.id === id ? "page" : undefined}
            >
              <Icon size={19} />
              {label}
              {page.id === id && (
                <ChevronRight size={16} className="nav-arrow" />
              )}
            </a>
          ))}
        </nav>
        <div className="sidebar-note">
          <ShieldCheck size={21} />
          <strong>让每条建议有据可查</strong>
          <p>知识证据、分析草稿与人工复核，在同一个工作空间协作。</p>
        </div>
        <button className="settings-link" onClick={() => setSettings(true)}>
          <Settings2 size={18} />
          连接设置{token && <span className="dot" />}
        </button>
        <div className="sidebar-footer">
          <span className="avatar">QP</span>
          <div>
            QualityPilot<small>开发工作空间</small>
          </div>
          <span className="version">v0.1</span>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            工作空间 <ChevronRight size={14} />
            <strong>{page.label}</strong>
          </div>
          <div className="topbar-right">
            <span
              className={
                "connection " +
                (connected
                  ? "online"
                  : connected === null
                    ? "checking"
                    : "offline")
              }
            >
              <span className="dot" />
              {connected
                ? "数据库已连接"
                : connected === null
                  ? "正在连接"
                  : "后端连接异常"}
            </span>
            <button
              className="icon-button"
              aria-label="打开连接设置"
              onClick={() => setSettings(true)}
            >
              <Settings2 size={18} />
            </button>
          </div>
        </header>
        <main>
          <div className="page-heading">
            <div>
              <div className="eyebrow">QUALITY OPERATIONS</div>
              <h1>{page.label}</h1>
              <p>
                {page.id === "analysis"
                  ? "从现场问题出发，用证据形成可复核的质量建议。"
                  : page.id === "knowledge"
                    ? "管理质量规范与现场经验，为每一次分析提供依据。"
                    : "先找到相关资料，再判断证据是否支持你的问题。"}
              </p>
            </div>
            <span className="heading-date">
              {new Date().toLocaleDateString("zh-CN", {
                month: "long",
                day: "numeric",
                weekday: "long",
              })}
            </span>
          </div>
          {connected === false && (
            <Notice tone="error">
              暂时无法连接后端。请启动后端与数据库后，
              <button
                className="text-button"
                onClick={() => void refreshConnection()}
              >
                重新连接
              </button>
              。已有资料不会因此丢失。
            </Notice>
          )}
          {page.id === "analysis" && (
            <Analysis
              runId={runId}
              recents={recents}
              onRun={updateRun}
              token={token}
              onSettings={() => setSettings(true)}
              supportsRules={supportsRules}
              connected={connected}
              seed={seed}
              documentCount={total}
            />
          )}
          {page.id === "knowledge" && <Knowledge onCount={setTotal} />}
          {page.id === "search" && (
            <Search
              onAnalyze={(query) => {
                setSeed(query);
                location.hash = "/analysis";
              }}
            />
          )}
          <footer className="page-footer">
            <ShieldCheck size={13} />
            所有分析均需人工复核，审批不会自动修改生产系统。
            <a
              href="http://localhost:8000/docs"
              target="_blank"
              rel="noreferrer"
            >
              接口文档 <ArrowUpRight size={12} />
            </a>
          </footer>
        </main>
      </div>
      {settings && (
        <Modal title="连接设置" onClose={() => setSettings(false)}>
          <div className="settings-status">
            <span className="field-title">后端与数据库</span>
            <span
              className={"connection " + (connected ? "online" : "offline")}
            >
              <span className="dot" />
              {connected ? "已连接" : "未连接"}
            </span>
          </div>
          <p className="muted">
            前端通过本地开发代理连接后端。数据库就绪不代表云端模型在线。
          </p>
          <button
            className="button secondary"
            onClick={() => void refreshConnection()}
          >
            重新检查连接
          </button>
          <hr />
          <label className="field">
            审批凭据（可选）
            <input
              type="password"
              autoComplete="off"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="后端启用认证时填写 API Key 或访问令牌"
            />
          </label>
          <p className="hint">
            只在提交审批时使用，仅保存在当前页面内存，刷新后清除。这里不填写百炼或云端模型密钥。
          </p>
          <div className="modal-actions">
            <button className="button secondary" onClick={() => setToken("")}>
              清除凭据
            </button>
            <button
              className="button primary"
              onClick={() => setSettings(false)}
            >
              完成
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
