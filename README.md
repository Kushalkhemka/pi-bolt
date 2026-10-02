<p align="center">
  <img src="assets/pi-bolt.svg" alt="Pi-Bolt logo" width="128" height="128">
</p>
<p align="center">
  <a href="https://github.com/Kushalkhemka/pi-bolt/releases/tag/pi-bolt-v1.0.0-m5.1"><img alt="Pi 1.0 runtime" src="https://img.shields.io/badge/Pi-1.0.0-111827?style=flat-square"></a>
  <img alt="Apple M5" src="https://img.shields.io/badge/Apple-M5-111827?style=flat-square">
  <img alt="Unsigned prerelease" src="https://img.shields.io/badge/release-unsigned_candidate-fbbf24?style=flat-square">
  <a href="https://github.com/Kushalkhemka/pi-bolt/actions/workflows/source-ci.yml"><img alt="Source checks" src="https://github.com/Kushalkhemka/pi-bolt/actions/workflows/source-ci.yml/badge.svg"></a>
</p>

# Pi-Bolt

**Pi 1.0, powered by a native AOT + JavaScriptCore hybrid runtime.**

Pi-Bolt brings build-time machine code to [Pi](https://github.com/earendil-works/pi), the extensible coding agent. Covered functions start in native code; dynamic JavaScript retains JSC's interpreter and JIT. The complete package runs without installing Node, Bun or npm.

**Current release:** unsigned M5 candidate. Requires **Apple M5 and macOS 27+**; tested on the base M5. M4, earlier macOS and Linux are outside this package's support contract. Signing, notarization and broader production validation remain pending.

## Getting started

Download and verify the pinned release, then install it into your user directory:

```bash
mkdir -p pi-bolt-download && cd pi-bolt-download
release="https://github.com/Kushalkhemka/pi-bolt/releases/download/pi-bolt-v1.0.0-m5.1"
curl -fLO "$release/pi-bolt-m5-1.0.0-m5.1.tar.gz"
curl -fLO "$release/install.sh"
curl -fLO "$release/SHA256SUMS"
shasum -a 256 --check SHA256SUMS --ignore-missing

bash install.sh \
  --archive "$PWD/pi-bolt-m5-1.0.0-m5.1.tar.gz" \
  --sha256 78bf22e8007f65be7f71098f81bdc67c811d66faa032150e20bcb843ca0dbac9 \
  --allow-unsigned
```

Start it in your project directory:

```bash
cd /path/to/project
"$HOME/.local/pi-bolt/bin/pi-bolt"
```

Run `/login` inside Pi to connect a provider. Your normal Pi settings, sessions, extensions, skills and themes remain available. `pi-bolt` uses a separate command name so it can coexist with your existing `pi` installation.

An unsigned download may be blocked by Gatekeeper. The installer does not remove quarantine or change macOS security settings. If blocked, [build from source](docs/BUILD.md) or wait for a signed release. See [install, update and rollback](docs/INSTALL.md).

## Runtime options

```bash
# Default: lowest measured Pi-process CPU and physical footprint.
"$HOME/.local/pi-bolt/bin/pi-bolt"

# Optional: native functions can tier into JIT after 10,000 calls.
"$HOME/.local/pi-bolt/bin/pi-bolt-tier10000"

# Regular terminal layout; print and RPC modes use Pi's usual flags.
"$HOME/.local/pi-bolt/bin/pi-bolt" --tui-mode regular
"$HOME/.local/pi-bolt/bin/pi-bolt" --help
```

The tiering policy improved sustained-session wall time against the default in the measured cohort, with higher CPU and memory. [How the hybrid works](docs/RUNTIME.md).

## Measured performance

Installed public commands, **100 interactive turns**, median of eight paired randomized runs on one M5. Deterministic loopback streaming provider and real read tools:

| Runtime | Wall time | Pi-process CPU | Peak physical footprint |
|---|---:|---:|---:|
| **Pi-Bolt default** | 1,384 ms | **727 ms** | **91 MB** |
| Pi-Bolt tier10000 | **1,296 ms** | 787 ms | 122 MB |
| Stable Bun bytecode + JIT, GC2 | 1,365 ms | 1,166 ms | 194 MB |
| Published Pi on Node | 2,302 ms | 1,423 ms | 288 MB |

The default reduced measured CPU by **37.7%** and peak footprint by **52.9%** against the stable Bun control. A wall-time win over stable Bun was **not established**: confidence intervals cross zero. CPU excludes helper subprocesses. These are Pi workload results, not a universal runtime ranking. [Method, all cohorts and confidence intervals](docs/M5-PACKAGED-BENCHMARKS.md).

## Verification

Both runtime policies and terminal layouts passed **120 bounded CLI assertions**, including RPC, session restoration, extensions, streamed tool turns, cancellation, image resizing and worker cleanup. Packaging and installation passed **32 tests** plus two source regression tests. The installed benchmark passed 64 measured sessions and 16 warmups.

[Public validation evidence](docs/VALIDATION.md) · [Candidate verification](docs/M5-UNSIGNED-CANDIDATE.md) · [Release process](native-aot/release/README.md)

## Source and packages

This is a public fork of Pi, with its source and history preserved. The shipped runtime is pinned to **Pi 1.0.0** through its isolated [npm lock](native-aot/pi-1.0-runtime/package-lock.json).

| Component | Location |
|---|---|
| Pi coding agent and libraries | [Upstream packages](UPSTREAM.md#packages) |
| Native runtime, guarded transforms and engine patches | [native-aot](native-aot) |
| Build instructions | [Build guide](docs/BUILD.md) |
| Source and relinking companion | [Release assets](https://github.com/Kushalkhemka/pi-bolt/releases/tag/pi-bolt-v1.0.0-m5.1) |

Pi's SDK, provider, extension and package documentation is available in [packages/coding-agent/docs](packages/coding-agent/docs). Runtime archives are distributed through GitHub Releases; this project does not republish upstream npm packages under the `@earendil-works` namespace.

## License

Upstream Pi retains its [MIT license](LICENSE). Original Pi-Bolt runtime integration, packaging and logo are [Apache-2.0](LICENSES/Apache-2.0.txt). Bun, JavaScriptCore/WebKit and bundled dependencies retain their original licenses and notices. See [NOTICE](NOTICE) and the [source/relink delivery notes](native-aot/release/license-review.md).
