import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";
import { Minimatch as esmMinimatch } from "../vendor/minimatch-compat/index.mjs";

const require = createRequire(import.meta.url);
const legacy = require("../vendor/minimatch-compat/index.cjs");
const modern = createRequire(new URL("../vendor/minimatch-compat/package.json", import.meta.url))("minimatch-modern");

test("legacy lint consumers receive the patched upstream callable and helpers", () => {
  assert.equal(typeof legacy, "function");
  assert.equal(legacy, modern.minimatch);
  assert.equal(legacy.Minimatch, modern.Minimatch);
  assert.equal(new esmMinimatch("**/*.tsx").match("src/page.tsx"), true);
  assert.equal(legacy.makeRe, modern.makeRe);
  assert.equal(legacy("src/app/page.tsx", "**/*.tsx"), true);
  assert.equal(legacy("src/app/page.tsx", "**/*.js"), false);
  assert.equal(legacy(".hidden.ts", "*.ts"), false);
  assert.equal(legacy(".hidden.ts", "*.ts", { dot: true }), true);
  assert.equal(legacy("SRC/PAGE.TSX", "**/*.tsx", { nocase: true }), true);
  assert.equal(legacy("src/app/page.tsx", "page.tsx", { matchBase: true }), true);
  assert.equal(new legacy.Minimatch("**/*.tsx").match("src/page.tsx"), true);
  assert.deepEqual(legacy.match(["a.ts", "b.js"], "*.ts"), ["a.ts"]);
  assert.equal(legacy.defaults({ dot: true })(".hidden.ts", "*.ts"), true);
});

test("the upstream pattern expansion bound is enforced through the legacy API", () => {
  assert.deepEqual(legacy.braceExpand("{a,b}{c,d}", { braceExpandMax: 2 }), ["ac", "ad"]);
  assert.equal(new legacy.Minimatch("{a,b,c,d}", { braceExpandMax: 2 }).set.length, 2);
});

test("installed lint packages resolve the callable compatibility API", () => {
  for (const name of ["eslint", "eslint-plugin-import", "eslint-plugin-jsx-a11y", "eslint-plugin-react"]) {
    const fromConsumer = createRequire(require.resolve(`${name}/package.json`));
    assert.equal(fromConsumer("minimatch"), legacy, name);
  }
});
