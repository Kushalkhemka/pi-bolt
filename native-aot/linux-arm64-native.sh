#!/bin/sh
# Run on the native Linux ARM64 host after installing the pinned Bun toolchain.
set -eu
case "$(uname -s):$(uname -m)" in
  Linux:aarch64|Linux:arm64) ;;
  *) echo 'Run this build and validation on native Linux ARM64.' >&2; exit 1 ;;
esac
if [ "$#" -lt 2 ]; then
  echo 'Usage: linux-arm64-native.sh /absolute/pi-package /absolute/bytecode-order [bun-executable] [jobs] [fresh-artifact-dir]' >&2
  exit 1
fi
task_pi_package=$1
task_order=$2
task_bun=${3:-bun}
task_jobs=${4:-2}
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
export PI_BENCH_PACKAGE_DIR="$task_pi_package"
export PI_BENCH_BUN="$(command -v "$task_bun")"
export PI_NATIVE_ARTIFACT_DIR=${5:-"$PWD/native-aot/artifacts/linux-arm64"}
python3 native-aot/linux-readiness.py --arch arm64 --pi-package "$task_pi_package" --bun "$task_bun" \
  --native-address-preflight --require-build --output "$PWD/native-aot/research/linux-arm64-preflight.json"
python3 native-aot/lab.py bootstrap
python3 native-aot/lab.py build-bun --bun "$task_bun" --jobs "$task_jobs"
task_runtime="$PWD/native-aot/sources/bun/build/release-local/bun"
# Repeat after bootstrap/build, validating the actual source ABI before invoking
# the custom runtime. The child releases its mapping; this is an availability
# check, not a reservation carried into Bun.
python3 native-aot/linux-readiness.py --arch arm64 --pi-package "$task_pi_package" --bun "$task_bun" \
  --native-address-preflight --require-build --output "$PWD/native-aot/research/linux-arm64-native-address.json"
python3 native-aot/lab.py verify-jsc
python3 native-aot/check-linked.py --runtime "$task_runtime" \
  --output "$PI_NATIVE_ARTIFACT_DIR-linked-check"
python3 native-aot/check-native-tiering.py --runtime "$task_runtime" \
  --output "$PI_NATIVE_ARTIFACT_DIR-tiering-check"
python3 native-aot/pi.py build --pi-package "$task_pi_package" --pipeline linked \
  --bytecode-order "$task_order" --priority-final-render
python3 native-aot/pi.py verify
python3 native-aot/pi.py validate
python3 native-aot/functional.py --modes native hybrid jit interpreter
python3 native-aot/linux-readiness.py --arch arm64 --pi-package "$task_pi_package" --bun "$task_bun" \
  --native-address-preflight --artifact "$PI_NATIVE_ARTIFACT_DIR" --validate --output "$PI_NATIVE_ARTIFACT_DIR/linux-host-validation.json"
python3 benchmarks/interactive_workload.py --variants-file "$PI_NATIVE_ARTIFACT_DIR/variants.json" \
  --variants pi_native pi_hybrid pi_jit pi_interpreter --turns 3 --pace-ms 2 --functional --runs 1 --warmups 0 \
  --output "$PI_NATIVE_ARTIFACT_DIR/linux-pty-functional.json"
echo 'Native ARM64 correctness checks complete. Performance cohorts are a separate step on an idle native host.'
