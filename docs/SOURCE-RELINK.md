# Native source and relinking

The [Pi 1.0 M5 prerelease](https://github.com/Kushalkhemka/pi-bolt/releases/tag/pi-bolt-v1.0.0-m5.1) supplies three separate archives:

| Archive | Contents | Download size |
|---|---|---:|
| `pi-bolt-m5-1.0.0-m5.1.tar.gz` | Ready-to-run Pi 1.0 runtime and assets | 58 MB |
| `pi-bolt-m5-source-1.0.0-m5.1.tar.gz` | Matching modified Bun/WebKit, native vendor and locked crate sources, Pi source/npm inputs, patches and build scripts | 719 MB |
| `pi-bolt-m5-relink-1.0.0-m5.1.tar.gz` | Ordered application objects and replaceable JSC/WTF/bmalloc archives | 213 MB |

Normal installation needs only the runtime archive and installer. Verify downloaded files against the release's `SHA256SUMS` before extracting them.

The source archive retains original licenses and headers, exact upstream pins and separately recorded dated change notices. `SOURCE.json` records coverage; `PI-BOLT-CHANGES.json` distinguishes original build bytes from the exported comment-only notice overlay. All 180 locked registry crate sources are supplied and verified against Cargo.lock checksums. The snapshot is not a complete offline toolchain distribution.

## Relink the runtime

Extract the relink archive. Install LLVM clang 23.1.2 and macOS 27 developer tools on an ARM64 Mac, then run from the extracted kit:

```bash
python3 relink-runtime.py \
  --clang /absolute/llvm23/bin/clang++ \
  --output /absolute/fresh/relinked

# To substitute compatible modified WebKit archives:
python3 relink-runtime.py \
  --clang /absolute/llvm23/bin/clang++ \
  --webkit-libs /absolute/modified-WebKit/lib \
  --output /absolute/fresh/modified
```

The kit ships application objects, Rust rlibs and native dependency objects; it does not redistribute Apple's SDK. ABI-changing library/header changes may require rebuilding Bun bindings and application objects from source. The included README documents the source-build route and required pinned dependencies.

The release's [RELINK-VALIDATION.json](RELINK-VALIDATION.json) records a successful cold ThinLTO relink from the copied kit, using copied compatible WebKit archives. The fresh compiler reported Bun 1.4.3 and passed all 14 assertion groups in each of four linked-backend fixture modes. This check did not rebuild all sources or test semantically modified WebKit libraries.

After modifying the runtime, regenerate **both** Pi executable and AOT sidecar using the included `pi.py`, `build-pi.js`, Pi1.0 npm inputs and workload profile. An old sidecar must not be reused with a modified runtime. Run the provided native, worker and CLI checks against the regenerated pair.

Successful relinking is technical evidence. It does not establish a full clean source rebuild, arbitrary ABI compatibility, legal clearance, signing/notarization or a production performance guarantee. The frozen candidate manifest retains its original prepublication gate state; this companion is delivered separately. Original file-level BSD/Library GPL terms apply to WebKit, alongside the separately preserved licenses of other components.

## Prepare a matching companion

Maintainers with the exact source trees and build objects can export a fresh companion with:

```bash
python3 native-aot/release/prepare-companion.py \
  --package /absolute/validated/pi-bolt-m5-1.0.0-m5.1 \
  --output /absolute/fresh/companion
```

The exporter verifies pins and build inputs before copying. It preserves original component licenses, records dated source notices separately, and records path redactions in optional generated reference evidence. Review the resulting source/object archives and run the relocated relink checks before publishing them.
