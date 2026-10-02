import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import {loadTypeScript, checkedTypeScript} from './frontend.mjs';
import {piContractOverrides} from './compatibility.mjs';

const ts = loadTypeScript();
for (const version of ['0.85.1', '1.0.0']) {
const filename = new URL(`../sources/pi-v${version}/packages/coding-agent/src/main.ts`, import.meta.url);
const source = fs.readFileSync(filename, 'utf8');
const overrides = piContractOverrides(ts, source, '@earendil-works/pi-coding-agent', 'main.js', version);
const file = ts.createSourceFile(filename.pathname, source, ts.ScriptTarget.ES2022, true);
const fn = file.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'resolveAppMode');
const result = checkedTypeScript(ts, fn.getText(file), 'boundary.ts', {contractOverrides: overrides});
const context = {$$t(value, mask) {
    const tag = value === undefined ? 1 : typeof value === 'boolean' ? 4 : 0;
    if (!(tag & mask)) throw new TypeError('contract');
    return value;
}};
vm.createContext(context);
vm.runInContext(result.contents, context);
assert.equal(context.resolveAppMode({mode: 'rpc'}, undefined, undefined), 'rpc');
assert.equal(context.resolveAppMode({mode: 'json'}, undefined, undefined), 'json');
assert.equal(context.resolveAppMode({}, undefined, undefined), 'print');
assert.equal(context.resolveAppMode({}, true, true), 'interactive');
assert.equal(context.resolveAppMode({print: true}, true, true), 'print');
assert.throws(() => context.resolveAppMode({}, 'wrong', true), TypeError);
assert.throws(() => context.resolveAppMode({}, true, null), TypeError);
assert.equal(result.report.contract_overrides.length, 2);
assert.throws(() => piContractOverrides(ts, source + '\n', '@earendil-works/pi-coding-agent', 'main.js', version), /input changed/);
const otherVersion = version === '0.85.1' ? '1.0.0' : '0.85.1';
assert.throws(() => piContractOverrides(ts, source, '@earendil-works/pi-coding-agent', 'main.js', otherVersion), /input changed/);
console.log(`PASS: Pi ${version} pinned TTY boundary keeps boolean|undefined guards, rejects other values, changed inputs and cross-version override reuse`);
}
