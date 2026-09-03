import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, errorDetail } from "./api";
import { readRecentIds, rememberRun, validateUpload } from "./lib";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
describe("upload boundaries", () => {
  it.each(["file.exe", "report.pdf.exe", "note.markdown"])(
    "rejects unsupported %s",
    (name) => expect(validateUpload({ name, size: 100 })).toBeTruthy(),
  );
  it("rejects empty and oversized documents, permits the exact limit", () => {
    expect(validateUpload({ name: "a.txt", size: 0 })).toBeTruthy();
    expect(validateUpload({ name: "a.txt", size: 20971521 })).toBeTruthy();
    expect(validateUpload({ name: "报告.DOCX", size: 20971520 })).toBeNull();
  });
});
it("records only valid IDs, deduplicates and tolerates restricted storage", () => {
  const id = "00000000-0000-4000-8000-000000000001";
  let value = JSON.stringify(["invalid", id, id]);
  vi.stubGlobal("localStorage", {
    getItem: () => value,
    setItem: (_k: string, v: string) => {
      value = v;
    },
  });
  expect(readRecentIds()).toEqual([id]);
  rememberRun(id);
  expect(JSON.parse(value)).toEqual([id]);
  vi.stubGlobal("localStorage", {
    getItem: () => {
      throw Error("denied");
    },
    setItem: () => {
      throw Error("denied");
    },
  });
  expect(readRecentIds()).toEqual([]);
  expect(() => rememberRun(id)).not.toThrow();
});
it("sends explicit offline mode and never a credential on generation requests", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue(
      new Response(JSON.stringify({ run_id: "example" }), { status: 202 }),
    );
  vi.stubGlobal("fetch", fetcher);
  await api.createRun("AOI", "keyword", 3, false);
  const [, init] = fetcher.mock.calls[0];
  expect(JSON.parse(init.body)).toEqual({
    question: "AOI",
    search_mode: "keyword",
    top_k: 3,
    use_model: false,
  });
  expect(init.headers.Authorization).toBeUndefined();
});
it("retains approval idempotency ID and sends bearer only for approval", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue(new Response("{}", { status: 200 }));
  vi.stubGlobal("fetch", fetcher);
  const payload = {
    approved: false,
    actor_id: "tester",
    comment: "test",
    request_id: "stable-id",
  };
  await api.approve("run-id", payload, "test-token");
  expect(fetcher.mock.calls[0][1].headers.Authorization).toBe(
    "Bearer test-token",
  );
  expect(JSON.parse(fetcher.mock.calls[0][1].body).request_id).toBe(
    "stable-id",
  );
});
it("exposes a failed run ID without showing an HTML upstream body", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        new Response("<html>private upstream</html>", {
          status: 502,
          headers: { "X-Agent-Run-Id": "failed-run" },
        }),
      ),
  );
  await expect(api.createRun("AOI", "keyword", 3, false)).rejects.toMatchObject(
    { status: 502, runId: "failed-run" },
  );
});
it("formats validation errors and separates identity from authorization failures", () => {
  expect(errorDetail({ detail: [{ msg: "输入不能为空" }] }, 422)).toBe(
    "输入不能为空",
  );
  expect(errorDetail(null, 401)).toContain("身份验证失败");
  expect(errorDetail(null, 403)).toContain("没有执行此操作的权限");
  expect(errorDetail(null, 409)).toContain("状态已发生变化");
  expect(new ApiError("x").status).toBe(0);
});
