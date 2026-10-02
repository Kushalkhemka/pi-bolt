# Installed M5 candidate benchmarks

The exact unsigned Pi 1.0.0 candidate was installed from the final archive and measured through its public symlinks. Apple M5 base, macOS 27, 16 GB. Node 26.10.0 uses the published Pi npm bundle; stable Bun 1.4.2 uses the optimized bytecode/JIT pipeline with two GC workers. The custom runtime is based on Bun 1.4.3 canary. These are application pipeline comparisons, not an isolated compiler experiment.

64 measured sessions plus 16 discarded warmups: eight randomized paired rounds per 20-turn and 100-turn fullscreen cohort. Each turn streams Markdown/code/Unicode in 32 flushed SSE chunks and executes a real read tool against a deterministic loopback provider. Completion is the final visible idle editor frame; later agent-settled work is excluded. No outlier removal or historical pooling.

| Turns | Runtime | Wall ms | Pi-process CPU ms | Peak physical footprint MB |
|---:|---|---:|---:|---:|
| 20 | Pi-Bolt default | 964.9 | 638.9 | 61.0 |
| 20 | Pi-Bolt tier10000 | 944.6 | 740.3 | 88.9 |
| 20 | Stable Bun bytecode + JIT (GC2) | 954.2 | 1466.1 | 156.6 |
| 20 | Published Pi on Node | 1802.1 | 1665.6 | 147.9 |
| 100 | Pi-Bolt default | 1384.4 | 726.7 | 91.3 |
| 100 | Pi-Bolt tier10000 | 1295.5 | 786.9 | 122.2 |
| 100 | Stable Bun bytecode + JIT (GC2) | 1364.7 | 1166.0 | 193.7 |
| 100 | Published Pi on Node | 2301.5 | 1422.7 | 288.1 |

Default Pi-Bolt reduced measured CPU by 56.4%/37.7% and peak physical footprint by 61.0%/52.9% versus the stable GC2 Bun control in the 20/100-turn cohorts. The corresponding paired 95% bootstrap intervals exclude zero. Pi-process CPU excludes helper subprocess CPU, including launcher platform/stat queries; launch wall includes their overhead.

Wall superiority to stable Bun is not established. The default medians were 1.1%/1.4% slower, with wide intervals spanning zero. Tier10000 was 1.0%/5.1% faster by median, but its intervals also span zero. At 100 turns, tier10000 improved wall versus the default by 6.4% (95% interval 1.8–12.1%) while adding 8.3% measured CPU and 33.9% peak footprint. Keep the default for CPU/memory and expose tiering as an explicit sustained-session option.

Both candidates beat the published Node control for wall, CPU and footprint within these cohorts. All 80 sessions passed the unchanged workload checks: 9,600 provider requests, 4,800 read tools and 4,800 visible idle frames. All 17,535 pinned input files were unchanged, including 2,447 installed package entries. The compact JSON includes all paired intervals, exact pair hashes and independent audit hashes.

The 20-turn cohort was materially slower for every policy than the earlier direct-executable study. The later 100-turn cohort returned near the earlier scale; a post-timing host snapshot showed load averages 5.08/11.64/23.49 on 10 CPUs. The post-timing snapshot does not establish the cause of the earlier slowdown. No concrete configuration or harness defect was found. This is a shared interactive machine, not a controlled idle performance lab. The retained results are not pooled with historical samples, and host activity limits interpretation.

The stable control predates the two Photon/image-worker portability repairs; text-read timing does not exercise those paths. Published Node uses its shipping bundle rather than the custom final-render transforms. Filesystem and Node private compile cache were warm. Remote-provider latency, image/codemode performance and production soak were not timed. Functional acceptance covers both terminal layouts separately; this performance cohort covers fullscreen only.

Archive SHA256: `78bf22e8007f65be7f71098f81bdc67c811d66faa032150e20bcb843ca0dbac9`. Executable/image identity and audit are in [compact benchmark data](M5-PACKAGED-BENCHMARKS.json). Full local raw evidence is retained under `native-aot/research/m5-packaged-launcher-benchmark-v1`.
