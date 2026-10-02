# Checked original-source subset

This frontend supports explicitly pinned original TypeScript for Pi v0.85.1 (`d981de1229ef899957bbe968bc8dcda02a21f477`) and Pi v1.0.0 (`a13d35a742c6ef8462812a28fbe1d8c8b7431c32`). The version registry selects the source commit, package roots, executable entrypoints and audited TTY boundary hash; unknown versions or mismatched packages fail. Pi 1.0.0 adds chord, codemode, MCP and telemetry roots and its standalone codemode worker entry. It adds runtime guards for explicit primitive annotations and primitive unions. It does not implement a complete TypeScript type checker, structural contracts, imported declaration-file contracts, or Jarred's complete experimental compiler frontend.

Checked types remain opt-in. Pi 1.0 now passes the defined CLI/session and compatible worker gates. A separate 72-session paired screen found no established CPU or wall-time benefit for checked hybrid over erased hybrid; regular checked tiering had a small wall regression. Generic non-tiering hybrid GC2 remains the CPU-priority recommendation. See [the Pi 1.0 report](../PI-1.0-2026-10-02.md) for exact results, intervals and scope. Earlier Pi 0.85.1 results remain separate historical cohorts.

The pinned WebKit branch recognizes `$$t(value, mask)` with `useSoundTypes=true` and emits `op_check_type`. No JavaScript fallback for `$$t` is included. The intrinsic name is reserved: the frontend rejects original identifiers with that name, and the plugin checks ordinary JavaScript dependencies it loads for collisions. The native backend fixture must demonstrate guard behavior after native execution and promotion to DFG before results are accepted.

The bridge uses binder symbol identity for annotated locals and parameters, including captures and shadowing. It guards reads, supported writes, callee entry, explicit and implicit returns, and supported type assertions. Unsupported types and mutation targets remain generic and are recorded in the receipt. Numeric updates preserve prefix/postfix values; checked compound assignment evaluates its right side once and checks before assigning. Primitive contract failures throw rather than log.

For each Pi module, the plugin first compares normalized original-source erasure with the installed npm JavaScript. It uses original source only when they match. It keeps modules with different erasure or missing source generic, and records that limitation. The matching erased control uses the same source selection, transpilation, Unicode transform, final-render transform, bundler driver, and profile. This isolates the inserted checks within this pair; it does not make the original npm build identical to either control.

Preparation and reference semantics:

```sh
node native-aot/typed/check-frontend.mjs
node native-aot/typed/check-compatibility.mjs
node native-aot/typed/check-version-port.mjs
python3 -m py_compile native-aot/typed/check-backend.py native-aot/typed/build-artifact.py
```

After the native compiler is frozen, build and validate the backend fixture:

```sh
python3 native-aot/typed/check-backend.py --runtime /absolute/path/to/native-bun --output /absolute/path/to/fresh-fixture-directory
```

It runs JIT, interpreter, pure native, hybrid, and native-first hybrid with thresholds 32, 1,000 and 10,000. It checks callee contracts using `Reflect.apply`/`Reflect.construct` after warmup, so call-site assertions cannot satisfy the callee test. The final mode must select native functions, report promotion, and record a positive DFG compilation count before testing bad values again.

Build three fresh application controls:

```sh
python3 native-aot/typed/build-artifact.py --runtime /absolute/path/to/native-bun --output native-aot/artifacts/darwin-arm64-m5-checked-types-priority
python3 native-aot/typed/build-artifact.py --runtime /absolute/path/to/native-bun --erased-source-control --output native-aot/artifacts/darwin-arm64-m5-erased-types-priority
python3 native-aot/typed/build-artifact.py --runtime benchmarks/runtime/node_modules/.bin/bun --stable --erased-source-control --output native-aot/artifacts/darwin-arm64-m5-stable-erased-types-priority
```

The native builds use `useSoundTypes=true`, throwing guards, linked sidecar export, two export workers, and the existing common research compiler settings. The stable build requires Bun 1.4.2 and receives no native export, sound-type, or unsupported research compiler flags. Stable 1.4.2 rejects all three `COMMON` options; the cross-engine comparison therefore includes these compiler setting differences and records that limitation. Each manifest records the driver, frontend, plugin, compiler, profile, selected module inputs, inserted-check count, and before/after input checks. Assets remain symlinks to the ordinary Pi package.

The defaults retain Pi 0.85.1. For Pi 1.0.0, pass `--pi-package native-aot/pi-1.0-runtime/node_modules/@earendil-works/pi-coding-agent` and `--source-root native-aot/sources/pi-v1.0.0` to all three builds, with a freshly trained `--bytecode-order` and fresh artifact directories. The driver shares ordinary-build worker inputs, explicit flat entry naming, exact published-input worker specifier guards, and native terminal assets. It records and requires both Pi 1.0 worker substitutions after the source erasure/checked transform. Preparation checks exercise representative package modules and the extra worker without compiling artifacts; use `check-version-port.mjs --all-modules` to audit every installed module in the supported package roots. Neither check proves the complete CLI or codemode worker workflow.

Ambient declarations are not executable local bindings and remain generic. A separate audited compatibility rule corrects only Pi's `resolveAppMode` TTY host parameters from `boolean` to `boolean | undefined`: Node's pipe properties are undefined, and the existing implementation handles them through truthiness after RPC/JSON early returns. The rule requires the exact pinned original-source hash and function signature/body; it preserves a guard that rejects strings/null, makes no runtime coercion, and records both the original and accepted contracts in the receipt. Original source erasure is unchanged. Earlier artifacts built before ambient/TTY corrections failed functional validation and must not be timed.

Native artifacts still require `pi.py verify` and `pi.py validate` with `PI_NATIVE_ARTIFACT_DIR` set to their fresh directory. All application artifacts require the separate interactive/CLI/provider/extension/worker checks before timing. These checks do not establish a universal performance win or production readiness across all Pi packages. Dynamic extensions using the reserved intrinsic name require separate review.
