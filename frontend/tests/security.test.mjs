import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import test from "node:test";
import ts from "typescript";

const sourceRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../src");

function browserHarness({ allowNetwork = false, moduleMocks = {}, globals = {} } = {}) {
  const makeStorage = () => {
    const data = new Map();
    return {
      getItem: (key) => data.get(key) ?? null,
      setItem: (key, value) => data.set(key, value),
      removeItem: (key) => data.delete(key),
    };
  };
  const cookieWrites = [];
  const requests = [];
  const window = {
    location: new URL("https://www.example.com/request-a-quote?token=private-query&utm_source=trade-show&utm_campaign=autumn#private-fragment"),
    localStorage: makeStorage(),
    sessionStorage: makeStorage(),
    dispatchEvent: () => true,
  };
  const document = {
    referrer: "https://partner.example/private/customer?email=private@example.com#secret",
    get cookie() { return "_ga=example; __Secure-_ga_TEST=example; __Host-_ga_TEST=example; sessionid=essential"; },
    set cookie(value) { cookieWrites.push(value); },
  };
  const context = vm.createContext({
    window, document, URL, URLSearchParams, FormData, Event, crypto,
    get dataLayer() { return window.dataLayer; },
    AbortController, DOMException, setTimeout, clearTimeout,
    process: { env: { NODE_ENV: "test" } },
    fetch: (input, init) => {
      if (!allowNetwork) throw new Error("Unexpected network request in security regression test");
      requests.push({ input, init });
      return Promise.resolve({ ok: true, status: 200 });
    },
    ...globals,
  });
  const modules = new Map();
  function load(relativePath) {
    const filename = resolve(sourceRoot, relativePath);
    if (modules.has(filename)) return modules.get(filename).exports;
    const sourceModule = { exports: {} };
    modules.set(filename, sourceModule);
    const source = readFileSync(filename, "utf8");
    const compiled = ts.transpileModule(source, {
      fileName: filename,
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
    }).outputText;
    const requireSource = (specifier) => {
      if (Object.hasOwn(moduleMocks, specifier)) return moduleMocks[specifier];
      assert.ok(specifier.startsWith("@/"), `Unexpected dependency: ${specifier}`);
      return load(`${specifier.slice(2)}.ts`);
    };
    const evaluate = vm.runInContext(`(function(require, module, exports) {\n${compiled}\n})`, context, { filename });
    evaluate(requireSource, sourceModule, sourceModule.exports);
    return sourceModule.exports;
  }
  return { load, window, cookieWrites, requests, runScript: (source) => vm.runInContext(source, context) };
}

test("CMS email values cannot inject mail recipients or message headers", () => {
  const { load } = browserHarness();
  const { safeMailto } = load("lib/site.ts");
  assert.equal(safeMailto("sales@example.com?subject=Injected&body=Injected"), "");
  assert.equal(safeMailto("sales@example.com?bcc=other%40example.com"), "");
  assert.equal(safeMailto("sales@example.com?bcc=other@example.com"), "");
  assert.equal(safeMailto("sales@example.com%0d%0aBcc:other@example.com"), "");
  assert.equal(safeMailto("sales@example.com\r\nBcc:other@example.com"), "");
  assert.equal(safeMailto("sales@example.com,other@example.com"), "");
  assert.equal(safeMailto("sales+oem@EXAMPLE.COM"), "mailto:sales%2Boem@example.com");
  // Reserved punctuation is legal in the local part, but must remain address
  // data after one URI decoding step instead of becoming a mailto query.
  assert.equal(safeMailto("sales?bcc=other&subject=quote@example.com"), "mailto:sales%3Fbcc%3Dother%26subject%3Dquote@example.com");
  assert.equal(safeMailto("sales%0d%0a@example.com"), "mailto:sales%250d%250a@example.com");
});

test("document URL validation rejects executable schemes and URL normalization tricks", () => {
  const { safeHref, serializeJsonLd } = browserHarness().load("lib/structured-data.ts");
  for (const unsafe of ["javascript:alert(1)", "data:text/html,<script>alert(1)</script>", "//other.example/file", "/\\other.example/file", "/\n/other.example/file", "https://sales:password@other.example/file"]) {
    assert.equal(safeHref(unsafe), "", unsafe);
  }
  assert.equal(safeHref("/api/documents/2/download/?preview=1"), "/api/documents/2/download/?preview=1");
  assert.equal(safeHref("https://files.example/drawing.pdf"), "https://files.example/drawing.pdf");
  const json = serializeJsonLd({ text: "</script><script>alert(1)</script>" });
  assert.ok(!json.includes("<"));
  assert.equal(JSON.parse(json).text, "</script><script>alert(1)</script>");
});

