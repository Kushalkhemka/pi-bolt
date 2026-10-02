import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {createTypedPiPlugin} from './plugin.mjs';
import {typeScriptCompilerPath} from './frontend.mjs';
import {piVersionPin} from './versions.mjs';
import {transformPriorityFinalRender, validatePrioritySchedulerSource} from '../optimizations/priority-final-render.js';
import {standaloneWorkerInputs, transformStandaloneWorkerSpecifier, validatePublishedUnicode} from '../optimizations/standalone-workers.js';

const args = new Map();
for (let i = 2; i < process.argv.length; i += 2) {
    const key = process.argv[i], value = process.argv[i + 1];
    if (!key.startsWith('--') || value === undefined || args.has(key)) throw new Error('Expected unique --name value arguments');
    args.set(key, value);
}
const supported = new Set(['--package', '--source-root', '--outfile', '--bytecode-order', '--receipt', '--typescript', '--priority-final-render', '--unicode-fast-path', '--erased-source-control']);
for (const key of args.keys()) if (!supported.has(key)) throw new Error(`Unknown flag ${key}`);
for (const key of ['--package', '--source-root', '--outfile', '--bytecode-order', '--receipt'])
    if (!args.get(key)) throw new Error(`Required: ${key}`);
const flag = (key, fallback = false) => {
    const value = args.get(key) ?? String(fallback);
    if (!['true', 'false'].includes(value)) throw new Error(`${key} requires true or false`);
    return value === 'true';
};
const buildInputURLs = [new URL(import.meta.url), new URL('./frontend.mjs', import.meta.url),
    new URL('./plugin.mjs', import.meta.url), new URL('./compatibility.mjs', import.meta.url),
    new URL('./versions.mjs', import.meta.url), new URL('./versions.json', import.meta.url), new URL('../optimizations/sanitize-unicode.js', import.meta.url),
    new URL('../optimizations/priority-final-render.js', import.meta.url), new URL('../optimizations/standalone-workers.js', import.meta.url)];
const buildInputs = Object.fromEntries(buildInputURLs.map(url => [url.pathname,
    createHash('sha256').update(fs.readFileSync(url)).digest('hex')]));
const typescriptCompilerPath = typeScriptCompilerPath(args.get('--typescript'));
buildInputs[typescriptCompilerPath] = createHash('sha256').update(fs.readFileSync(typescriptCompilerPath)).digest('hex');
const packageDir = fs.realpathSync(args.get('--package'));
const packageData = JSON.parse(fs.readFileSync(path.join(packageDir, 'package.json'), 'utf8'));
const entrypoints = piVersionPin(packageData.version).entrypoints.map(relative => {
    const filename = path.join(packageDir, relative);
    if (!fs.statSync(filename).isFile()) throw new Error(`Missing pinned Pi entrypoint: ${filename}`);
    return filename;
});
const workerInputs = standaloneWorkerInputs(packageDir);
if (JSON.stringify(entrypoints) !== JSON.stringify(workerInputs.entrypoints) || packageData.version !== workerInputs.version)
    throw new Error('Shared standalone worker inputs differ from the pinned checked-source registry');
const outfile = path.resolve(args.get('--outfile'));
const priority = flag('--priority-final-render');
const unicode = flag('--unicode-fast-path', true);
const erasedControl = flag('--erased-source-control');
if (!erasedControl && process.env.BUN_JSC_useSoundTypes !== 'true')
    throw new Error('Checked frontend requires BUN_JSC_useSoundTypes=true during compilation; do not use a compiler that lacks op_check_type');
