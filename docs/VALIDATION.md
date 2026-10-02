# Pi-Bolt M5 unsigned candidate validation

Local checks on one Apple M5 running macOS 27.0, dated 2026-10-02. This is an unsigned candidate, with completed bounded validation; signing, notarization, a second host and provider soak remain pending. The detailed [sanitized evidence](VALIDATION.json) records hashes, scopes and paired confidence intervals.

## Checked artifact

- Pi 1.0.0, release `1.0.0-m5.1`, target `darwin-arm64`.
- Archive SHA-256: `78bf22e8007f65be7f71098f81bdc67c811d66faa032150e20bcb843ca0dbac9`.
- Executable SHA-256: `6b472c42e504f173699a8066d34964d7638b6ac5786d671cd1309b9ca2c681be`.
- AOT image SHA-256: `cb6a7c8d75604afe75a481fed37b978f66bc3e4b427743d06978baa86353dc85`.
- Compiler SHA-256: `8f212e04300c762ef712a515dfa9a5f478a5600cef5333038991cce2c4a073d5`.

## Correctness and source checks

Both v3 and fresh clean-export v4 acceptance passed the same 120 assertions: RPC 30, CLI commands 52, workers 14 and full PTY 24. Both launch policies and fullscreen/regular CLI layouts were covered. Each revision observed all 20 workers exiting and 12 final visible idle frames. Runtime and child reads from the original checkout were denied. Package and consumer hashes stayed unchanged. Native diagnostics selected 2,245 sites in v3 and 2,246 in v4, and loaded the mapped image and prelinked graph v4 with three modules. Counts are startup diagnostics, not overall native instruction coverage.

The final local source export passed 32 macOS unit tests, two JavaScript guards (Photon relocation and awaited worker cleanup), Python 3.14 compilation, Bash syntax, two workflow YAML checks and a fresh pinned npm install. The exact archive installed into a path with spaces and Unicode; both public launcher symlinks passed version and inherited-runtime-override checks. Digest mismatch and unsigned installation without explicit opt-in were rejected. Doctor verified inventory and observed the expected unsigned Gatekeeper rejection. These local checks do not attest actual GitHub CI execution.

## Installed public-launcher measurements

Fresh fullscreen cohorts retain 64 measured sessions and 16 discarded warmups across four policies, eight paired randomized measured rounds per workload. All 9,600 requests, 4,800 read tools and 4,800 visible idle frames passed, including warmups. An independent rehash verified all 17,535 pinned inputs unchanged. The original v3 visible-idle endpoint, 32 SSE deltas, built-in extensions and default tools were preserved; no external observer or source sandbox was enabled during timing.

Medians; wall and CPU in milliseconds, peak macOS physical footprint in decimal MB.

| Turns | Policy | Wall | Process CPU | Peak MB |
| --- | --- | ---: | ---: | ---: |
| 20 | Pi-Bolt default hybrid | 964.918 | 638.874 | 61.031 |
| 20 | Pi-Bolt tier 10,000 | 944.647 | 740.319 | 88.925 |
| 20 | Stable Bun bytecode + JIT, GC2 | 954.176 | 1466.113 | 156.648 |
| 20 | Published npm Node | 1802.143 | 1665.570 | 147.945 |
| 100 | Pi-Bolt default hybrid | 1384.377 | 726.680 | 91.284 |
| 100 | Pi-Bolt tier 10,000 | 1295.519 | 786.883 | 122.209 |
| 100 | Stable Bun bytecode + JIT, GC2 | 1364.678 | 1166.042 | 193.652 |
| 100 | Published npm Node | 2301.537 | 1422.711 | 288.123 |

Both package modes reduced CPU and footprint against stable Bun and published Node, and wall time against published Node, with positive 95% paired bootstrap intervals. **A wall-time win over stable Bun was not established:** every interval crossed zero. Default hybrid had the lowest CPU and footprint. At 100 turns, optional tiering reduced wall time versus the same package hybrid by 6.42% [1.79%, 12.07%], while increasing CPU and footprint. Intervals use 20,000 seeded paired-round resamples of the ratio of medians; workloads are kept separate.

The 20-turn phase was slower than historical cohorts for all policies, including unchanged controls. Both startup and turn times rose; the 100-turn phase returned near historical scale. The cause was not established. All samples were retained, with no outlier removal, replacement run or historical pooling.

These are complete application pipelines, not an isolated AOT experiment. Stable Bun 1.4.2 has shared final-render/Unicode transforms and profile but an older build driver; Node 26.10.0 uses its published npm bundle without those transforms. Its private compile cache was already warm, received two discarded warmups and remained unchanged in file count/bytes. Package worker/Photon fixes were outside the read-text timing path. Wall includes the public shell and two platform/stat child queries; process CPU excludes child, mock and harness CPU. Same-PID shell CPU can remain across exec. Remote model latency and codemode timing are excluded.

## Retained failures and limits

The first portability attempt failed Photon resizing under original-source read denial. A pinned adjacent-WASM transform corrected it. The next attempt produced the resized image but failed final worker-exit observation; awaited termination corrected it. Both original failures remain retained, and v3/v4 passed the unchanged gates. Reusable audit development also retained consumer/interpreter identity rejection and a failed sandboxed native diagnostic before fresh clean-export validation succeeded.

This evidence does not establish all providers, OAuth, MCP, arbitrary extensions, clipboard side effects, production soak, other CPU/OS targets or universal fastest performance. The candidate manifest still marks signed/hardened acceptance, notarization, quarantined installation, second M5, source/relink delivery and provider soak incomplete. The source/relink companion is a separate delivery gate. Raw local paths, credentials and private process inventories are omitted; hashes identify the retained private raw receipts.
