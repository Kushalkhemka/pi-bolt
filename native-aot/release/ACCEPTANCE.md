# Relocated M5 release acceptance

`acceptance.py` consumes schema-1 `release.json`, checks every declared file's
SHA256, bytes and integer permission mode, and runs the existing correctness
harnesses unchanged. No builds, benchmarks, dependency installations or remote
model requests are performed. Package members and public launchers must be
internal regular files; package-root symlinks, traversal paths, undeclared files
and external asset links are rejected. `release.json` is recorded separately
because it cannot contain its own checksum. `SHA256SUMS` must exactly list the
declared files and `release.json`; its own digest, size and mode are retained
separately in the acceptance inventory.

Run only after the package is ready, on native Apple M5/macOS at or above the
manifest minimum. Copy the package into a fresh directory whose path contains
spaces, outside the build checkout. The evidence directory must be outside the
package. Provide already available executable `fd` and `rg` files for offline
tool setup; no user agent settings or credentials are reused.

```sh
python3 native-aot/release/acceptance.py \
  --package '/absolute/path/relocated Pi release' \
  --tool-cache /absolute/path/offline-tools \
  --output /absolute/path/evidence/acceptance.json
```

The default tests both `bin/pi` (non-tiering hybrid) and `bin/pi-tier10000` in
fullscreen and regular layouts. `--modes hybrid` can prepare a narrower
diagnostic run, but cannot substitute for the full two-mode release gate.
`--timeout-seconds` defaults to 600 per suite and is bounded to 60–1800. An
interruption or timeout terminates the suite's process group and retains its
original logs, diagnostics and partial output with `complete:false`. Receipts
and sibling `<receipt-stem>-evidence` directories are never reused.

The default repeated assertions are:

| Suite | Scope | Assertions |
| --- | --- | ---: |
| RPC | Two policies, 15 original gates each | 30 |
| Commands | Two policies and two layouts, 13 gates each | 52 |
| Workers | Two policies, seven Darwin gates each | 14 |
| Full PTY | Two policies and two layouts, six gates each | 24 |
| Total | Original bounded correctness suites | 120 |

RPC covers invalid-command recovery, extension dialogs, session events,
compaction, provider/tool failure recovery, abort recovery and exact durable
failed-retry restoration. Commands load a temporary TypeScript extension,
reload it after a source change, test dialogs and settings/TUI roundtrips,
session naming/export/new-session behavior and successful recovery. Workers
prove real codemode read/write/store/recovery/cancellation, successful image
worker resizing and exact observed worker exits. The Darwin native helper is
loaded without reading/writing the clipboard. Full PTY checks include persisted
sessions/resume, actual alternate-screen mode and distinct cleared working
indicators after streamed read-tool turns. See the unchanged harness source and
retained results for the exact gate names.

The public launchers establish their runtime mode and package root. Generated
test configurations contain no JSC/AOT overrides. Each process receives isolated
temporary agent/session settings and `PI_OFFLINE=1`; model traffic is deterministic
loopback HTTP. Consumers, helper binaries and complete package inventories are
hashed before and after all suites. Resource/timing fields from these functional
checks are diagnostics and must not be presented as a performance benchmark.

## Installer and release-gate review checklist

The installer protocol is owned separately. Its tests should use temporary
install roots and a harmless existing-version sentinel, without changing user
PATH, globals or credentials. Before publication, cover these independent cases:

- A valid archive installs into a versioned directory and switches the public
  symlink only after verification and the required acceptance gate.
- Archive SHA mismatch, malformed manifest, missing/extra file, file SHA mismatch,
  wrong executable mode or wrong target is rejected before executing package code.
- Archive extraction rejects absolute/traversing paths, escaping links and
  unsupported special members; temporary sentinels outside the staging root
  remain unchanged. Define accepted archive root/layout before extracting.
