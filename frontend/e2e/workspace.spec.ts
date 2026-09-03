import { expect, test, type Page } from "@playwright/test";
const id = "11111111-1111-4111-8111-111111111111";
const chunkId = "22222222-2222-4222-8222-222222222222";
const docId = "33333333-3333-4333-8333-333333333333";
const document = {
  id: docId,
  title: "测试用 AOI 复核规范",
  source_type: "upload",
  status: "ready",
  original_filename: "AOI.md",
  file_size: 1200,
  chunk_count: 1,
  created_at: "2026-09-03T03:00:00Z",
  processed_at: "2026-09-03T03:00:00Z",
};
const evidence = {
  chunk_id: chunkId,
  document_id: docId,
  document_title: document.title,
  original_filename: "AOI.md",
  chunk_index: 0,
  content: "测试证据：先复核原始图像，不能直接放宽阈值。",
  score: 0.2,
  match_type: "keyword",
  vector_score: null,
  keyword_score: 0.2,
};
const pending = {
  run_id: id,
  question: "测试 AOI 复核",
  search_mode: "keyword",
  top_k: 3,
  status: "pending_approval",
  evidence: [evidence],
  business_records: [],
  business_tool_failures: [],
  draft: {
    summary: "测试规则草稿",
    suggested_actions: ["核查原始图像。"],
    risk_notes: ["必须人工复核。"],
    citations: [chunkId],
    business_record_references: [],
    generation_mode: "deterministic_fallback",
  },
  final_response: null,
  approval_required: true,
  approved: null,
  approval_event: null,
};

