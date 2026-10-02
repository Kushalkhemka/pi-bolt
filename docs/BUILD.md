# Build and validate Pi-Bolt

Pi 1.0.0 with a native AOT + JavaScriptCore hybrid runtime. Covered functions start in build-time machine code; dynamically loaded code retains JSC's interpreter/JIT support. The default uses two GC workers. An optional 10,000-call tiering policy trades additional CPU and memory for sustained-session wall time.

**Current distribution: unsigned release candidate, not a production-certified release.** Supports Apple M5 and macOS **27.0 or later**; the base M5 is the tested host. M4, older macOS versions and Linux are outside this package's support contract. Developer ID signing/notarization, a second M5 host, full source/relink delivery and broader provider/extension soak remain release gates.

[Candidate verification](M5-UNSIGNED-CANDIDATE.md) records the exact archive SHA256, repairs, acceptance and installation evidence.

## Install a candidate

Obtain the complete `pi-bolt-m5-1.0.0-m5.1.tar.gz`, its trusted SHA256, and `native-aot/release/install.sh` from the same release. Download binaries as GitHub Release assets; don't commit executables or the sidecar to Git.

```sh
bash native-aot/release/install.sh \
  --archive /absolute/path/pi-bolt-m5-1.0.0-m5.1.tar.gz \
  --sha256 THE_64_CHARACTER_RELEASE_SHA256 \
  --allow-unsigned

"$HOME/.local/pi-bolt/bin/pi-bolt" --version
"$HOME/.local/pi-bolt/bin/pi-bolt"
# Optional sustained-session policy:
"$HOME/.local/pi-bolt/bin/pi-bolt-tier10000"
# Regular terminal layout:
"$HOME/.local/pi-bolt/bin/pi-bolt" --tui-mode regular
```

No Node, Bun, npm, Python or sudo is needed to run the complete package. Installation uses built-in macOS tools and verifies the external archive pin and internal file checksums before activation. Checksums detect corruption; trust the publisher/channel supplying them. An unsigned downloaded executable may still be rejected by macOS Gatekeeper; this installer does not remove quarantine or change security settings. Build locally or wait for a signed release if your Mac blocks it.

Add `$HOME/.local/pi-bolt/bin` to your own PATH if desired. The installer keeps version directories and uses atomic symlink activation. It does not change existing `pi`, shell profiles or provider credentials. Pi continues to use its normal user settings and sessions; your own extensions/tools execute with your account's permissions.

```sh
# Full integrity check; unsigned Gatekeeper assessment will report rejection.
"$HOME/.local/pi-bolt/bin/pi-bolt-doctor"
# After installing a later version:
bash native-aot/release/manage.sh rollback "$HOME/.local/pi-bolt"
# Remove only the managed commands; preserve versions and user data:
bash native-aot/release/manage.sh unlink "$HOME/.local/pi-bolt"
```

## Build and review

Maintainers need Python 3.12+ (3.14 tested), Git, npm/Node, bootstrap Bun, Xcode/Apple command-line tools and the pinned Bun native build dependencies: LLVM 23.1 and Rust nightly 2026-09-15. The build downloads pinned upstream toolchains/dependencies; it is not an offline or bit-for-bit reproducibility claim. Use a separate checkout and limit concurrency on 16 GB machines.

```sh
npm ci --prefix native-aot/pi-1.0-runtime --ignore-scripts
python3 native-aot/lab.py bootstrap
python3 native-aot/lab.py build-bun --bun /absolute/path/bootstrap/bun \
  --profile m5-aot-release --jobs 2

# Use a fresh absolute output directory; never pair images with another engine.
export PI_NATIVE_ARTIFACT_DIR="$PWD/native-aot/artifacts/local-m5"
python3 native-aot/pi.py build \
  --runtime native-aot/sources/bun/build/m5-aot-release/bun \
  --pi-package native-aot/pi-1.0-runtime/node_modules/@earendil-works/pi-coding-agent \
  --pipeline linked --bytecode-order native-aot/research/pi-v1-v2-workload.order \
  --priority-final-render
python3 native-aot/pi.py verify
python3 native-aot/pi.py validate
```