- Paths containing spaces work. Existing release directories and evidence are
  not overwritten; concurrent installation attempts have explicit lock behavior.
- Failed extraction, verification, acceptance or interrupted activation leaves
  the old working release active. A rollback changes only the active version
  pointer and keeps both immutable version directories and failure evidence.
- Optional signatures/checksums are verified against the intended publisher or
  pinned digest. A self-consistent manifest alone is not authenticity proof.
- Signed/notarized distributions re-inventory the final signed bytes. Apple
  verification/notarization status must come from actual tool results, not an
  `unsigned-candidate` or ad-hoc signature label.

A GitHub release gate needs an actual supported M5/macOS runner for runtime
acceptance; an ARM cross-build or another Apple CPU is not M5 execution proof.
Pin the final package/archive digest, dependency/tool versions and frozen
correctness consumer digests. Install no Node or Bun at runtime acceptance:
the shipped executable must provide its own JavaScript runtime. Make `fd`/`rg`
available during job setup and record their identities. Give the acceptance job
read-only repository permissions, no provider tokens and no publication/signing
credentials. Keep signing/publishing in a separate gated job with the required
credentials and publish only the exact reviewed artifact digest.

For stronger relocation evidence, add `--deny-source-root /absolute/path/original-checkout`
with both package and evidence outside that checkout. This stores a macOS
`sandbox-exec` profile, checks that relocated metadata remains readable while
original source is denied, and applies the deny rule to the application and its
children only. Harnesses remain outside the sandbox. Sandbox/profile hashes and
read-probe exits are retained. Alternatively run in a clean supported host/account or make
the original checkout/build package unavailable during acceptance. A test from
the same checkout cannot prove that hidden absolute source/dependency fallbacks
are absent. In particular, the worker extension imports
`@earendil-works/pi-tui`; the released package must resolve this without relying
on the developer's installed npm tree.

These bounded checks do not establish every provider/OAuth/MCP/plugin, arbitrary
extension compatibility, clipboard side effects, production soak, Apple
notarization, other CPU/OS targets, universal performance or full maturity.

## Current verification status

The first relocated unsigned candidate was tested with reads from the original
checkout denied. It passed 30 RPC and 52 interactive command assertions. The
worker suite stopped when image resizing returned no inline image; full PTY
acceptance did not run. Package hashes remained unchanged. The failed receipt,
worker observations and logs are retained in
`native-aot/research/m5-portable-acceptance-v1`. This candidate has not passed
portable acceptance; a corrected fresh package needs all four suites again.

The Photon-fixed candidate passed those same 82 assertions and produced the
correct resized PNG in its real image worker. It then failed the original
worker-exit gate: the last image worker returned its result, but its exit event
was not observed before CLI shutdown. Full PTY remains pending. Both direct
runtime policies selected native code under source denial and accepted the
mapped image and prelinked graph; the executable/image hashes stayed unchanged.
Evidence is retained separately in `native-aot/research/m5-portable-acceptance-v2`.

RC2, with adjacent Photon WASM resolution and awaited image-worker cleanup,
passed all 120 original assertions under source denial: RPC 30, commands 52,
workers 14 and full PTY 24. Both launch policies passed; fullscreen and regular
CLI cases produced 12 visible idle frames, and all 20 watched workers exited.
All 2,447 package inventory entries and 15 frozen consumer/tool inputs matched
before and after. Separate direct diagnostics selected 2,245 native sites for
each policy, loaded the mapped image and accepted graph v4 with three modules.
The executable/image hashes matched the declared package pair. Receipts, logs,
release manifest, checksum table and independent audit are preserved in
`native-aot/research/m5-portable-acceptance-v3`; earlier failures remain intact.

This establishes bounded portability on this Apple M5/macOS 27 host. Installer
tamper/path/rollback checks, a second supported host, signing/notarization,
quarantine installation and provider soak remain separate release gates. No
performance gain is inferred from these correctness tests.