async function mockBase(page: Page, supportsRules = true) {
  await page.route("**/openapi.json", (route) =>
    route.fulfill({
      json: {
        components: {
          schemas: {
            AgentRunCreate: {
              properties: supportsRules ? { use_model: {} } : {},
            },
          },
        },
      },
    }),
  );
  await page.route("**/api/v1/ready", (route) =>
    route.fulfill({ json: { status: "ready" } }),
  );
  await page.route("**/api/v1/knowledge/documents?*", (route) =>
    route.fulfill({ json: [document], headers: { "X-Total-Count": "1" } }),
  );
  await page.route("**/api/v1/knowledge/documents/*/chunks?*", (route) =>
    route.fulfill({
      json: [
        {
          id: chunkId,
          document_id: docId,
          chunk_index: 0,
          content: evidence.content,
          has_embedding: true,
          embedding_dimension: 1024,
        },
      ],
      headers: { "X-Total-Count": "1" },
    }),
  );
  await page.route("**/api/v1/agent/runs/" + id, (route) =>
    route.fulfill({ json: pending }),
  );
}
test("rules mode sends no-model request; history survives reload; text is rendered safely", async ({
  page,
}) => {
  await mockBase(page);
  const payloads: unknown[] = [];
  await page.route("**/api/v1/agent/runs", (route) => {
    payloads.push(route.request().postDataJSON());
    return route.fulfill({ json: pending, status: 202 });
  });
  await page.goto("/");
  await expect(page.getByText("数据库已连接")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "规则草稿", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "桥连 / 短路" }).click();
  await page.getByRole("button", { name: "生成规则草稿" }).click();
  await expect(page.getByText("规则草稿 · 非模型生成")).toBeVisible();
  expect(payloads).toEqual([
    {
      question: "AOI发现PCB桥接或短路报点增加，应该如何排查？",
      search_mode: "keyword",
      top_k: 3,
      use_model: false,
    },
  ]);
  await page.getByRole("button", { name: "证据 1", exact: true }).click();
  await expect(page.getByText(evidence.content, { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText("测试规则草稿")).toBeVisible();
  await expect(page.getByRole("link", { name: /测试 AOI 复核/ })).toBeVisible();
  await page.screenshot({
    path: "test-results/analysis-desktop.png",
    fullPage: true,
  });
});
test("old backend cannot silently treat a rules request as a model call", async ({
  page,
}) => {
  await mockBase(page, false);
  await page.goto("/");
  await expect(
    page.getByText(
      "后端需重启后才能启用规则草稿。重启后请在连接设置中重新检查。",
    ),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "生成规则草稿" }),
  ).toBeDisabled();
});
test("knowledge browsing, upload error and successful retry use actual API states", async ({
  page,
}) => {
  await mockBase(page);
  let uploads = 0;
  await page.route("**/api/v1/knowledge/documents/upload", (route) => {
    uploads++;
    return uploads === 1
      ? route.fulfill({
          status: 503,
          json: { detail: "测试：向量服务暂时不可用" },
        })
      : route.fulfill({ status: 201, json: document });
  });
  await page.goto("/#/knowledge");
  await page.getByRole("button", { name: /测试用 AOI 复核规范/ }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByText(evidence.content)).toBeVisible();
  await page.getByRole("button", { name: "关闭窗口" }).click();
  await page.getByRole("button", { name: "上传文档", exact: true }).click();
  await page
    .getByLabel("选择知识文档")
    .setInputFiles({
      name: "AOI.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("前端测试文档"),
    });
  await page.getByRole("button", { name: "上传并入库" }).click();
  await expect(page.getByText("测试：向量服务暂时不可用")).toBeVisible();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "上传并入库" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByText(/已入库，可查看分块/)).toBeVisible();
  expect(uploads).toBe(2);
});
test("search returns evidence and transfers the question; mobile stays within viewport", async ({
  page,
}) => {
  await mockBase(page);
  await page.route("**/api/v1/knowledge/search", (route) =>
    route.fulfill({
      json: [
        {
          ...evidence,
          content: "<img src=x onerror=alert(1)>测试内容只应作为文字展示",
        },
      ],
    }),
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/#/search");
  await page
    .getByRole("textbox", { name: "问题或关键词" })
    .fill("AOI 误报复核");
  await page.getByRole("button", { name: "检索证据", exact: true }).click();
  await expect(page.getByText("测试用 AOI 复核规范")).toBeVisible();
  await page.getByText("测试用 AOI 复核规范").click();
  await expect(
    page.getByText("<img src=x onerror=alert(1)>测试内容只应作为文字展示"),
  ).toBeVisible();
  await expect(page.locator(".evidence-content img")).toHaveCount(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "test-results/search-mobile.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "用此问题创建分析" }).click();
  await expect(page.getByRole("textbox", { name: "描述现场问题" })).toHaveValue(
    "AOI 误报复核",
  );
});
test("approval requires review and preserves request ID across uncertain retry", async ({
  page,
}) => {
  await mockBase(page);
  const requests: { request_id: string; approved: boolean }[] = [];
  await page.route("**/api/v1/agent/runs/" + id + "/approval", (route) => {
    const body = route.request().postDataJSON();
    requests.push(body);
    return requests.length === 1
      ? route.fulfill({ status: 503, json: { detail: "测试：暂时无法提交" } })
      : route.fulfill({
          json: {
            ...pending,
            status: "completed",
            approved: true,
            approval_required: false,
            final_response: pending.draft,
            approval_event: {
              id: body.request_id,
              actor_id: body.actor_id,
              actor_authenticated: false,
              auth_method: null,
              approved: true,
              comment: body.comment,
              occurred_at: "2026-09-03T06:00:00Z",
            },
          },
        });
  });
  await page.goto("/#/analysis/" + id);
  await page.getByRole("button", { name: "批准草稿", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "确认批准", exact: true }),
  ).toBeDisabled();
  await page
    .getByRole("textbox", { name: "复核人（必填）" })
    .fill("前端测试员");
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "确认批准", exact: true }).click();
  await expect(page.getByText(/测试：暂时无法提交/)).toBeVisible();
  await page.getByRole("button", { name: "确认批准", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "已批准的答复" }),
  ).toBeVisible();
  expect(requests).toHaveLength(2);
  expect(requests[0].request_id).toBe(requests[1].request_id);
  await expect(page.getByText(/自填身份，未经认证/)).toBeVisible();
});
test("live backend: read documents, create a rules draft and reject only this test run", async ({
  page,
}) => {
  test.skip(
    process.env.E2E_LIVE_API !== "1",
    "Opt in to create an explicitly labeled QA run in the local database.",
  );
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(page.getByText("数据库已连接")).toBeVisible();
  await page.screenshot({
    path: "test-results/live-workspace.png",
    fullPage: true,
  });
  await page
    .getByRole("textbox", { name: "描述现场问题" })
    .fill("【前端联调测试】AOI短路报点增加，应该如何复核？");
  const createdResponse = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/v1/agent/runs") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "生成规则草稿" }).click();
  const response = await createdResponse;
  const run = await response.json();
  expect(response.status()).toBe(202);
  expect(response.request().postDataJSON().use_model).toBe(false);
  expect(run.evidence.length).toBeGreaterThan(0);
  expect(run.draft.generation_mode).toBe("deterministic_fallback");
  await expect(page.getByText("规则草稿 · 非模型生成")).toBeVisible();
  await page.getByRole("button", { name: "拒绝", exact: true }).click();
  await page
    .getByRole("textbox", { name: "复核人（必填）" })
    .fill("frontend-qa");
  await page
    .getByRole("textbox", { name: "复核备注" })
    .fill("仅验证前端联调，不作为生产处置依据。");
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "确认拒绝", exact: true }).click();
  await expect(page.getByText("已拒绝 · frontend-qa")).toBeVisible();
  await page.reload();
  await expect(page.getByText("已拒绝 · frontend-qa")).toBeVisible();
  console.log("LIVE_QA_RUN_ID=" + run.run_id);
  expect(errors).toEqual([]);
});

test("finishing a request preserves navigation and keeps the new run discoverable", async ({ page }) => {
  await mockBase(page);
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/v1/agent/runs", async route => {
    await gate;
    await route.fulfill({ status: 202, json: pending });
  });
  await page.goto("/");
  await expect(page.getByText("数据库已连接")).toBeVisible();
  await page.getByRole("button", { name: "桥连 / 短路" }).click();
  const requested = page.waitForRequest(r => r.url().endsWith("/api/v1/agent/runs"));
  await page.getByRole("button", { name: "生成规则草稿" }).click();
  await requested;
  await page.getByRole("link", { name: "知识库", exact: true }).click();
  const response = page.waitForResponse(r => r.url().endsWith("/api/v1/agent/runs"));
  release();
  await response;
  await expect(page.getByRole("heading", { name: "知识库", exact: true })).toBeVisible();
  await expect(page).toHaveURL(/#\/knowledge$/);
  await page.getByRole("link", { name: "质量分析", exact: true }).click();
  await expect(page.getByRole("link", { name: /测试 AOI 复核/ })).toBeVisible();
});