if (process.env.BUN_JSC_reportSoundTypeViolations === 'true' || process.env.JSC_reportSoundTypeViolations === 'true') throw new Error('Checked AOT requires throwing type guards');
if (fs.existsSync(outfile) || fs.existsSync(args.get('--receipt'))) throw new Error('Choose a fresh executable and receipt path');
const fastPath = fs.realpathSync(path.join(packageDir, 'node_modules/@earendil-works/pi-ai/dist/utils/sanitize-unicode.js'));
const interactivePath = fs.realpathSync(path.join(packageDir, 'dist/modes/interactive/interactive-mode.js'));
const scheduler = fs.readFileSync(path.join(packageDir, 'node_modules/@earendil-works/pi-tui/dist/tui.js'), 'utf8');
if (priority) validatePrioritySchedulerSource(scheduler);
const fastSource = fs.readFileSync(new URL('../optimizations/sanitize-unicode.js', import.meta.url), 'utf8');
let unicodeSubstitutions = 0, prioritySubstitutions = 0, workerSubstitutions = 0;
const typed = createTypedPiPlugin({packageDir, sourceRoot: args.get('--source-root'),
    typescriptPath: args.get('--typescript'), erasedControl,
    exclude: unicode ? [fastPath] : [], transform(contents, filename) {
        if (unicode && filename === fastPath) {
            validatePublishedUnicode(contents);
            unicodeSubstitutions++;
            return fastSource;
        }
        if (priority && filename === interactivePath) {
            prioritySubstitutions++;
            return transformPriorityFinalRender(contents, scheduler);
        }
        const workerContents = transformStandaloneWorkerSpecifier(contents, filename, packageDir, workerInputs.version);
        if (workerContents !== contents) workerSubstitutions++;
        return workerContents;
    }});
const result = await Bun.build({
    entrypoints,
    ...(workerInputs.naming ? {naming: workerInputs.naming} : {}),
    compile: {outfile, autoloadDotenv: false, autoloadBunfig: false, execArgv: [], bytecodeOrder: path.resolve(args.get('--bytecode-order'))},
    bytecode: true, format: 'esm', minify: true, plugins: [typed.plugin]
});
if (!result.success) throw new AggregateError(result.logs, 'Typed subset Pi build failed');
if (unicode && unicodeSubstitutions !== 1) throw new Error(`Expected one Unicode replacement, found ${unicodeSubstitutions}`);
if (priority && prioritySubstitutions !== 1) throw new Error(`Expected one final-render replacement, found ${prioritySubstitutions}`);
if (workerInputs.version === '1.0.0' && workerSubstitutions !== 2)
    throw new Error(`Expected two embedded worker specifier replacements, found ${workerSubstitutions}`);
const checks = typed.assertCoverage();
for (const module of typed.report.modules) {
    if (createHash('sha256').update(fs.readFileSync(module.source_path)).digest('hex') !== module.source_sha256 ||
        createHash('sha256').update(fs.readFileSync(module.installed_path)).digest('hex') !== module.installed_js_sha256)
        throw new Error(`Source changed during typed build: ${module.source_path}`);
}
for (const [filename, expected] of Object.entries(buildInputs))
    if (createHash('sha256').update(fs.readFileSync(filename)).digest('hex') !== expected)
        throw new Error(`Typed frontend input changed during build: ${filename}`);
const receipt = {...typed.report, build_inputs: buildInputs, inserted_check_sites: erasedControl ? 0 : checks,
    entrypoints, entry_naming: workerInputs.naming ?? null, worker_specifier_substitutions: workerSubstitutions,
    standalone_workers_optimizer_sha256: buildInputs[new URL('../optimizations/standalone-workers.js', import.meta.url).pathname],
    typescript_compiler_path: typescriptCompilerPath, typescript_compiler_sha256: buildInputs[typescriptCompilerPath],
    unicode_fast_path: unicode, priority_final_render: priority,
    unicode_substitutions: unicodeSubstitutions, priority_substitutions: prioritySubstitutions,
    build_driver_sha256: createHash('sha256').update(fs.readFileSync(new URL(import.meta.url))).digest('hex'),
    frontend_sha256: createHash('sha256').update(fs.readFileSync(new URL('./frontend.mjs', import.meta.url))).digest('hex'),
    plugin_sha256: createHash('sha256').update(fs.readFileSync(new URL('./plugin.mjs', import.meta.url))).digest('hex'),
    bytecode_order_sha256: createHash('sha256').update(fs.readFileSync(args.get('--bytecode-order'))).digest('hex'),
    limits: 'Primitive original-source checked subset only. Structural/reference types and unsupported mutation targets remain generic; no .d.ts assumptions or full TypeScript soundness. Backend selection/check_type semantics must pass independent four-mode fixtures before performance claims.'};
fs.writeFileSync(args.get('--receipt'), JSON.stringify(receipt, null, 2) + '\n', {flag: 'wx'});
console.log(JSON.stringify({typed_subset: !erasedControl, erased_source_control: erasedControl, typed_modules: typed.report.modules.length,
    generic_modules: typed.report.generic_modules.length, inserted_check_sites: receipt.inserted_check_sites,
    unicode_fast_path: unicode, priority_final_render: priority, receipt: path.resolve(args.get('--receipt'))}));