test("enquiry attribution strips arbitrary queries/fragments and preserves explicit campaign fields", () => {
  const { load, window } = browserHarness();
  const { addAttributionToForm, captureAttribution } = load("lib/attribution.ts");
  const form = new FormData();
  addAttributionToForm(form);
  assert.equal(form.get("source_url"), "https://www.example.com/request-a-quote");
  assert.equal(form.get("first_landing_page"), "/request-a-quote");
  assert.equal(form.get("last_landing_page"), "/request-a-quote");
  assert.equal(form.get("referrer_url"), "https://partner.example");
  assert.equal(form.get("utm_source"), "trade-show");
  assert.equal(form.get("utm_campaign"), "autumn");
  assert.ok(!JSON.stringify([...form.entries()]).includes("private"));
  const sessionId = form.get("session_id");
  assert.ok(sessionId);

  // Existing sessions created by older releases are sanitized on read too.
  window.sessionStorage.setItem("axleforge-attribution", JSON.stringify({
    session_id: sessionId,
    first_landing_page: "/products?q=private-query#private-fragment",
    referrer_url: "https://partner.example/customer?token=private-referrer",
    utm_source: "trade-show",
  }));
  window.location = new URL("https://www.example.com/products/trailer-axles?q=3500&capacity_min=2000#dimensions");
  const attribution = captureAttribution();
  assert.equal(attribution.first_landing_page, "/products");
  assert.equal(attribution.last_landing_page, "/products/trailer-axles");
  assert.equal(attribution.referrer_url, "https://partner.example");
  assert.equal(attribution.utm_source, "trade-show");
  assert.equal(attribution.session_id, sessionId);
  assert.ok(!window.sessionStorage.getItem("axleforge-attribution").includes("private"));
  // Privacy filtering must never mutate the search/filter URL used by the UI.
  assert.equal(window.location.search, "?q=3500&capacity_min=2000");
});

test("consent updates use the Google command format and clear attribution on withdrawal", () => {
  const { load, window } = browserHarness();
  const { setConsent, updateGoogleConsent } = load("lib/consent.ts");
  window.sessionStorage.setItem("axleforge-attribution", "old-session");
  updateGoogleConsent(true);
  let message = window.dataLayer.at(-1);
  assert.equal(Object.prototype.toString.call(message), "[object Arguments]");
  assert.equal(message[0], "consent");
  assert.equal(message[1], "update");
  assert.equal(message[2].analytics_storage, "granted");
  assert.equal(message[2].ad_storage, "denied");
  assert.equal(window.sessionStorage.getItem("axleforge-attribution"), "old-session");

  setConsent("essential");
  message = window.dataLayer.at(-1);
  assert.equal(message[2].analytics_storage, "denied");
  assert.equal(window.localStorage.getItem("axleforge-cookie-consent"), "essential");
  assert.equal(window.sessionStorage.getItem("axleforge-attribution"), null);
});

test("API fetches do not leak page queries through a same-origin Referer fallback", async () => {
  const { load, requests } = browserHarness({ allowNetwork: true });
  const { fetchWithTimeout } = load("lib/fetch-with-timeout.ts");
  await fetchWithTimeout("/api/contact/", { method: "POST", body: new FormData() });
  assert.equal(requests.length, 1);
  assert.equal(requests[0].init.referrerPolicy, "strict-origin");
  assert.equal(requests[0].init.method, "POST");
});

test("optional first-party measurement remains disabled without consent", async () => {
  const { load, window, requests } = browserHarness({ allowNetwork: true });
  const { recordPageView } = load("lib/attribution.ts");
  recordPageView("Product catalogue");
  assert.equal(requests.length, 0);
  window.localStorage.setItem("axleforge-cookie-consent", "accepted");
  recordPageView("Product catalogue");
  // Wait for the visit-session and page-view promise chain to settle.
  await new Promise((done) => setImmediate(done));
  assert.equal(requests.length, 2);
  for (const request of requests) {
    assert.equal(request.init.referrerPolicy, "strict-origin");
    assert.ok(!request.init.body.includes("private"));
  }
});

