const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");
const { renderToStaticMarkup } = require("react-dom/server");
const messages = require("../src/locales/zh-Hant.json");

function load(sourcePath, globals = {}) {
  const source = fs.readFileSync(path.join(__dirname, "..", "src", sourcePath), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, require, URLSearchParams, window: {}, ...globals });
  return exports;
}

function translate(source, ...values) {
  return (messages[source] ?? source).replace(/\{(\d+)\}/g, (_, index) => values[Number(index)] ?? "");
}

test("summary failures show the specific translated reason and safe location without retrying", async () => {
  for (const code of ["invalid_report_component_data", "invalid_report_sources", "unsupported_report_claim", "invalid_report_evidence", "invalid_report_json", "invalid_report_structure"]) {
    let calls = 0;
    const api = load("system/backendApi.ts", { fetch: async () => {
      calls++;
      return { ok: false, status: 502, json: async () => ({ detail: {
        code, location: "parts.0.sections.1.blocks.2.data", message: "private model response",
      } }) };
    } });
    await assert.rejects(api.generateMeetingReport("m"), error => {
      assert.equal(error.code, code);
      assert.equal(error.needsSettings, false);
      const message = api.summaryFailureMessage(error, translate);
      assert.match(message, /模型/);
      assert.match(message, /錯誤位置：parts\.0\.sections\.1\.blocks\.2\.data/);
      assert.doesNotMatch(message, /private|模型設定/);
      return true;
    });
    assert.equal(calls, 1);
  }
});

test("model configuration and connection failures retain the settings action", async () => {
  for (const code of ["model_not_configured", "model_authentication", "model_connection", "model_timeout", "provider_error"]) {
    const api = load("system/backendApi.ts");
    assert.equal(new api.SummaryGenerationError(code).needsSettings, true);
  }
  const missing = load("system/backendApi.ts", { fetch: async () => ({ ok: false, status: 503 }) });
  await assert.rejects(missing.generateMeetingReport("m"), error => error.code === "model_not_configured");
  const offline = load("system/backendApi.ts", { fetch: async () => { throw new Error("private network error"); } });
  await assert.rejects(offline.generateMeetingReport("m"), error => error.code === "model_connection" && !error.message.includes("private"));
});

test("unknown failure codes and unsafe locations never display arbitrary response content", () => {
  const api = load("system/backendApi.ts");
  for (const code of ["unknown", "constructor", "__proto__"]) {
    const error = new api.SummaryGenerationError(code, "private transcript <script>");
    assert.equal(error.location, null);
    assert.equal(error.needsSettings, false);
    assert.match(error.message, /Retry/);
  }
});

test("manual and background errors use the same retry notice and show settings only when relevant", () => {
  const api = load("system/backendApi.ts");
  const { SummaryFailureNotice } = load("components/SummaryFailureNotice.tsx", { require: name => {
    if (name === "../i18n") return { t: translate };
    if (name === "../system/backendApi") return api;
    return require(name);
  } });
  let retries = 0;
  const props = { retry: () => { retries++; }, settings: () => assert.fail("not invoked during render") };
  const element = SummaryFailureNotice({ ...props, error: new api.SummaryGenerationError("invalid_report_component_data", "parts.0.sections.0.blocks.1.data") });
  const html = renderToStaticMarkup(element);
  assert.match(html, /role="alert"/);
  assert.match(html, /重試/);
  assert.match(html, /錯誤位置/);
  assert.doesNotMatch(html, /開啟設定/);
  assert.equal(retries, 0);
  element.props.children.find(child => child?.type === "button").props.onClick();
  assert.equal(retries, 1);
  const configured = renderToStaticMarkup(SummaryFailureNotice({ ...props, disabled: true,
    error: new api.SummaryGenerationError("model_authentication") }));
  assert.match(configured, /開啟設定/);
  assert.match(configured, /disabled=""/);
  assert.equal(retries, 1);
});

test("metric repair warning has a Traditional Chinese translation", () => {
  const warning = "Some metric cards were displayed as ordinary text because they contain no numeric values. Text and sources were preserved.";
  assert.match(translate(warning), /原文字與來源引用均已保留/);
});

test("automatic summaries stop after failure and only an explicit retry starts a new request", async () => {
  // A small deterministic hook host lets the real hook run its state/effects
  // against controlled timers, without a browser or a paid model request.
  const slots = [];
  let index = 0;
  let effects = [];
  const same = (a, b) => a?.length === b.length && a.every((value, i) => Object.is(value, b[i]));
  const hooks = {
    useState(initial) {
      const slot = index++;
      if (!slots[slot]) slots[slot] = { value: initial, set: value => {
        slots[slot].value = typeof value === "function" ? value(slots[slot].value) : value;
      } };
      return [slots[slot].value, slots[slot].set];
    },
    useRef(initial) {
      const slot = index++;
      return slots[slot] ??= { current: initial };
    },
    useMemo(factory, deps) {
      const slot = index++;
      if (!slots[slot] || !same(slots[slot].deps, deps)) slots[slot] = { deps, value: factory() };
      return slots[slot].value;
    },
    useCallback(callback, deps) { return hooks.useMemo(() => callback, deps); },
    useEffect(effect, deps) {
      const slot = index++;
      if (!slots[slot] || !same(slots[slot].deps, deps)) {
        slots[slot]?.cleanup?.();
        slots[slot] = { deps };
        effects.push(() => { slots[slot].cleanup = effect(); });
      }
    },
  };
  const timers = new Map();
  let timerId = 0;
  let calls = 0;
  const api = load("system/backendApi.ts", { fetch: async () => {
    calls++;
    return calls === 1
      ? { ok: false, status: 502, json: async () => ({ detail: { code: "invalid_report_component_data" } }) }
      : { ok: true, json: async () => ({ meeting_id: "m", created_at: "2026-11-12T08:00:00Z", document: { generation_method: "llm" } }) };
  } });
  const { useSummaryGeneration } = load("components/useSummaryGeneration.ts", {
    require: name => name === "react" ? hooks : api,
    window: {
      setInterval: callback => { timers.set(++timerId, callback); return timerId; },
      clearInterval: id => timers.delete(id),
    },
  });
  function render() {
    index = 0; effects = [];
    const result = useSummaryGeneration("m", true, [{ id: "s", revision: 1, text: "Cost overview." }], null);
    effects.forEach(effect => effect());
    return result;
  }
  let result = render();
  result.setAutomatic(true);
  result = render();
  assert.equal(timers.size, 1);
  [...timers.values()][0]();
  await new Promise(setImmediate);
  result = render();
  assert.equal(result.automatic, false);
  assert.equal(result.error.code, "invalid_report_component_data");
  assert.equal(result.errorMeetingId, "m");
  assert.equal(timers.size, 0);
  assert.equal(calls, 1);
  await result.generate("m");
  result = render();
  assert.equal(calls, 2);
  assert.equal(result.error, null);
  assert.equal(result.reports.m.document.generation_method, "llm");
  assert.equal(result.automatic, false);
  assert.equal(timers.size, 0);
});
