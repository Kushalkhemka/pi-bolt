// The npm worker entries are JavaScript; upstream source-release specifiers name TypeScript.
// Flat, explicit entry naming gives both workers deterministic embedded-module names.
import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";

const PUBLISHED = {
    "dist/config.js": ["65fb18914aed61cb62d6ef932131a9b66a4e37e482d2aa09136301267929dbed",
        '"./src/extensions/codemode/worker.ts"', '"./worker.js"'],
    "dist/utils/image-resize.js": ["87a8f1d940612932991b122b66b47cef13a981744b837f738d00c7ccf46aa20b",
        '"./src/utils/image-resize-worker.ts"', '"./image-resize-worker.js"'],
};

export function standaloneWorkerInputs(packageDir) {
    const version = JSON.parse(fs.readFileSync(path.join(packageDir, "package.json"), "utf8")).version;
    const entries = ["dist/bun/cli.js", "dist/utils/image-resize-worker.js"];
    if (version === "1.0.0") entries.push("dist/extensions/codemode/worker.js");
    for (const entry of entries) if (!fs.statSync(path.join(packageDir, entry)).isFile())
        throw new Error(`Missing Pi worker/CLI entry: ${entry}`);
    return { version, entrypoints: entries.map(entry => path.join(packageDir, entry)),
        ...(version === "1.0.0" ? { naming: { entry: "[name].[ext]" } } : {}) };
}

export function transformStandaloneWorkerSpecifier(contents, filename, packageDir, version) {
    if (version !== "1.0.0") return contents;
    const relative = path.relative(path.resolve(packageDir), path.resolve(filename)).split(path.sep).join("/");
    const pin = PUBLISHED[relative];
    if (!pin) return contents;
    const original = fs.readFileSync(filename);
    if (createHash("sha256").update(original).digest("hex") !== pin[0]
        || contents !== original.toString("utf8") || contents.split(pin[1]).length !== 2)
        throw new Error(`Pi ${version} ${relative} changed; revalidate the embedded worker specifier`);
    let transformed = contents.replace(pin[1], pin[2]);
    if (relative === "dist/utils/image-resize.js") {
        const cleanup = "void worker.terminate().catch(() => undefined);";
        if (transformed.split(cleanup).length !== 2)
            throw new Error("Pi image worker cleanup changed; revalidate awaited termination");
        transformed = transformed.replace(cleanup, "await worker.terminate().catch(() => undefined);");
    }
    return transformed;
}

export function validatePublishedUnicode(source) {
    if (createHash("sha256").update(source).digest("hex") !== "77cb844b43f502cc24b25ef284ec5a6f6ac375ac994bcf688275848e1475bc80")
        throw new Error("Pi Unicode implementation changed; revalidate the optimization before building");
}

// wasm-bindgen's CJS __dirname is folded into the build machine's path by
// standalone bundling. Always use the adjacent, shipped Wasm for this entry;
// the upstream missing-file fallback cannot handle inaccessible build paths.
export function transformStandalonePhoton(contents, filename, packageDir, version) {
    if (version !== "1.0.0") return contents;
    const expected = path.resolve(packageDir, "node_modules/@silvia-odwyer/photon-node/photon_rs.js");
    if (path.resolve(filename) !== expected) return contents;
    const pin = "d60656705f0d59baa79e36b0381eb023f1864eeb57e92956cf21dcd9fb8f879f";
    const original = "const path = require('path').join(__dirname, 'photon_rs_bg.wasm');";
    if (createHash("sha256").update(contents).digest("hex") !== pin || contents.split(original).length !== 2)
        throw new Error("Pi 1.0 Photon loader changed; revalidate standalone Wasm relocation");
    return contents.replace(original,
        "const path = require('path').join(require('path').dirname(process.execPath), 'photon_rs_bg.wasm');");
}