test("withdrawing consent during session creation prevents the follow-up page view", async () => {
  let finishSession;
  const requests = [];
  const { load, window } = browserHarness({
    moduleMocks: {
      "@/lib/fetch-with-timeout": {
        fetchWithTimeout: (input, init) => {
          requests.push({ input, init });
          return new Promise((resolve) => { finishSession = resolve; });
        },
      },
    },
  });
  const { recordPageView } = load("lib/attribution.ts");
  window.localStorage.setItem("axleforge-cookie-consent", "accepted");
  recordPageView("Product catalogue");
  assert.equal(requests.length, 1);
  load("lib/consent.ts").setConsent("essential");
  finishSession({ ok: true, status: 200 });
  await new Promise((done) => setImmediate(done));
  assert.equal(requests.length, 1, "A withdrawn consent must stop the second analytics request");
  assert.equal(window.sessionStorage.getItem("axleforge-attribution"), null);
});

test("site search events measure usage without persisting visitors' free-text queries", () => {
  const { load, window, requests } = browserHarness({
    allowNetwork: true,
    moduleMocks: {
      react: {
        useEffect: (effect) => effect(),
        useRef: (value) => ({ current: value }),
      },
    },
  });
  const { SearchEventTracker } = load("components/SearchEventTracker.tsx");
  window.localStorage.setItem("axleforge-cookie-consent", "accepted");
  const query = "private@example.com token=private-search-secret";
  SearchEventTracker({ query, resultCount: 0 });
  assert.equal(requests.length, 1);
  const event = JSON.parse(requests[0].init.body);
  assert.equal(event.event_name, "search_product");
  assert.equal(event.metadata.query_length, query.length);
  assert.equal(event.metadata.results, 0);
  assert.equal(event.metadata.placement, "site_search");
  assert.equal(Object.hasOwn(event.metadata, "query"), false);
  assert.ok(!requests[0].init.body.includes("private"));
});

test("the rendered built-in GA config overrides full page and referral URLs with sanitized values", () => {
  const ScriptType = "next-script";
  const jsx = (type, props) => ({ type, props });
  const { load, window, runScript } = browserHarness({
    moduleMocks: {
      "react/jsx-runtime": { jsx, jsxs: jsx, Fragment: "fragment" },
      react: {
        useEffect: () => {},
        useRef: (value) => ({ current: value }),
        useSyncExternalStore: (_subscribe, getSnapshot) => getSnapshot(),
      },
      "next/script": { default: ScriptType },
      "@/components/SiteConfigProvider": { useSiteConfig: () => ({ googleAnalyticsId: "G-TEST123", googleTagManagerId: "" }) },
    },
  });
  const { ConsentAnalytics } = load("components/ConsentAnalytics.tsx");
  assert.equal(ConsentAnalytics(), null);
  window.localStorage.setItem("axleforge-cookie-consent", "accepted");
  const scripts = [];
  function collectScripts(node) {
    if (Array.isArray(node)) return node.forEach(collectScripts);
    if (!node || typeof node !== "object") return;
    if (node.type === ScriptType) scripts.push(node.props);
    collectScripts(node.props?.children);
  }
  collectScripts(ConsentAnalytics());
  const inlineConfig = scripts.find((script) => script.id === "ga-consent-config");
  assert.ok(inlineConfig, "The consented page must render its GA initialization script");
  runScript(inlineConfig.children);
  const configCommand = window.dataLayer.find((message) => message[0] === "config");
  assert.ok(configCommand, "The actual rendered script must queue a GA config command");
  assert.equal(configCommand[1], "G-TEST123");
  assert.equal(configCommand[2].page_location, "https://www.example.com/request-a-quote");
  assert.equal(configCommand[2].page_referrer, "https://partner.example");
  assert.ok(!JSON.stringify(configCommand[2]).includes("private"));
  assert.ok(!JSON.stringify(configCommand[2]).includes("#"));
  assert.equal(window.location.search, "?token=private-query&utm_source=trade-show&utm_campaign=autumn");
});

test("analytics cookie withdrawal expires parent-domain and prefixed cookies", () => {
  const { load, cookieWrites } = browserHarness();
  load("lib/consent.ts").clearAnalyticsCookies();
  assert.ok(cookieWrites.some((value) => value.startsWith("_ga=;") && value.includes("Domain=example.com")));
  assert.ok(cookieWrites.some((value) => value.startsWith("__Secure-_ga_TEST=;") && value.includes("Domain=example.com") && value.includes("; Secure")));
  assert.ok(cookieWrites.some((value) => value.startsWith("__Host-_ga_TEST=;") && value.includes("; Secure")));
  assert.ok(cookieWrites.every((value) => !value.startsWith("__Host-") || !value.includes("Domain=")));
  assert.ok(cookieWrites.every((value) => !value.startsWith("sessionid=")));
});

