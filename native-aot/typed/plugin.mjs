import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {loadTypeScript, eraseTypeScript, normalizedJavaScript, checkedTypeScript} from './frontend.mjs';
import {piContractOverrides} from './compatibility.mjs';
import {piVersionPin} from './versions.mjs';

export const PI_SOURCE_COMMIT = piVersionPin('0.85.1').source_commit;
const sha = text => createHash('sha256').update(text).digest('hex');

export function createTypedPiPlugin({packageDir, sourceRoot, typescriptPath, erasedControl = false,
    exclude = [], transform = (contents) => contents}) {
    packageDir = fs.realpathSync(packageDir);
    sourceRoot = fs.realpathSync(sourceRoot);
    const packageData = JSON.parse(fs.readFileSync(path.join(packageDir, 'package.json'), 'utf8'));
    const pin = piVersionPin(packageData.version);
    const commit = execFileSync('git', ['-C', sourceRoot, 'rev-parse', 'HEAD'], {encoding: 'utf8'}).trim();
    if (commit !== pin.source_commit) throw new Error(`Expected Pi ${packageData.version} source commit ${pin.source_commit}, found ${commit}`);
    if (execFileSync('git', ['-C', sourceRoot, 'status', '--porcelain', '--untracked-files=no'], {encoding: 'utf8'}).trim())
        throw new Error('Typed Pi source checkout contains tracked edits');
    const ts = loadTypeScript(typescriptPath);
    const roots = [];
    for (const [name, relative] of Object.entries(pin.packages)) {
        const installed = name.endsWith('/pi-coding-agent') ? packageDir :
            fs.realpathSync(path.join(packageDir, 'node_modules', name));
        const actual = JSON.parse(fs.readFileSync(path.join(installed, 'package.json'), 'utf8'));
        const upstream = path.join(sourceRoot, 'packages', relative);
        const original = JSON.parse(fs.readFileSync(path.join(upstream, 'package.json'), 'utf8'));
        if (actual.name !== name || actual.version !== packageData.version || original.name !== name || original.version !== actual.version)
            throw new Error(`Pi source/package mismatch for ${name}`);
        roots.push({installed, upstream, name});
    }
    const excluded = new Set(exclude.map(p => path.resolve(p)));
    const report = {schema: 'pi-primitive-checked-subset-v1', pi_version: packageData.version, original_source_commit: commit,
        source_root: sourceRoot, typescript_version: ts.version, erased_source_control: erasedControl,
        modules: [], generic_modules: [], annotations_preserved_globally: false};
    const plugin = {name: 'pi-original-source-checked-primitive-subset', setup(builder) {
        builder.onLoad({filter: /\.js$/}, args => {
            const installedPath = fs.realpathSync(args.path);
            const root = roots.find(r => installedPath.startsWith(r.installed + '/dist/'));
            const originalJS = fs.readFileSync(installedPath, 'utf8');
            if (originalJS.includes('$$t')) {
                const parsed = ts.createSourceFile(installedPath, originalJS, ts.ScriptTarget.ES2022, true, ts.ScriptKind.JS);
                const reserve = node => {
                    if (ts.isIdentifier(node) && node.text === '$$t') throw new Error(`${installedPath}: $$t is reserved; source cannot bind/shadow or invoke it`);
                    ts.forEachChild(node, reserve);
                };
                reserve(parsed);
            }
            if (!root) return;
            if (excluded.has(installedPath)) {
                report.generic_modules.push({installed_path: installedPath, reason: 'Explicit competing transform exclusion'});
                return {contents: transform(originalJS, installedPath), loader: 'js', resolveDir: path.dirname(installedPath)};
            }
            const relative = path.relative(path.join(root.installed, 'dist'), installedPath);
            const source = path.join(root.upstream, 'src', relative.replace(/\.js$/, '.ts'));
            if (!fs.existsSync(source)) {
                report.generic_modules.push({installed_path: installedPath, reason: 'No matching original TypeScript file'});
                return {contents: transform(originalJS, installedPath), loader: 'js', resolveDir: path.dirname(installedPath)};
            }
            const originalTS = fs.readFileSync(source, 'utf8');
            const erased = eraseTypeScript(ts, originalTS, source);
            if (normalizedJavaScript(ts, erased) !== normalizedJavaScript(ts, originalJS)) {
                report.generic_modules.push({installed_path: installedPath, source_path: source,
                    source_sha256: sha(originalTS), installed_js_sha256: sha(originalJS),
                    reason: 'Original-source erasure differs from installed JavaScript; keep npm module generic'});
                return {contents: transform(originalJS, installedPath), loader: 'js', resolveDir: path.dirname(installedPath)};
            }
            const checked = checkedTypeScript(ts, originalTS, source, {contractOverrides: piContractOverrides(ts, originalTS, root.name, relative, packageData.version)});
            const output = erasedControl ? erased : checked.contents;
            report.modules.push({installed_path: installedPath, source_path: source,
                source_sha256: sha(originalTS), installed_js_sha256: sha(originalJS),
                erased_js_sha256: sha(erased), checked_js_sha256: sha(checked.contents), ...checked.report});
            return {contents: transform(output, installedPath), loader: 'js', resolveDir: path.dirname(installedPath)};
        });
    }};
    return {plugin, report, assertCoverage() {
        const checks = report.modules.reduce((sum, m) => sum + m.entry_checks + m.read_checks + m.write_checks + m.return_checks + m.assertion_checks, 0);
        if (!checks) throw new Error('No supported checked original-source annotations reached the bundle');
        return checks;
    }};
}
