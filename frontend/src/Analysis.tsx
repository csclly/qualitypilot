import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  ArrowRight,
  Check,
  CheckCheck,
  ClipboardList,
  Clock3,
  FileText,
  FolderSearch,
  History,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import { api, ApiError } from "./api";
import { approvalKey, formatDate, isUUID, modeLabels } from "./lib";
import type { AgentRun, RunError, SearchMode } from "./types";
import {
  Empty,
  EvidenceCard,
  Modal,
  Notice,
  Spinner,
  Status,
} from "./components";

interface Props {
  runId: string;
  recents: AgentRun[];
  onRun: (run: AgentRun) => void;
  token: string;
  onSettings: () => void;
  supportsRules: boolean;
  connected: boolean | null;
  seed: string;
  documentCount: number | null;
}
export function Analysis({
  runId,
  recents,
  onRun,
  token,
  onSettings,
  supportsRules,
  connected,
  seed,
  documentCount,
}: Props) {
  const [question, setQuestion] = useState(seed);
  const [mode, setMode] = useState<SearchMode>("keyword");
  const [useModel, setUseModel] = useState(false);
  const [topK, setTopK] = useState(3);
  const [busy, setBusy] = useState(false);
  const [loadingRun, setLoadingRun] = useState(false);
  const [run, setRun] = useState<AgentRun | null>(null);
  const [error, setError] = useState("");
  const [errorRunId, setErrorRunId] = useState("");
  const [runErrors, setRunErrors] = useState<RunError[] | null>(null);
  const [lookup, setLookup] = useState("");
  const [focusId, setFocusId] = useState("");
  const [decision, setDecision] = useState<boolean | null>(null);
  const [actor, setActor] = useState("");
  const [comment, setComment] = useState("");
  const [checked, setChecked] = useState(false);
  const [approvalBusy, setApprovalBusy] = useState(false);
  const [approvalError, setApprovalError] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const callback = useRef(onRun);
  callback.current = onRun;
  const attempt = useRef<{ key: string; id: string } | null>(null);
  const submitting = useRef(false);
  useEffect(() => {
    setQuestion(seed);
  }, [seed]);
  useEffect(() => {
    if (!busy) return;
    setElapsed(0);
    const timer = setInterval(() => setElapsed((v) => v + 1), 1000);
    return () => clearInterval(timer);
  }, [busy]);
  useEffect(() => {
    setError("");
    setRunErrors(null);
    setErrorRunId("");
    if (!runId) {
      setRun(null);
      setLoadingRun(false);
      return;
    }
    if (!isUUID(runId)) {
      setRun(null);
      setError("运行编号格式不正确，请复制完整编号。");
      return;
    }
    const controller = new AbortController();
    setLoadingRun(true);
    setRun(null);
    void api
      .run(runId, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) {
          setRun(data);
          callback.current(data);
        }
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingRun(false);
      });
    return () => controller.abort();
  }, [runId]);
  const refresh = async () => {
    if (!run) return;
    setLoadingRun(true);
    setError("");
    try {
      const data = await api.run(run.run_id);
      setRun(data);
      onRun(data);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoadingRun(false);
    }
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (submitting.current) return;
    if (!question.trim()) {
      setError("请先描述需要分析的质量问题。");
      return;
    }
    if (!useModel && !supportsRules) {
      setError("请先重启后端并重新检查连接，以启用规则草稿模式。");
      return;
    }
    submitting.current = true;
    setBusy(true);
    setError("");
    setErrorRunId("");
    setRunErrors(null);
    const originHash = location.hash;
    try {
      const data = await api.createRun(question.trim(), mode, topK, useModel);
      onRun(data);
      if (location.hash === originHash) {
        setRun(data);
        location.hash = "/analysis/" + data.run_id;
      }
    } catch (e) {
      setError((e as Error).message);
      if (e instanceof ApiError && e.runId) setErrorRunId(e.runId);
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  };
  const submitApproval = async () => {
    if (!run || decision == null || approvalBusy || !checked) return;
    if (!actor.trim() && !token.trim()) {
      setApprovalError("请填写复核人，或在连接设置中提供审批凭据。");
      return;
    }
    const actorId = actor.trim() || "unverified";
    const key = approvalKey(run, decision, actorId, comment);
    if (attempt.current?.key !== key)
      attempt.current = { key, id: crypto.randomUUID() };
    setApprovalBusy(true);
    setApprovalError("");
    try {
      const data = await api.approve(
        run.run_id,
        {
          approved: decision,
          actor_id: actorId,
          comment: comment.trim() || null,
          request_id: attempt.current.id,
        },
        token,
      );
      setRun(data);
      onRun(data);
      setDecision(null);
      setComment("");
      setChecked(false);
    } catch (e) {
      setApprovalError(
        (e as Error).message +
          " 如提交结果不明确，请关闭窗口并刷新状态；重试同一决定会复用请求编号。",
      );
    } finally {
      setApprovalBusy(false);
    }
  };
  return (
    <>
      <div className="overview-strip">
        <span>
          <FolderSearch size={16} />
          知识库文档 <strong>{documentCount ?? "—"}</strong>
        </span>
        <span>
          <History size={16} />
          本机最近打开 <strong>{recents.length}</strong>
        </span>
        <span className="strip-tip">
          <ShieldCheck size={16} />
          生成草稿后，始终由人做决定
        </span>
      </div>
      <div className="analysis-grid">
        <section className="input-column">
          <form className="panel question-panel" onSubmit={submit}>
            <div className="panel-title">
              <span className="title-icon">
                <ClipboardList size={18} />
              </span>
              <h2>发起质量分析</h2>
              <span className="quiet-tag">新分析</span>
            </div>
            <label className="field question-label">
              描述现场问题
              <textarea
                maxLength={2000}
                rows={6}
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="例如：AOI 发现某批 PCB 短路报点突然增加，目前没有 MES 和 Gerber 数据，应该如何排查？"
                required
              />
            </label>
            <div className="input-caption">
              描述现象、批次与已有证据<span>{question.length} / 2000</span>
            </div>
            <div className="example-chips">
              <button
                type="button"
                onClick={() =>
                  setQuestion("AOI发现PCB桥接或短路报点增加，应该如何排查？")
                }
              >
                桥连 / 短路
              </button>
              <button
                type="button"
                onClick={() =>
                  setQuestion(
                    "AOI连续出现同类误报，应该如何复核并验证检测程序？",
                  )
                }
              >
                AOI 误报
              </button>
              <button
                type="button"
                onClick={() =>
                  setQuestion("回流后出现元件偏移，应该收集哪些证据？")
                }
              >
                元件偏移
              </button>
            </div>
            <div className="form-divider" />
            <span className="field-title">草稿方式</span>
            <div className="segmented" role="group" aria-label="草稿方式">
              <button
                type="button"
                aria-pressed={!useModel}
                className={!useModel ? "selected" : ""}
                onClick={() => setUseModel(false)}
              >
                <FileText size={15} />
                规则草稿
              </button>
              <button
                type="button"
                aria-pressed={useModel}
                className={useModel ? "selected" : ""}
                onClick={() => setUseModel(true)}
              >
                <Sparkles size={15} />
                模型分析
              </button>
            </div>
            <p className="hint">
              {useModel
                ? "调用已配置的生成模型。模型需在线，调用失败时会明确标记规则兜底。"
                : "按检索证据整理草稿，不调用生成模型。适合云端模型停机期间使用。"}
            </p>
            {!useModel && connected && !supportsRules && (
              <Notice>
                后端需重启后才能启用规则草稿。重启后请在连接设置中重新检查。
              </Notice>
            )}
            <div className="field-row">
              <label className="field">
                检索方式
                <select
                  value={mode}
                  onChange={(e) => setMode(e.target.value as SearchMode)}
                >
                  {Object.entries(modeLabels).map(([id, label]) => (
                    <option key={id} value={id}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field narrow">
                证据上限
                <select
                  value={topK}
                  onChange={(e) => setTopK(Number(e.target.value))}
                >
                  {[3, 5, 10, 20].map((n) => (
                    <option key={n} value={n}>
                      {n} 条
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {mode !== "keyword" && (
              <p className="hint">
                语义和混合检索需要百炼向量服务，与生成模型独立。
              </p>
            )}
            <button
              className="button primary full"
              disabled={
                busy || connected !== true || (!useModel && !supportsRules)
              }
            >
              {busy ? (
                <>
                  <Spinner />
                  正在整理 · {elapsed}s
                </>
              ) : (
                <>
                  {useModel ? "开始模型分析" : "生成规则草稿"}
                  <ArrowRight size={17} />
                </>
              )}
            </button>
            {busy && (
              <p className="hint" role="status">
                正在处理本次请求，请勿重复提交。模型分析可能需要较长时间。
              </p>
            )}
          </form>
          <section className="panel recent-panel">
            <div className="panel-title">
              <History size={17} />
              <h2>最近打开</h2>
              <small>仅本机</small>
            </div>
            <form
              className="lookup"
              onSubmit={(e) => {
                e.preventDefault();
                if (isUUID(lookup.trim()))
                  location.hash = "/analysis/" + lookup.trim();
                else setError("运行编号格式不正确，请复制完整编号。");
              }}
            >
              <label className="sr-only" htmlFor="run-lookup">
                运行编号
              </label>
              <input
                id="run-lookup"
                value={lookup}
                onChange={(e) => setLookup(e.target.value)}
                placeholder="粘贴运行编号，找回分析"
              />
              <button aria-label="查找运行" className="icon-button">
                <Search size={17} />
              </button>
            </form>
            {recents.length === 0 ? (
              <p className="recent-empty">
                还没有打开的分析。创建草稿后会出现在这里，也可通过编号找回已有记录。
              </p>
            ) : (
              <div className="recent-list">
                {recents.slice(0, 6).map((r) => (
                  <a
                    key={r.run_id}
                    href={"#/analysis/" + r.run_id}
                    className={
                      "recent-item " + (runId === r.run_id ? "selected" : "")
                    }
                  >
                    <span className="recent-question">{r.question}</span>
                    <Status value={r.status} />
                  </a>
                ))}
              </div>
            )}
          </section>
        </section>
        <section className="result-column">
          {error && (
            <Notice tone="error">
              {error}
              {errorRunId && (
                <>
                  <p className="mono">运行编号：{errorRunId}</p>
                  <button
                    className="text-button"
                    onClick={() =>
                      void api
                        .errors(errorRunId)
                        .then(setRunErrors)
                        .catch((e) => setError(e.message))
                    }
                  >
                    查看错误记录
                  </button>
                </>
              )}
            </Notice>
          )}
          {runErrors && (
            <Notice tone="error">
              {runErrors.length
                ? runErrors.map((e) => <p key={e.id}>{e.message}</p>)
                : "此运行没有可查询的错误记录。"}
            </Notice>
          )}
          {!run && !loadingRun && (
            <div className="panel result-welcome">
              <div className="welcome-icon">
                <CircuitIllustration />
              </div>
              <span className="eyebrow">EVIDENCE FIRST</span>
              <h2>让质量判断，有据可循</h2>
              <p>
                描述一个现场问题，系统会查找相关知识，
                <br />
                整理建议与风险，并保留每一条引用。
              </p>
              <div className="welcome-steps">
                <span>
                  <Search size={17} />
                  检索证据
                </span>
                <Chevron />
                <span>
                  <FileText size={17} />
                  形成草稿
                </span>
                <Chevron />
                <span>
                  <ShieldCheck size={17} />
                  人工复核
                </span>
              </div>
              <div className="welcome-note">
                <span className="dot" />
                从左侧开始 · 云端停机时可使用规则草稿
              </div>
            </div>
          )}
          {loadingRun && (
            <div className="panel loading-panel" role="status">
              <Spinner />
              正在读取分析记录…
            </div>
          )}
          {run && !loadingRun && (
            <>
              <section className="panel draft-panel">
                <div className="panel-title">
                  <span className="title-icon">
                    <FileText size={18} />
                  </span>
                  <h2>
                    {run.status === "completed" ? "已批准的答复" : "分析草稿"}
                  </h2>
                  <Status value={run.status} />
                  <button
                    className="icon-button"
                    aria-label="刷新分析状态"
                    onClick={() => void refresh()}
                  >
                    <RefreshCw size={16} />
                  </button>
                </div>
                <div className="run-question">{run.question}</div>
                <div className="run-meta">
                  <span>
                    {modeLabels[run.search_mode]} · {run.evidence.length} 条证据
                  </span>
                  <span className="mono" title={run.run_id}>
                    运行 {run.run_id.slice(0, 8)}
                  </span>
                </div>
                {run.draft ? (
                  <>
                    <div
                      className={
                        "generation-label " +
                        (run.draft.generation_mode === "model"
                          ? "model"
                          : "rules")
                      }
                    >
                      {run.draft.generation_mode === "model" ? (
                        <Sparkles size={15} />
                      ) : (
                        <ClipboardList size={15} />
                      )}
                      {run.draft.generation_mode === "model"
                        ? "模型生成 · 待核验内容"
                        : "规则草稿 · 非模型生成"}
                    </div>
                    <h3 className="section-label">分析摘要</h3>
                    <p className="draft-summary preserve">
                      {run.draft.summary}
                    </p>
                    <h3 className="section-label">建议复核事项</h3>
                    {run.draft.suggested_actions.length ? (
                      <ol className="action-list">
                        {run.draft.suggested_actions.map((action, i) => (
                          <li key={i}>
                            <span>{String(i + 1).padStart(2, "0")}</span>
                            <p>{action}</p>
                          </li>
                        ))}
                      </ol>
                    ) : (
                      <p className="muted">暂无足够证据形成具体处置建议。</p>
                    )}
                    {run.draft.risk_notes.length > 0 && (
                      <div className="risk-box">
                        <strong>
                          <ShieldCheck size={16} />
                          风险与限制
                        </strong>
                        <ul>
                          {run.draft.risk_notes.map((note, i) => (
                            <li key={i}>{note}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    <div className="citation-row">
                      <span>引用证据</span>
                      {run.draft.citations.length ? (
                        run.draft.citations.map((id) => {
                          const index = run.evidence.findIndex(
                            (e) => e.chunk_id === id,
                          );
                          return index >= 0 ? (
                            <button key={id} onClick={() => setFocusId(id)}>
                              证据 {index + 1}
                              <ArrowRight size={12} />
                            </button>
                          ) : (
                            <span key={id}>未找到对应证据</span>
                          );
                        })
                      ) : (
                        <span className="muted">无知识引用</span>
                      )}
                    </div>
                  </>
                ) : (
                  <Notice tone="info">
                    当前运行尚未生成草稿，请刷新状态。
                  </Notice>
                )}
                {run.approval_required && run.draft && (
                  <div className="approval-bar">
                    <div>
                      <Clock3 size={18} />
                      <span>
                        等待人工复核<small>请核对建议与引用是否一致</small>
                      </span>
                    </div>
                    <button
                      className="button secondary small"
                      onClick={() => {
                        setDecision(false);
                        setApprovalError("");
                        setChecked(false);
                      }}
                    >
                      拒绝
                    </button>
                    <button
                      className="button primary small"
                      onClick={() => {
                        setDecision(true);
                        setApprovalError("");
                        setChecked(false);
                      }}
                    >
                      <Check size={16} />
                      批准草稿
                    </button>
                  </div>
                )}
                {run.approval_event && (
                  <div className="audit-block">
                    <CheckCheck size={18} />
                    <div>
                      <strong>
                        {run.approval_event.approved ? "已批准" : "已拒绝"} ·{" "}
                        {run.approval_event.actor_id}
                      </strong>
                      <p>
                        {formatDate(run.approval_event.occurred_at)} ·{" "}
                        {run.approval_event.actor_authenticated
                          ? "已认证身份"
                          : "自填身份，未经认证"}
                      </p>
                      {run.approval_event.comment && (
                        <p>{run.approval_event.comment}</p>
                      )}
                    </div>
                  </div>
                )}
                <details className="record-id">
                  <summary>完整运行编号</summary>
                  <code>{run.run_id}</code>
                </details>
              </section>
              <section className="panel evidence-panel">
                <div className="panel-title">
                  <FolderSearch size={18} />
                  <h2>知识证据</h2>
                  <span className="count-tag">{run.evidence.length}</span>
                  <small>展开查看原文</small>
                </div>
                {run.evidence.length ? (
                  run.evidence.map((item, index) => (
                    <EvidenceCard
                      key={item.chunk_id}
                      item={item}
                      index={index}
                      cited={run.draft?.citations.includes(item.chunk_id)}
                      focusId={focusId}
                    />
                  ))
                ) : (
                  <Empty title="没有检索到证据">
                    请补充知识文档，或换用与知识库内容相关的问题。
                  </Empty>
                )}
              </section>
              <section className="business-section">
                <h3>
                  业务记录 <span>{run.business_records.length}</span>
                </h3>
                {run.business_records.length ? (
                  run.business_records.map((r) => (
                    <div
                      className="panel business-record"
                      key={r.tool_name + r.record_id}
                    >
                      <strong>
                        {r.system.toUpperCase()} · {r.record_id}
                      </strong>
                      <p>{r.summary}</p>
                    </div>
                  ))
                ) : (
                  <p>本次没有业务记录，分析不包含实时 MES / QMS 数据。</p>
                )}
                {run.business_tool_failures.map((f, i) => (
                  <Notice key={i}>
                    {f.tool_name}：{f.message}
                  </Notice>
                ))}
              </section>
            </>
          )}
        </section>
      </div>
      {decision !== null && run && (
        <Modal
          title={decision ? "确认批准草稿" : "确认拒绝草稿"}
          onClose={() => {
            if (!approvalBusy) setDecision(null);
          }}
        >
          <p className="muted">
            此决定将写入审批记录。批准后只形成最终答复，不会自动变更生产系统。
          </p>
          <label className="field">
            复核人{!token && "（必填）"}
            <input
              maxLength={255}
              value={actor}
              onChange={(e) => setActor(e.target.value)}
              placeholder={
                token ? "认证成功后以后端身份为准" : "请输入姓名或工号"
              }
            />
          </label>
          {!token && (
            <p className="hint">
              未提供审批凭据时，此姓名仅作为未经认证的身份声明。
              <button className="text-button" onClick={onSettings}>
                连接设置
              </button>
            </p>
          )}
          <label className="field">
            复核备注
            <textarea
              rows={3}
              maxLength={2000}
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder="记录核验依据或拒绝原因"
            />
          </label>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={checked}
              onChange={(e) => setChecked(e.target.checked)}
            />
            我已核对草稿、引用证据及风险说明，确认提交此决定。
          </label>
          {approvalError && <Notice tone="error">{approvalError}</Notice>}
          <div className="modal-actions">
            <button
              className="button secondary"
              disabled={approvalBusy}
              onClick={() => setDecision(null)}
            >
              取消
            </button>
            <button
              className={"button " + (decision ? "primary" : "danger")}
              disabled={
                approvalBusy || !checked || (!actor.trim() && !token.trim())
              }
              onClick={() => void submitApproval()}
            >
              {approvalBusy ? (
                <Spinner />
              ) : decision ? (
                <Check size={16} />
              ) : (
                <X size={16} />
              )}
              {decision ? "确认批准" : "确认拒绝"}
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
const Chevron = () => <ArrowRight size={13} className="step-arrow" />;
const CircuitIllustration = () => <ClipboardList size={40} strokeWidth={1.3} />;
