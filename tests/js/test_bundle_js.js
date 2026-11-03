/*
 * tests/js/test_bundle_js.js — bin/bundle-js, the map page's bundler (SNOW-1412).
 *
 * The bundle has to behave as the separate classic scripts did, so what is
 * pinned here is what concatenation could change:
 *
 *   - members are joined in list order, with the `\n;\n` separator;
 *   - bin/minify-js's `//# sourceMappingURL=` lines are stripped, since they
 *     would point at the wrong offsets inside the bundle;
 *   - a top-level `const` in one member is still a bare identifier in the
 *     next, as in the browser's shared global lexical scope;
 *   - a member that throws at the top level stops the members after it —
 *     the accepted trade-off, pinned so nobody is surprised by it;
 *   - a member opening with 'use strict', a duplicate, a missing file and an
 *     empty list are refused.
 *
 * Which members the page lists, and where the bundles sit, is pytest's:
 * tests/public/templatetags/test_js_bundles.py.
 */

import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

const require = createRequire(import.meta.url);
const { buildBundle, parseList, run, SEPARATOR } = require('../../bin/bundle-js');

let root;
let jsDir;
let listsDir;
let outDir;

/** Write `files` ({name: source}) into the fixture's js directory. */
function members(files) {
  for (const [name, source] of Object.entries(files)) {
    writeFileSync(path.join(jsDir, name), source);
  }
}

/** Run a built bundle as ONE classic script, the way the browser does. */
function execute(code) {
  const context = vm.createContext({ log: [] });
  try {
    vm.runInContext(code, context);
  } catch (err) {
    context.error = err.message;
  }
  return context;
}

beforeEach(() => {
  root = mkdtempSync(path.join(os.tmpdir(), 'bundle-js-'));
  jsDir = path.join(root, 'js');
  listsDir = path.join(jsDir, 'bundles');
  outDir = path.join(root, 'bundles');
  mkdirSync(listsDir, { recursive: true });
});

afterEach(() => {
  rmSync(root, { recursive: true, force: true });
});

describe('the list format', () => {
  it('reads filenames in order and skips notes and blank lines', () => {
    expect(parseList('# header\n\na.js\n  # note\n b.js \n')).toEqual(['a.js', 'b.js']);
  });
});

describe('a built bundle', () => {
  it('joins the members in list order with the separator', () => {
    members({ 'a.js': 'log.push("a")\n', 'b.js': 'log.push("b")' });
    const code = buildBundle(['b.js', 'a.js'], jsDir);
    expect(code).toBe(`log.push("b")${SEPARATOR}log.push("a")\n`);
    expect(execute(code).log).toEqual(['b', 'a']);
  });

  it('keeps an expression from running into the next member', () => {
    // Without the `;` this would parse as `x = 1(function () {…})()`.
    members({ 'a.js': 'var x = 1', 'b.js': '(function () { log.push(x); })()' });
    expect(execute(buildBundle(['a.js', 'b.js'], jsDir)).log).toEqual([1]);
  });

  it('strips source-map comments', () => {
    members({ 'a.js': 'log.push(1);\n//# sourceMappingURL=a.js.map\n' });
    expect(buildBundle(['a.js'], jsDir)).not.toContain('sourceMappingURL');
  });

  it('shares a top-level const between members, as the browser does', () => {
    members({ 'state.js': 'const MAP = { ready: true };', 'map.js': 'log.push(MAP.ready);' });
    expect(execute(buildBundle(['state.js', 'map.js'], jsDir)).log).toEqual([true]);
  });

  it('stops at a member that throws at the top level', () => {
    members({
      'a.js': 'log.push("a");',
      'b.js': 'throw new Error("boom");',
      'c.js': 'log.push("c");',
    });
    const context = execute(buildBundle(['a.js', 'b.js', 'c.js'], jsDir));
    expect(context.log).toEqual(['a']);
    expect(context.error).toBe('boom');
  });
});

describe('what it refuses', () => {
  it.each([
    ["'use strict';\nlog.push(1);"],
    ['/* header */\n// note\n"use strict";\n'],
  ])('a member that opens with a use strict directive: %j', (source) => {
    members({ 'strict.js': source });
    expect(() => buildBundle(['strict.js'], jsDir)).toThrow(/use strict/);
  });

  it('scans a long run of comments in linear time', () => {
    // CodeQL flagged the old regex: '/*' then many '*//*' backtracked.
    members({ 'comments.js': `/*${'*//*'.repeat(50000)}*/\nlog.push(1);` });
    const started = Date.now();
    expect(execute(buildBundle(['comments.js'], jsDir)).log).toEqual([1]);
    expect(Date.now() - started).toBeLessThan(2000);
  });

  it('accepts a source that is only a comment', () => {
    members({ 'note.js': '// nothing else' });
    expect(buildBundle(['note.js'], jsDir)).toBe('// nothing else\n');
  });

  it('accepts use strict inside a function', () => {
    members({ 'iife.js': "(function () { 'use strict'; log.push(1); })();" });
    expect(execute(buildBundle(['iife.js'], jsDir)).log).toEqual([1]);
  });

  it('a member listed twice', () => {
    members({ 'a.js': '' });
    expect(() => buildBundle(['a.js', 'a.js'], jsDir)).toThrow(/listed twice/);
  });

  it('a member that does not exist', () => {
    expect(() => buildBundle(['gone.js'], jsDir)).toThrow(/does not exist/);
  });

  it('an empty list, naming the bundle', () => {
    writeFileSync(path.join(listsDir, 'empty.txt'), '# nothing\n');
    expect(() => run({ listsDir, jsDir, outDir })).toThrow(/bundle empty: the list names no scripts/);
  });
});

describe('run', () => {
  it('writes every list to <name>.bundle.js', () => {
    members({ 'a.js': 'log.push("a");', 'b.js': 'log.push("b");' });
    writeFileSync(path.join(listsDir, 'one.txt'), 'a.js\n');
    writeFileSync(path.join(listsDir, 'two.txt'), '# note\nb.js\na.js\n');
    const built = run({ listsDir, jsDir, outDir });
    expect(built.map((b) => [b.name, b.members])).toEqual([['one', 1], ['two', 2]]);
    const two = readFileSync(path.join(outDir, 'two.bundle.js'), 'utf8');
    expect(execute(two).log).toEqual(['b', 'a']);
  });

  it('builds only the lists it is given', () => {
    members({ 'a.js': '' });
    writeFileSync(path.join(listsDir, 'one.txt'), 'a.js\n');
    writeFileSync(path.join(listsDir, 'two.txt'), 'missing.js\n');
    expect(run({ names: ['one'], listsDir, jsDir, outDir }).map((b) => b.name)).toEqual(['one']);
  });

  it('builds the real lists', () => {
    // The repository's own lists, into the temp dir: every member exists and
    // none opens with 'use strict'.
    const built = run({ outDir });
    expect(built.map((b) => b.name)).toEqual(['map', 'map_surfaces']);
  });
});
