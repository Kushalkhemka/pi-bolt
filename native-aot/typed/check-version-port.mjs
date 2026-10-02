import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createTypedPiPlugin} from './plugin.mjs';
import {piVersionPin} from './versions.mjs';
import {loadTypeScript, normalizedJavaScript} from './frontend.mjs';
import {standaloneWorkerInputs, transformStandaloneWorkerSpecifier, validatePublishedUnicode} from '../optimizations/standalone-workers.js';

// Preparation only: exercise onLoad against pinned npm/source pairs without a
// bundler, native compiler, sidecar export, or timing workload.
const base = path.resolve(new URL('..', import.meta.url).pathname);
const ts = loadTypeScript();
const allModules = process.argv.slice(2).includes('--all-modules');
assert.ok(process.argv.slice(2).every(arg => arg === '--all-modules'), 'Only --all-modules is supported');
const javascriptFiles = directory => fs.readdirSync(directory, {withFileTypes: true}).flatMap(entry => {
    const filename = path.join(directory, entry.name);
    return entry.isDirectory() ? javascriptFiles(filename) : entry.isFile() && entry.name.endsWith('.js') ? [filename] : [];
});
assert.throws(() => piVersionPin('1.0.1'), /Unsupported/);
for (const [version, packageDir] of [
    ['0.85.1', '/opt/homebrew/lib/node_modules/@earendil-works/pi-coding-agent'],
    ['1.0.0', path.join(base, 'pi-1.0-runtime/node_modules/@earendil-works/pi-coding-agent')],
]) {
    const pin = piVersionPin(version);
    const sourceRoot = path.join(base, `sources/pi-v${version}`);
    let checkedWorkers = 0, erasedWorkers = 0;
    const transform = counter => (contents, filename) => {
        const result = transformStandaloneWorkerSpecifier(contents, filename, packageDir, version);
        if (result !== contents) counter();
        return result;
    };
    const checked = createTypedPiPlugin({packageDir, sourceRoot, transform: transform(() => checkedWorkers++)});
    const erased = createTypedPiPlugin({packageDir, sourceRoot, erasedControl: true, transform: transform(() => erasedWorkers++)});
    const workerInputs = standaloneWorkerInputs(packageDir);
    assert.deepEqual(workerInputs.entrypoints, pin.entrypoints.map(entry => path.join(packageDir, entry)));
    assert.deepEqual(workerInputs.naming ?? null, version === '1.0.0' ? {entry: '[name].[ext]'} : null);
    validatePublishedUnicode(fs.readFileSync(path.join(packageDir, 'node_modules/@earendil-works/pi-ai/dist/utils/sanitize-unicode.js'), 'utf8'));
    let loadChecked, loadErased;
    checked.plugin.setup({onLoad(_options, callback) { loadChecked = callback; }});
    erased.plugin.setup({onLoad(_options, callback) { loadErased = callback; }});
    const probes = new Set(pin.entrypoints.map(entry => path.join(packageDir, entry)));
    probes.add(path.join(packageDir, 'dist/main.js'));
    if (version === '1.0.0') {
        probes.add(path.join(packageDir, 'dist/config.js'));
        probes.add(path.join(packageDir, 'dist/utils/image-resize.js'));
    }
    for (const name of Object.keys(pin.packages)) {
        const root = name === '@earendil-works/pi-coding-agent' ? packageDir : path.join(packageDir, 'node_modules', name);
        probes.add(path.join(root, 'dist/index.js'));
        if (allModules) for (const filename of javascriptFiles(path.join(root, 'dist'))) probes.add(filename);
    }
    let checkedProbes = 0, genericProbes = 0;
    for (const filename of probes) {
        assert.ok(fs.statSync(filename).isFile(), `${version}: missing entry/probe ${filename}`);
        const c = loadChecked({path: filename}), e = loadErased({path: filename});
        assert.ok(c && e, `${version}: package root was not recognized: ${filename}`);
        const original = fs.readFileSync(filename, 'utf8');
        const expected = transformStandaloneWorkerSpecifier(original, filename, packageDir, version);
        assert.equal(normalizedJavaScript(ts, e.contents), normalizedJavaScript(ts, expected),
            `${version}: erased-source control changes npm program: ${filename}`);
        if (c.contents !== e.contents) checkedProbes++; else genericProbes++;
    }
    assert.ok(checkedProbes > 0);
    assert.equal(checked.report.pi_version, version);
    assert.equal(checkedWorkers, version === '1.0.0' ? 2 : 0);
    assert.equal(erasedWorkers, checkedWorkers);
    assert.equal(checked.report.original_source_commit, pin.source_commit);
    assert.equal(checked.report.modules.length, erased.report.modules.length);
    assert.deepEqual(checked.report.modules, erased.report.modules);
    assert.deepEqual(checked.report.generic_modules, erased.report.generic_modules);
    assert.ok(checked.assertCoverage() > 0);
    const main = checked.report.modules.find(m => m.installed_path.endsWith('/dist/main.js'));
    assert.equal(main?.contract_overrides.length, 2);
    if (version === '1.0.0') {
        assert.equal(pin.entrypoints.length, 3);
        assert.ok(checked.report.modules.some(m => m.installed_path.endsWith('/dist/extensions/codemode/worker.js')));
    } else assert.equal(pin.entrypoints.length, 2);
    const otherSource = path.join(base, `sources/pi-v${version === '1.0.0' ? '0.85.1' : '1.0.0'}`);
    assert.throws(() => createTypedPiPlugin({packageDir, sourceRoot: otherSource}), /Expected Pi .* source commit/);
    console.log(JSON.stringify({status: 'PASS', version, all_modules: allModules, package_roots: Object.keys(pin.packages).length,
        entrypoints: pin.entrypoints.length, probes: probes.size, checked_probes: checkedProbes,
        unchanged_or_generic_probes: genericProbes, modules: checked.report.modules.length,
        generic_modules: checked.report.generic_modules.length, check_sites: checked.assertCoverage()}));
}
