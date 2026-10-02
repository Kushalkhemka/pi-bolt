#!/bin/bash
set -eu
launch_file="${BASH_SOURCE[0]}"
while [ -L "$launch_file" ]; do
    launch_dir="$(cd -P -- "$(dirname -- "$launch_file")" && pwd)"
    launch_file="$(/usr/bin/readlink "$launch_file")"
    case "$launch_file" in /*) ;; *) launch_file="$launch_dir/$launch_file" ;; esac
done
package_dir="$(cd -P -- "$(dirname -- "$launch_file")/.." && pwd)"
(cd "$package_dir" && /usr/bin/shasum -a 256 -c SHA256SUMS)
/usr/bin/codesign --verify --strict "$package_dir/pi-native"
"$package_dir/bin/pi" --version
printf 'Package checksums, signature structure and Pi version passed.\n'
printf 'Gatekeeper status (an unsigned candidate is expected to be rejected):\n'
/usr/sbin/spctl --assess --type execute --verbose=2 "$package_dir/pi-native"