function rfqDraftHarness() {
  class Input {
    constructor(name, value = "", type = "text", checked = false) {
      Object.assign(this, { name, value, type, checked });
    }
  }
  class Select {
    constructor(name, value = "") { Object.assign(this, { name, value }); }
  }
  class Textarea {
    constructor(name, value = "") { Object.assign(this, { name, value }); }
  }
  const effects = [];
  const refs = [];
  const jsx = (type, props) => ({ type, props });
  const harness = browserHarness({
    globals: { HTMLInputElement: Input, HTMLSelectElement: Select, HTMLTextAreaElement: Textarea },
    moduleMocks: {
      "react/jsx-runtime": { jsx, jsxs: jsx, Fragment: "fragment" },
      react: {
        useEffect: (effect) => { effects.push(effect); },
        useRef: (value) => { const ref = { current: value }; refs.push(ref); return ref; },
        useState: (value) => [value, () => {}],
      },
      "next/link": { default: "next-link" },
      "next/navigation": { useSearchParams: () => new URLSearchParams() },
      "@/lib/data": { apiBase: "/api" },
      "@/lib/rfq": { useRfqItems: () => [], clearRfq: () => {} },
      "@/lib/events": { trackEvent: () => {} },
      "@/lib/attribution": { addAttributionToForm: () => {} },
      "@/components/TurnstileWidget": { TurnstileWidget: "turnstile-widget", turnstileEnabled: true },
      "@/lib/form-errors": { apiErrorMessage: () => "Unexpected form submission" },
      "@/components/AttachmentInput": { AttachmentInput: "attachment-input" },
      "@/lib/inquiry-attachments": { validateInquiryAttachments: () => "" },
    },
  });
  const controls = [
    new Input("name", "Test buyer"),
    new Input("email", "buyer@example.com", "email"),
    new Input("privacy_consent", "true", "checkbox", true),
    new Input("specifications.capacity", "3500", "number"),
    new Select("specifications.capacity_unit", "lb"),
    new Textarea("message", "Please quote the axle configuration."),
    new Input("turnstile_token", "fresh-turnstile-token", "hidden"),
    new Input("cf-turnstile-response", "fresh-cloudflare-response", "hidden"),
    new Input("attachments", "C:\\fakepath\\private-drawing.pdf", "file"),
    new Input("website", "honeypot-value"),
  ];
  controls.namedItem = (name) => controls.find((control) => control.name === name);
  const form = { elements: controls, querySelectorAll: () => [] };
  const { RFQForm } = harness.load("components/RFQForm.tsx");
  const rendered = RFQForm();
  assert.equal(rendered.type, "form");
  refs[0].current = form;
  return { ...harness, controls, rendered, restoreDraft: () => effects.forEach((effect) => effect()) };
}

test("the actual RFQ change handler saves contact/specification drafts without security tokens or files", () => {
  const { window, requests, rendered, restoreDraft } = rfqDraftHarness();
  restoreDraft();
  rendered.props.onChange();
  const raw = window.sessionStorage.getItem("axleforge-rfq-draft");
  const draft = JSON.parse(raw);
  assert.equal(draft.name, "Test buyer");
  assert.equal(draft.email, "buyer@example.com");
  assert.equal(draft.privacy_consent, true);
  assert.equal(draft["specifications.capacity"], "3500");
  assert.equal(draft["specifications.capacity_unit"], "lb");
  assert.equal(draft.message, "Please quote the axle configuration.");
  for (const field of ["turnstile_token", "cf-turnstile-response", "attachments", "website"]) {
    assert.equal(Object.hasOwn(draft, field), false, `${field} must not be persisted`);
  }
  for (const sensitive of ["fresh-turnstile-token", "fresh-cloudflare-response", "private-drawing.pdf", "honeypot-value"]) {
    assert.ok(!raw.includes(sensitive));
  }
  assert.equal(requests.length, 0);
});

