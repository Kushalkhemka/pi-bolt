# macOS ARM64 release asset and license review

This is an evidence inventory and delivery plan, not legal clearance. The proposed unsigned local package can preserve notices and portable assets, but publication should not be described as a completed source/relink distribution until that delivery path is assembled and verified.

## Runtime assets

The selected `artifacts/darwin-arm64-m5-pi-v1-v2-priority` contains ten absolute asset symlinks. Dereference these into the portable package and reject any remaining outbound symlink. The compiled executable and `.aot` sidecar are a matched pair; the package-relative launcher must resolve both without the original workspace or npm installation.

| Asset | Use / source evidence |
| --- | --- |
| `package.json` | Pi version/package configuration, config.js353–355. |
| `theme/` | Built-in terminal themes, config.js325–337. |
| `assets/` | Interactive artwork, config.js376–389. Artwork attribution should accompany the source tree; a generic package SPDX field is not separate artwork clearance. |
| `export-html/` | HTML templates, renderer and vendored Marked/highlight.js, config.js340–351. Needed for HTML export. |
| `native/darwin/prebuilds/darwin-arm64/darwin-platform.node` | Pi TUI AppKit helper for modifiers and clipboard; native-platform.js11–16. Retain helper source/build instructions. The helper uses the Apple SDK; do not redistribute SDK files. |
| `photon_rs_bg.wasm` | Photon image resize fallback beside executable, utils/photon.js33–39. Keep the package Apache2.0 notice and audit its compiled Rust dependencies. |
| `docs/`, `examples/`, `README.md`, `CHANGELOG.md` | Documentation/help/example flows; config.js358–371. Examples may contain additional package metadata/notices and should not silently lose them. |

QuickJS Wasm and the codemode/image worker JavaScript entries are embedded by this build, rather than external runtime symlinks. `config.js397–398`, `dist/bun/runtime-setup.js`, and the manifest's `codemode_worker_embedded:true` provide the source/build evidence. Packaging must still validate worker execution after relocation. The normal tool workflows also depend on host shells and external `rg`/`fd` discovery or download (`utils/tools-manager.js300–322`). If those binaries are added to the release, collect their exact-version notices independently. User-installed extensions and their dependencies are outside the binary's bundled dependency inventory.

## Available original notices and unresolved scope

Pi1.0 source `LICENSE` is MIT, copyright Mario Zechner. Some published `@earendil-works/*` npm packages declare MIT but omit a top-level license file; preserve the pinned Pi source license and establish the package-to-source relationship rather than synthesizing a license from the SPDX label.

Bun's [own license document](https://github.com/oven-sh/bun/blob/37da174d500f2201793c8352f791afdd9102eab9/LICENSE.md) identifies MIT Bun code, LGPL JavaScriptCore/WebKit, linked libraries, and embedded polyfills. JavaScriptCore also carries mixed file-level BSD/LGPL notices; preserve original headers and `Source/JavaScriptCore/COPYING.LIB`, not a blanket replacement license. Its static linking/source and relinking provisions require a concrete applicable delivery path. TinyCC is separately listed LGPL2.1; reconcile its actual link/build involvement. BoringSSL/libarchive, Rust crates, ICU, native libraries and toolchain runtime notices need review against exact build inputs; scanning every available source notice is conservative and can include unlinked components.