The release packager currently requires the pinned validated compiler hash. A fresh compiler build may have another hash and must be revalidated and deliberately enrolled before packaging; local `pi.py` launchers remain available after validation. Source pins, actual transformations and patch hashes are recorded. [Source/relink requirements](../native-aot/release/license-review.md) explain the modified JSC distribution obligations; notices alone do not establish a complete relinking distribution.

## Package and validate

```sh
python3 native-aot/release/package.py build \
  --artifact /absolute/path/validated-artifact \
  --output /absolute/path/pi-bolt-m5-1.0.0-m5.1
python3 native-aot/release/package.py verify /absolute/path/pi-bolt-m5-1.0.0-m5.1
python3 native-aot/release/acceptance.py \
  --package /absolute/relocated/path/pi-bolt-m5-1.0.0-m5.1 \
  --output /absolute/fresh/evidence/acceptance.json \
  --deny-source-root /absolute/source/checkout \
  --tool-cache /absolute/path/containing/fd-and-rg
python3 native-aot/release/audit.py \
  --package /absolute/relocated/path/pi-bolt-m5-1.0.0-m5.1 \
  --receipt /absolute/fresh/evidence/acceptance.json \
  --output /absolute/fresh/evidence/audit.json
python3 native-aot/release/record-acceptance.py \
  --package /absolute/relocated/path/pi-bolt-m5-1.0.0-m5.1 \
  --receipt /absolute/fresh/evidence/acceptance.json \
  --audit /absolute/fresh/evidence/audit.json
python3 native-aot/release/package.py archive /absolute/relocated/path/pi-bolt-m5-1.0.0-m5.1 \
  --output /absolute/fresh/pi-bolt-m5-1.0.0-m5.1.tar.gz
```

Use `--deny-source-root /absolute/source/checkout` for acceptance after copying the package and evidence outside that checkout. The runtime alone is denied source reads; unchanged harnesses continue to run outside the sandbox. This caught and fixed Photon's embedded absolute Wasm path. The build now binds Photon to the shipped adjacent Wasm, with an exact upstream source guard. Image-tool completion also awaits worker termination, fixing a shutdown race found by the unchanged worker-exit gate.

[Acceptance coverage](../native-aot/release/ACCEPTANCE.md) covers both public launchers, both terminal layouts, RPC/session restoration, extensions, worker recovery, image resizing, native helper loading, cancellation and real interactive PTY workflows. It is scoped coverage, not every provider or third-party extension. [Release checks](../native-aot/release/README.md) record the remaining production gates.

[Installed-candidate benchmarks](M5-PACKAGED-BENCHMARKS.md) measure both public policies against stable Bun bytecode/JIT and published Pi on Node. The measured candidate improves CPU and physical footprint; wall-time superiority to stable Bun is not established. [Historical measurements](HISTORICAL-M5-BENCHMARKS.md) describe the earlier direct-executable artifacts separately.

## GitHub

Run `python3 native-aot/release/source-export.py --output /absolute/fresh/github-source` to create a clean source tree without npm trees, engine checkouts, executables, caches, personal settings or research logs. Initialize your repository from that export. Source CI runs packaging/Photon guard tests. Native validation is a manual workflow on a trusted self-hosted M5/macOS27 runner; it never runs pull-request code on that runner or publishes automatically.

Original Pi-Bolt packaging/integration code is **Apache-2.0** under `LICENSES/Apache-2.0.txt`; upstream Pi retains its root MIT license. Upstream Bun, JavaScriptCore/WebKit, Pi, Wasm and native dependencies retain their original licenses. Review [NOTICE](../NOTICE), preserved notices and source/relink requirements before publishing binary assets. GitHub prerelease publication does not promote this candidate to production.