test("the actual RFQ mount sanitizes legacy drafts without restoring stale verification or file values", () => {
  const { window, controls, restoreDraft } = rfqDraftHarness();
  window.sessionStorage.setItem("axleforge-rfq-draft", JSON.stringify({
    name: "Restored buyer", email: "restored@example.com", privacy_consent: false,
    "specifications.capacity": "7000", "specifications.capacity_unit": "lb",
    message: "Please keep this legitimate unsent requirement.",
    turnstile_token: "legacy-spent-token", "cf-turnstile-response": "legacy-cloudflare-token",
    attachments: "legacy-confidential-drawing.pdf", website: "legacy-honeypot",
  }));
  restoreDraft();
  assert.equal(controls.namedItem("name").value, "Restored buyer");
  assert.equal(controls.namedItem("email").value, "restored@example.com");
  assert.equal(controls.namedItem("privacy_consent").checked, false);
  assert.equal(controls.namedItem("specifications.capacity").value, "7000");
  assert.equal(controls.namedItem("message").value, "Please keep this legitimate unsent requirement.");
  assert.equal(controls.namedItem("turnstile_token").value, "fresh-turnstile-token");
  assert.equal(controls.namedItem("cf-turnstile-response").value, "fresh-cloudflare-response");
  assert.equal(controls.namedItem("attachments").value, "C:\\fakepath\\private-drawing.pdf");
  assert.equal(controls.namedItem("website").value, "honeypot-value");
  const sanitized = JSON.parse(window.sessionStorage.getItem("axleforge-rfq-draft"));
  assert.equal(sanitized.name, "Restored buyer");
  for (const field of ["turnstile_token", "cf-turnstile-response", "attachments", "website"]) {
    assert.equal(Object.hasOwn(sanitized, field), false, `${field} must be removed from legacy storage`);
  }
});

