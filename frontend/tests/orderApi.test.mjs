import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { api } from "../src/api/client.ts";

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

test("all order API payloads send expected_order_revision", async () => {
  const requests = [];
  globalThis.fetch = async (path, init) => {
    requests.push({ path, body: JSON.parse(init?.body ?? "{}") });
    return new Response(JSON.stringify({ status: "ok", order_revision: "order-v1-next" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };

  await api.reorderToday("USER-1", ["TASK-2", "TASK-1"], "order-v1-today");
  await api.moveWbsTask("TASK-1", "USER-1", 1, "order-v1-move");
  await api.reorderWbs("USER-1", ["TASK-2", "TASK-1"], "order-v1-wbs");
  await api.reorderWbsSiblings("USER-1", null, ["TASK-2", "TASK-1"], "order-v1-siblings");

  assert.deepEqual(
    requests.map(({ body }) => body.expected_order_revision),
    ["order-v1-today", "order-v1-move", "order-v1-wbs", "order-v1-siblings"]
  );
  assert.deepEqual(
    requests.map(({ path }) => path),
    [
      "/api/tasks/today/reorder",
      "/api/tasks/TASK-1/wbs/move",
      "/api/tasks/wbs/reorder",
      "/api/tasks/wbs/reorder-siblings",
    ]
  );
});
