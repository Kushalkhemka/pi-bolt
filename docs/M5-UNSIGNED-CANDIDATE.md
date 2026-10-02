# M5 unsigned candidate verification

Pi-Bolt `1.0.0-m5.1` contains Pi 1.0.0, native mapped AOT code and JSC dynamic JIT support. The default hybrid targets measured CPU and physical footprint; the optional tier10000 command targets sustained sessions. It is an unsigned candidate for Apple M5/macOS 27+, tested on the base M5 with 16 GB. It has not been promoted to a signed production release.

## Deliverables

- `native-aot/dist/candidate/pi-bolt-m5-1.0.0-m5.1.tar.gz`: 58,038,252 bytes.
- Archive SHA256: `78bf22e8007f65be7f71098f81bdc67c811d66faa032150e20bcb843ca0dbac9`.
- The same directory contains the installer, management script and external checksum tables.
- `source-export.py` creates the GitHub source companion: original Apache-2.0 integration code, preserved upstream notices, pinned source/patches, npm lock, workload profile, tests and manual M5 workflow. It omits engine/npm trees, binary assets, personal settings and private research logs.

The archive includes ten runtime asset groups and 2,445 declared files plus release metadata/checksums. It has no external symlinks. Users need no Node, Bun, npm, Python or sudo to run it. [Installation and rollback](../README.md) describe the explicit unsigned opt-in and trusted external SHA requirement. macOS can reject quarantined unsigned downloads; the installer does not remove quarantine or change security settings.

## Repairs and verification

Relocated testing with original source reads denied found two actual application portability bugs. Photon attempted to load Wasm through a baked build-machine path, and CLI shutdown could precede an image worker's exit event. Guarded transforms now load the shipped adjacent Wasm and await worker termination. These repairs preserve the original npm installation and are regression-tested on its exact pinned source.

The corrected package passed 120 original repeated assertions: 30 RPC, 52 interactive commands, 14 worker checks and 24 full PTY checks, covering both public policies and both terminal layouts. All 20 watched workers exited. The image worker produced the required resized PNG; codemode read/write/store/recovery/cancellation and the Darwin native helper passed. PTY tests exercised streamed tool turns, typing, resizing, cancellation/recovery and persisted session restoration. All 2,447 package entries and frozen correctness inputs remained unchanged. These are bounded assertions, not a claim that every provider/extension was tested.

Separate native diagnostics proved mapped machine-code selection and graph v4 with three modules for both policies. Reusable independent auditing and saved-proof replay passed. Diagnostic site counts can differ by startup path; counters are derived from the retained logs rather than used as a fixed performance threshold.

The final maintainer tooling was also exported to a separate clean checkout outside the denied original workspace. That checkout reran all 120 assertions and the independent native audit successfully. The deny probe is bound to the recorded original build-driver hash, rather than assuming the maintainer checkout is the build workspace. Metadata recording and full verification passed on a fresh independent package copy; original runtime assets and the final archive stayed unchanged. This companion evidence is retained in `native-aot/research/m5-portable-acceptance-v4`.

The final exact archive installed into a fresh temporary prefix with spaces and Unicode. Both public symlinks returned Pi 1.0.0 and cleared inherited engine overrides. Bad external SHA pins and default unsigned installation were rejected. All installed bytes matched the candidate. Doctor passed file integrity, ad-hoc code-signature structure and version, then reported the expected Gatekeeper rejection (exit 3).

All 32 packaging/installer unit and integration tests passed on macOS. Both source-guard regression tests passed: adjacent Photon Wasm with inaccessible original paths, and successful/failed image results waiting for worker termination. Python compilation, Bash syntax and both workflow YAML files passed local checks. The exported source also passed Python 3.14 compilation, all 32 local macOS tests, both JavaScript regression tests using a fresh lockfile installation, Bash syntax and YAML parsing. An initial compile attempt selected unsupported Python 3.11 from the export directory; the documented Python 3.12+ requirement and explicit native-workflow version check cover that case. Actual GitHub-hosted/self-hosted workflow execution remains pending.

Initial Photon and worker-shutdown failures remain in separate v1/v2 evidence directories. Independent audit implementation diagnostics also remain preserved. The final runtime/archive was not overwritten during subsequent source-tool validation.

## Installed performance

A fresh independent [installed-launcher benchmark](M5-PACKAGED-BENCHMARKS.md) passed 64 measured sessions and 16 warmups, with 17,535 pinned inputs unchanged. Both candidates improved measured Pi-process CPU and physical footprint against stable bytecode/JIT Bun and published Pi on Node. Wall-time superiority over stable Bun was not established. The shared-machine short-session slowdown is retained and unexplained. CPU excludes helper subprocesses; wall includes launcher overhead. No universal-fastest claim follows from these cohorts.

## Remaining publication gates

Release metadata marks only portable acceptance complete. Developer ID/Hardened Runtime acceptance, notarization, quarantined-download installation, another supported M5 host, linked-license/source/relink delivery and real-provider/concurrency/long-session soak remain pending. Minimal entitlements and compatibility with arbitrary third-party packages are unproven. The fixed virtual address reservation and engine fallback on incompatible images remain runtime limits; per-launch size checks do not detect same-size corruption, while installer/doctor verify full hashes.

Apache-2.0 covers original Pi-Bolt integration code. Upstream licenses remain separate. The conservative notice inventory is not a completed legal/relink review. Review [the source and relink plan](../native-aot/release/license-review.md) before publishing binary assets. This report records local validation before public prerelease publication. The checks made no global installation or provider credential changes; consult the GitHub release history for publication records.
