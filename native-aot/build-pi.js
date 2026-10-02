import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";
import { transformPriorityFinalRender, validatePrioritySchedulerSource } from "./optimizations/priority-final-render.js";
import { standaloneWorkerInputs, transformStandaloneWorkerSpecifier, transformStandalonePhoton, validatePublishedUnicode } from "./optimizations/standalone-workers.js";
const [packageDir, outfile, useFastUnicode, compileTarget, bytecodeOrder, execArgvJSON, usePriorityFinalRender] = process.argv.slice(2);
if (usePriorityFinalRender !== undefined && !["true", "false"].includes(usePriorityFinalRender))
    throw new Error("usePriorityFinalRender must be true or false");
const execArgv = execArgvJSON ? JSON.parse(execArgvJSON) : [];
if (!Array.isArray(execArgv) || execArgv.some(value => typeof value !== "string"))
    throw new Error("execArgv must be a JSON array of strings");
const workerInputs = standaloneWorkerInputs(packageDir);
const expected = path.join(packageDir, "node_modules/@earendil-works/pi-ai/dist/utils/sanitize-unicode.js");
const fastSource = fs.readFileSync(new URL("./optimizations/sanitize-unicode.js", import.meta.url), "utf8");
const priorityEnabled = usePriorityFinalRender === "true";
const interactivePath = path.resolve(packageDir, "dist/modes/interactive/interactive-mode.js");
const schedulerSource = priorityEnabled ? fs.readFileSync(path.join(packageDir,
    "node_modules/@earendil-works/pi-tui/dist/tui.js"), "utf8") : undefined;
if (priorityEnabled) validatePrioritySchedulerSource(schedulerSource);
const priorityHash = priorityEnabled ? createHash("sha256").update(fs.readFileSync(
    new URL("./optimizations/priority-final-render.js", import.meta.url))).digest("hex") : null;
let substitutions = 0;
let prioritySubstitutions = 0;
const plugins = useFastUnicode === "true" ? [{
    name: "pi-aot-unicode-fast-path",
    setup(builder) {
        builder.onLoad({filter: /sanitize-unicode\.js$/}, args => {
            if (path.resolve(args.path) !== expected) return;
            const original = fs.readFileSync(args.path, "utf8");
            validatePublishedUnicode(original);
            substitutions++;
            return {contents: fastSource, loader: "js"};
        });
    }
}] : [];
let workerSubstitutions = 0;
let photonSubstitutions = 0;
if (workerInputs.version === "1.0.0") plugins.push({
    name: "pi-standalone-adjacent-photon-wasm",
    setup(builder) {
        builder.onLoad({filter: /photon_rs\.js$/}, args => {
            const original = fs.readFileSync(args.path, "utf8");
            const contents = transformStandalonePhoton(original, args.path, packageDir, workerInputs.version);
            if (contents === original) return;
            photonSubstitutions++;
            return {contents, loader: "js"};
        });
    }
});
if (workerInputs.version === "1.0.0") plugins.push({
    name: "pi-npm-standalone-worker-specifiers",
    setup(builder) {
        builder.onLoad({filter: /(?:config|image-resize)\.js$/}, args => {
            const original = fs.readFileSync(args.path, "utf8");
            const contents = transformStandaloneWorkerSpecifier(original, args.path, packageDir, workerInputs.version);
            if (contents === original) return;
            workerSubstitutions++;
            return {contents, loader: "js"};
        });
    }
});
if (priorityEnabled) plugins.push({
    name: "pi-priority-final-render",
    setup(builder) {
        builder.onLoad({filter: /interactive-mode\.js$/}, args => {
            if (path.resolve(args.path) !== interactivePath) return;
            const contents = transformPriorityFinalRender(fs.readFileSync(args.path, "utf8"), schedulerSource);
            prioritySubstitutions++;
            return {contents, loader: "js"};
        });
    }
});
const result = await Bun.build({
    entrypoints: workerInputs.entrypoints,
    ...(workerInputs.naming ? {naming: workerInputs.naming} : {}),
    compile: {outfile, autoloadDotenv: false, autoloadBunfig: false, execArgv,
        ...(compileTarget ? {target: compileTarget} : {}), ...(bytecodeOrder ? {bytecodeOrder} : {})},
    bytecode: true, format: "esm", minify: true, plugins
});
if (!result.success) throw new AggregateError(result.logs, "Pi build failed");
if (useFastUnicode === "true" && substitutions !== 1) throw new Error(`Expected one Unicode substitution, found ${substitutions}`);
if (priorityEnabled && prioritySubstitutions !== 1) throw new Error(`Expected one priority final-render substitution, found ${prioritySubstitutions}`);
if (workerInputs.version === "1.0.0" && workerSubstitutions !== 2)
    throw new Error(`Expected two embedded worker specifier substitutions, found ${workerSubstitutions}`);
if (workerInputs.version === "1.0.0" && photonSubstitutions !== 1)
    throw new Error(`Expected one standalone Photon Wasm relocation, found ${photonSubstitutions}`);
console.log(JSON.stringify({unicode_fast_path: useFastUnicode === "true", substitutions,
    pi_version: workerInputs.version, worker_entrypoints: workerInputs.entrypoints,
    worker_specifier_substitutions: workerSubstitutions,
    standalone_photon_wasm_substitutions: photonSubstitutions,
    image_worker_termination_awaited: workerInputs.version === "1.0.0",
    standalone_workers_optimizer_sha256: createHash("sha256").update(fs.readFileSync(new URL("./optimizations/standalone-workers.js", import.meta.url))).digest("hex"),
    priority_final_render: priorityEnabled, priority_final_render_substitutions: prioritySubstitutions,
    priority_final_render_optimizer_sha256: priorityHash}));
