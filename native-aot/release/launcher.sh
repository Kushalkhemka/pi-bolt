#!/bin/bash
# Pi-Bolt M5: resolve the installed launcher, including its public symlink.
set -eu
launch_file="${BASH_SOURCE[0]}"
while [ -L "$launch_file" ]; do
    launch_dir="$(cd -P -- "${launch_file%/*}" && pwd)"
    launch_file="$(/usr/bin/readlink "$launch_file")"
    case "$launch_file" in /*) ;; *) launch_file="$launch_dir/$launch_file" ;; esac
done
package_dir="$(cd -P -- "${launch_file%/*}/.." && pwd)"
# Query platform once rather than starting uname/sw_vers/sysctl separately.
if ! host_info="$(/usr/sbin/sysctl -n hw.machine machdep.cpu.brand_string kern.osproductversion 2>/dev/null)"; then
    echo 'Pi-Bolt requires an Apple M5 Mac.' >&2; exit 1
fi
host_arch="${host_info%%$'\n'*}"; host_info="${host_info#*$'\n'}"
host_cpu="${host_info%%$'\n'*}"; host_os="${host_info#*$'\n'}"
[ "$host_arch" = arm64 ] || { echo 'Pi-Bolt requires native Apple ARM64.' >&2; exit 1; }
case "$host_cpu" in
    'Apple M5'|'Apple M5 Pro'|'Apple M5 Max'|'Apple M5 Ultra') ;;
    *) echo 'This release targets Apple M5. Other chips need a separately validated build.' >&2; exit 1 ;;
esac
os_major="${host_os%%.*}"
case "$os_major" in ''|*[!0-9]*) echo 'Cannot determine macOS version.' >&2; exit 1 ;; esac
if [ "$os_major" -lt 27 ]; then echo 'Pi-Bolt requires macOS 27 or later.' >&2; exit 1; fi
if [ ! -x "$package_dir/pi-native" ] || [ ! -f "$package_dir/pi-native.aot" ]; then
    echo 'Incomplete Pi-Bolt installation. Reinstall the complete release package.' >&2; exit 1
fi
# Reject accidental pair truncation without hashing 132MB on every launch.
pair_sizes="$(/usr/bin/stat -f %z "$package_dir/pi-native" "$package_dir/pi-native.aot")"
if [ "${pair_sizes%%$'\n'*}" != '@EXECUTABLE_BYTES@' ] ||
   [ "${pair_sizes#*$'\n'}" != '@IMAGE_BYTES@' ]; then
    echo 'Pi-Bolt runtime/image size mismatch. Run pi-bolt-doctor and reinstall.' >&2; exit 1
fi
for runtime_key in "${!JSC_@}" "${!BUN_JSC_@}" "${!PI_NATIVE_AOT_@}"; do unset "$runtime_key"; done
export BUN_JSC_resolveAllScopeSlotsStatically=true
export BUN_JSC_evaluateObjectLiteralValuesFirst=true
export BUN_JSC_definePlainInstanceFieldsInConstructor=true
export BUN_JSC_useJIT=true BUN_JSC_useAOT=true BUN_JSC_numberOfGCMarkers=2
export BUN_JSC_useAOTMappedImages=true
export BUN_JSC_aotImagePath="$package_dir/pi-native.aot"
export PI_PACKAGE_DIR="$package_dir"
@TIERING@
exec "$package_dir/pi-native" "$@"