test("submission retries retain their identifier across refreshed verification and source context", async () => {
  const { load, window } = browserHarness({ globals: { TextEncoder } });
  const { getSubmissionKey } = load("lib/submission.ts");
  const form = new FormData();
  form.set("email", "private-buyer@example.com");
  form.set("message", "Private technical requirement.");
  form.set("turnstile_token", "first-token");
  form.set("utm_source", "trade-show");
  const first = await getSubmissionKey("rfq", form);
  form.set("turnstile_token", "replacement-token");
  form.set("utm_source", "return-visit");
  assert.equal(await getSubmissionKey("rfq", form), first);
  assert.match(first, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  const stored = window.sessionStorage.getItem("axleforge-submission:rfq");
  assert.ok(!stored.includes("private-buyer"));
  assert.ok(!stored.includes("Private technical"));
  assert.ok(!stored.includes("token"));
});

test("edited or completed submissions receive a new identifier without clearing another pending request", async () => {
  const { load } = browserHarness({ globals: { TextEncoder } });
  const { getSubmissionKey, clearSubmissionKey } = load("lib/submission.ts");
  const form = new FormData();
  form.set("message", "First requirement");
  const first = await getSubmissionKey("contact", form);
  form.set("message", "Changed requirement");
  const second = await getSubmissionKey("contact", form);
  assert.notEqual(second, first);
  clearSubmissionKey("contact", first);
  assert.equal(await getSubmissionKey("contact", form), second);
  assert.notEqual(await getSubmissionKey("rfq", form), second);
  clearSubmissionKey("contact", second);
  assert.notEqual(await getSubmissionKey("contact", form), second);
});

test("attachment identity uses content bytes and tolerates reselecting the same file", async () => {
  const { load } = browserHarness({ globals: { TextEncoder } });
  const { getSubmissionKey } = load("lib/submission.ts");
  const form = new FormData();
  form.set("attachments", new Blob(["first bytes"], { type: "text/plain" }), "drawing.txt");
  const first = await getSubmissionKey("rfq", form);
  form.set("attachments", new Blob(["first bytes"], { type: "text/plain" }), "drawing.txt");
  assert.equal(await getSubmissionKey("rfq", form), first);
  form.set("attachments", new Blob(["other bytes"], { type: "text/plain" }), "drawing.txt");
  assert.notEqual(await getSubmissionKey("rfq", form), first);
});

test("corrupt submission storage is replaced and parallel preparations share one identifier", async () => {
  const { load, window } = browserHarness({ globals: { TextEncoder } });
  const { getSubmissionKey } = load("lib/submission.ts");
  const form = new FormData();
  form.set("message", "One requirement");
  window.sessionStorage.setItem("axleforge-submission:rfq", "not-json");
  const identifiers = await Promise.all([getSubmissionKey("rfq", form), getSubmissionKey("rfq", form)]);
  assert.equal(identifiers[0], identifiers[1]);
});

function submissionFormHarness(kind, respond) {
  const fields = new Map([["name", "Test buyer"], ["email", "buyer@example.com"], ["message", "Please quote this requirement."], ["privacy_consent", "true"], ["turnstile_token", "first-challenge"]]);
  const requests = [];
  const refs = [];
  let resets = 0;
  let cartClears = 0;
  const form = { elements: { namedItem: () => null }, reset: () => { resets++; } };
  class FormDataFromForm extends FormData {
    constructor(element) {
      super();
      if (element === form) for (const [name, value] of fields) this.set(name, value);
    }
  }
  const jsx = (type, props) => ({ type, props });
  const harness = browserHarness({
    globals: {
      TextEncoder,
      FormData: FormDataFromForm,
      HTMLInputElement: class Input {},
      fetch: async (input, init) => {
        requests.push({ input, init });
        return respond(requests.length);
      },
    },
    moduleMocks: {
      "react/jsx-runtime": { jsx, jsxs: jsx, Fragment: "fragment" },
      react: { useEffect: () => {}, useRef: (value) => { const ref = { current: value }; refs.push(ref); return ref; }, useState: (value) => [value, () => {}] },
      "next/link": { default: "next-link" },
      "next/navigation": { useSearchParams: () => new URLSearchParams() },
      "@/lib/data": { apiBase: "/api" },
      "@/lib/rfq": { useRfqItems: () => [], clearRfq: () => { cartClears++; } },
      "@/lib/events": { trackEvent: () => {} },
      "@/lib/attribution": { addAttributionToForm: () => {} },
      "@/components/TurnstileWidget": { TurnstileWidget: "turnstile-widget", turnstileEnabled: true },
      "@/lib/form-errors": { apiErrorMessage: () => "Submission failed" },
      "@/components/AttachmentInput": { AttachmentInput: "attachment-input" },
      "@/lib/inquiry-attachments": { validateInquiryAttachments: () => "" },
      "@/components/SiteConfigProvider": { useSiteConfig: () => ({ whatsappUrl: "" }) },
    },
  });
  const name = { rfq: "RFQForm", contact: "ContactForm", product: "ProductInquiryForm" }[kind];
  const Component = harness.load(`components/${name}.tsx`)[name];
  const rendered = kind === "product" ? Component({ product: { id: 9, title: "Test axle", model: "TEST-AXLE", rated_capacity: 3500, capacity_unit: "lb" } }) : Component();
  if (kind === "rfq") refs[0].current = form;
  assert.equal(rendered.type, "form");
  return { ...harness, fields, requests, submit: () => rendered.props.onSubmit({ preventDefault() {}, currentTarget: form }), get resets() { return resets; }, get cartClears() { return cartClears; } };
}

for (const kind of ["rfq", "contact", "product"]) {
  test(`the actual ${kind} form reuses its request identifier after a lost response`, async () => {
    const harness = submissionFormHarness(kind, (attempt) => {
      if (attempt === 1) throw new Error("The confirmation response was lost.");
      return { ok: true, status: 201, json: async () => ({ reference: "TEST-REFERENCE" }) };
    });
    await harness.submit();
    assert.equal(harness.resets, 0);
    assert.equal(harness.cartClears, 0);
    harness.fields.set("turnstile_token", "fresh-challenge");
    await harness.submit();
    assert.equal(harness.requests.length, 2);
    assert.equal(harness.requests[0].init.headers["Idempotency-Key"], harness.requests[1].init.headers["Idempotency-Key"]);
    assert.equal(harness.resets, 1);
    assert.equal(harness.cartClears, kind === "rfq" ? 1 : 0);
    const scope = kind === "product" ? "product-9" : kind;
    assert.equal(harness.window.sessionStorage.getItem(`axleforge-submission:${scope}`), null);
  });
}

test("a successful HTTP response without a reference preserves the RFQ and its retry identifier", async () => {
  const harness = submissionFormHarness("rfq", (attempt) => ({ ok: true, status: 201, json: async () => attempt === 1 ? {} : { reference: "TEST-REFERENCE" } }));
  await harness.submit();
  assert.equal(harness.resets, 0);
  assert.equal(harness.cartClears, 0);
  await harness.submit();
  assert.equal(harness.requests[0].init.headers["Idempotency-Key"], harness.requests[1].init.headers["Idempotency-Key"]);
  assert.equal(harness.cartClears, 1);
});

test("overlapping submit events send only one in-flight request", async () => {
  const harness = submissionFormHarness("contact", () => ({ ok: true, status: 201, json: async () => ({ reference: "TEST-REFERENCE" }) }));
  await Promise.all([harness.submit(), harness.submit()]);
  assert.equal(harness.requests.length, 1);
  assert.equal(harness.resets, 1);
});
