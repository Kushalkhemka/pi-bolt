# Historical M5 comparison

This precursor Pi1.0 hybrid build was measured on 2 October 2026. It predates the standalone Photon relocation, awaited worker cleanup and installed platform/size checks. Do not substitute these timings for a packaged-launcher cohort. All results are end-to-end pipelines with the recorded version, bundling, renderer and cache differences.

224 measured sessions +56 warmups, eight paired randomized rounds per policy/cohort, no outlier removal. Real PTY/local SSE/read-tool sessions; remote model latency is excluded. Wall includes startup to final visible idle; CPU is Pi-process all-thread user+system; sampled macOS physical footprint is decimal MB.

## fullscreen / 20 burst turns

| Pipeline | Wall ms | CPU ms | Peak MB |
| --- | ---: | ---: | ---: |
| Our default hybrid | 211.3 | 178.2 | 69.2 |
| Our tier10,000 | 208.0 | 240.2 | 110.5 |
| Optimized stable Bun1.4.2 bytecode/JIT | 275.8 | 478.1 | 193.3 |
| Published Pi on Node26.10.0 | 689.5 | 584.1 | 150.6 |

## fullscreen / 100 burst turns

| Pipeline | Wall ms | CPU ms | Peak MB |
| --- | ---: | ---: | ---: |
| Our default hybrid | 1434.8 | 743.6 | 94.7 |
| Our tier10,000 | 1274.5 | 787.8 | 123.3 |
| Optimized stable Bun1.4.2 bytecode/JIT | 1344.2 | 1140.4 | 192.7 |
| Published Pi on Node26.10.0 | 2310.6 | 1438.8 | 290.6 |

## regular / 20 burst turns

| Pipeline | Wall ms | CPU ms | Peak MB |
| --- | ---: | ---: | ---: |
| Our default hybrid | 247.0 | 183.3 | 69.7 |
| Our tier10,000 | 249.1 | 252.4 | 120.2 |
| Optimized stable Bun1.4.2 bytecode/JIT | 270.6 | 438.1 | 192.0 |
| Published Pi on Node26.10.0 | 717.6 | 552.9 | 155.2 |

## regular / 100 burst turns

| Pipeline | Wall ms | CPU ms | Peak MB |
| --- | ---: | ---: | ---: |
| Our default hybrid | 2100.8 | 953.8 | 109.4 |
| Our tier10,000 | 1990.1 | 987.0 | 123.8 |
| Optimized stable Bun1.4.2 bytecode/JIT | 1991.0 | 1306.9 | 191.2 |
| Published Pi on Node26.10.0 | 3276.5 | 1513.3 | 292.5 |

Default hybrid had the lowest median CPU and peak footprint in all four historical cohorts. Long-session wall intervals against optimized stable Bun overlap zero for both hybrid modes. This is not a universal fastest-runtime claim.

[Compact full-policy medians, intervals, raw hashes and limitations](HISTORICAL-M5-BENCHMARKS.json). Full original receipts remain in the private research workspace; the summary is a derived export, not a complete reproduction of the frozen source inventory.