MIT requires preservation of its copyright/permission notice. BSD notices include attribution/disclaimer and applicable endorsement restrictions. Apache2.0 components such as Photon require the license and relevant original notices, with change notices where applicable; see the [official Photon license](https://github.com/silvia-odwyer/photon/blob/master/LICENSE.md). No trademark endorsement or Apple SDK redistribution right follows from these notices.

Exact upstream notices were retrieved and pinned in `release/notices/provenance.json`:

| Component | Source commit / evidence |
| --- | --- |
| quickjs-wasi3.6.2 | Tag `quickjs-wasi@3.6.2`, commit `5a7a0eeda87c99542f8cf3095b6d61ecfa755977`; original Vercel MIT license, Makefile and `.gitmodules`. Installed Wasm is byte-identical to the official npm3.6.2 tarball. Npm metadata supplies no `gitHead`, so this is not proof of a reproducible source-to-Wasm build. |
| QuickJS-NG | The release tree's `quickjs-ng` gitlink selects `6d46d07d04041b40f4f49eaa7fdebe44c314c699`; [original MIT notice](https://github.com/quickjs-ng/quickjs/blob/6d46d07d04041b40f4f49eaa7fdebe44c314c699/LICENSE) preserves Bellard/Gordon/Noordhuis/Ibarra copyrights. |
| HTML Marked18.0.5 | Header identifies18.0.5, not installed npm18.0.11. Tag `v18.0.5`, commit `4063c638cb621c09091d41b26f323ff074416bb9`; full LICENSE contains both Marked MIT and Markdown attribution terms. |
| HTML highlight.js11.9.0 | Header identifies11.9.0, not installed npm10.7.3. Tag `11.9.0`, commit `15d3b627fa7c99cb98d7b6760a6fbdbfd519d1a0`; full BSD3-Clause notice names Ivan Sagalaev, correcting the insufficient generated `undefined` header attribution. |

The collector preserves original notice bytes, code-header extracts separately, package metadata, source commits, SHA256s, supplementary upstream provenance, and the asset symlink inventory. `collection_complete:true` does not imply `legal_clearance:true`, a precise linked SBOM, or a completed source/relink bundle. Remaining items are recorded explicitly.

```sh
python3 native-aot/release/collect-licenses.py --output /absolute/fresh/notices --dry-run
python3 native-aot/release/collect-licenses.py --output /absolute/fresh/notices
```

Dry-run verification found153 package metadata entries, ten external asset links, hundreds of original notice files and thousands of original code-header origins. No compiler, benchmark or application workload was run for this review.

## Concrete source/relink delivery plan

Deliver a source/rebuild companion alongside the binary, with the original upstream trees or reliably available exact corresponding sources, our modifications, build scripts, lockfiles, notices and a change record. Merely linking current upstream `main` is inadequate for the modified runtime. The existing lab build route has completed on this host; rebuilding/relinking the companion after relocation remains a release gate.

Pinned inputs for this M5 artifact:

- Bun commit `37da174d500f2201793c8352f791afdd9102eab9` at `https://github.com/oven-sh/bun.git`.
- WebKit commit `d28f16e5cc234c19054d7edec2a2de5519500a1f` at `https://github.com/oven-sh/WebKit.git`.
- Pi1.0 commit `a13d35a742c6ef8462812a28fbe1d8c8b7431c32` and the isolated npm1.0.0 lockfile.
- `patches/jsc-image-export.patch` SHA256 `f43a7183b96bcce733f070d51fc7b910a25976b936ec3cf3d234f55e30ccc1c0`.
- `patches/bun-aot-engine-init.patch` SHA256 `4bac907d330af21a218841e95f06fad09e293efa68df4e0194a84122423d9eed`.
- `pi-1.0-runtime/package-lock.json` SHA256 `37eba8fd45c24fea4d106ffe1eb6395ec7a8096ed2026757928f8d93bea5b641`; package.json SHA256 `1a32c7edac8cd9d7826b4d0cc940fddfca0627fce0633ad320571f795f606f5e`.
- Preserve `sources.lock.json`, `lab.py`, `pi.py`, `build-pi.js`, `optimizations/`, the profile `research/pi-v1-v2-workload.order`, fixtures/checkers, and recorded toolchain/dependency build inputs. The Linux portability supplement is not part of this Mac source patch pair.

From an isolated companion checkout on macOS ARM64 with Python3.12+, Bun1.4.2 bootstrap, Node/npm and the Apple developer tools, use the actual guarded lab route:

```sh
python3 native-aot/lab.py bootstrap
python3 native-aot/lab.py build-bun --bun /absolute/bootstrap/bun --profile m5-aot-release --jobs 2
```

`lab.py` verifies source pins and applies the exact patches. It drives Bun's official `scripts/build.ts --profile=m5-aot-release --build-dir=build/m5-aot-release --webkit-version=d28f16e5cc234c19054d7edec2a2de5519500a1f -j2` with `BUN_WEBKIT_PATH` pointing at the patched WebKit checkout. Preserve fetched submodules/vendor crates and the exact recorded build environment for an offline source bundle; this command alone is not an offline/reproducibility guarantee.

For a recipient's modified JSC, rebuild the runtime using this source route, install Pi from the supplied npm lock in a fresh directory, and regenerate a matched executable plus sidecar with the rebuilt compiler:

```sh
npm ci --prefix native-aot/pi-1.0-runtime --ignore-scripts
python3 native-aot/pi.py build --runtime /absolute/companion/native-aot/sources/bun/build/m5-aot-release/bun --pipeline linked --pi-package /absolute/companion/native-aot/pi-1.0-runtime/node_modules/@earendil-works/pi-coding-agent --bytecode-order /absolute/companion/native-aot/research/pi-v1-v2-workload.order --priority-final-render
```

Then run the provided release-gate, backend, image-pair, worker and CLI checkers against the regenerated pair. Keep original notices and visibly identify modifications and dates. Decide and document an applicable LGPL source/object/relink distribution option for the actual linked components; if using object delivery, ship the needed non-JSC objects/static libraries and link inputs, not only the final executable. Our source-route instructions do not yet prove all legal or technical relinking requirements are fulfilled.
