#!/bin/bash
# Install a local, externally checksum-pinned release. No sudo or curl|sh.
set -eu
valid_version() { [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+-m5\.[0-9]+$ ]]; }
validate_pointer() {
    local target
    [ -L "$prefix/$1" ] || { echo 'Unmanaged installation pointer.' >&2; return 1; }
    target="$(/usr/bin/readlink "$prefix/$1")"
    [[ "$target" =~ ^packages/pi-bolt-m5-[0-9]+\.[0-9]+\.[0-9]+-m5\.[0-9]+$ ]] || { echo 'Unmanaged installation pointer.' >&2; return 1; }
    [ ! -L "$prefix/$target" ] && [ -d "$prefix/$target" ] || { echo 'Installation pointer must refer to a real installed package directory.' >&2; return 1; }
}
validate_command() {
    local name="$1" target="$2"
    if [ -e "$prefix/bin/$name" ] || [ -L "$prefix/bin/$name" ]; then
        [ -L "$prefix/bin/$name" ] && [ "$(/usr/bin/readlink "$prefix/bin/$name")" = "../current/bin/$target" ] || {
            echo "Refusing unmanaged command: $name." >&2; return 1;
        }
    fi
}
validate_notarized() {
    local field team details
    for field in signing.developer_id signing.notarized production_gates.portable_acceptance production_gates.signed_hardened_acceptance production_gates.notarization production_gates.quarantined_install production_gates.second_m5 production_gates.source_relink_delivery production_gates.provider_soak; do
        [ "$(/usr/bin/plutil -extract "$field" raw -o - "$package/release.json")" = true ] || { echo 'Notarized release gates are incomplete.' >&2; return 1; }
    done
    team="$(/usr/bin/plutil -extract signing.team_id raw -o - "$package/release.json")"
    [[ "$team" =~ ^[A-Z0-9]{10}$ ]] || { echo 'A pinned Developer ID team is required.' >&2; return 1; }
    /usr/bin/codesign --verify --strict "$package/pi-native"
    details="$(/usr/bin/codesign -dv --verbose=4 "$package/pi-native" 2>&1)"
    case "$details" in *"Authority=Developer ID Application:"*) ;; *) echo 'Developer ID Application signature required.' >&2; return 1 ;; esac
    printf '%s\n' "$details" | /usr/bin/grep -qx "TeamIdentifier=$team" || return 1
    printf '%s\n' "$details" | /usr/bin/grep -Eq '^CodeDirectory .*flags=.*[(,]runtime[),]' || { echo 'Hardened Runtime signature required.' >&2; return 1; }
    printf '%s\n' "$details" | /usr/bin/grep -q '^Timestamp=' || { echo 'Secure signing timestamp required.' >&2; return 1; }
    /usr/sbin/spctl --assess --type execute "$package/pi-native"
}
archive='' expected_sha='' prefix="${HOME}/.local/pi-bolt" allow_unsigned=false
while [ "$#" -gt 0 ]; do
    case "$1" in --archive|--sha256|--prefix) [ "$#" -ge 2 ] || { echo "Missing value for $1." >&2; exit 2; } ;; esac
    case "$1" in
        --archive) archive="$2"; shift 2 ;;
        --sha256) expected_sha="$2"; shift 2 ;;
        --prefix) prefix="$2"; shift 2 ;;
        --allow-unsigned) allow_unsigned=true; shift ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done
if [ -z "$archive" ] || [ -z "$expected_sha" ]; then
    echo 'Usage: bash install.sh --archive RELEASE.tar.gz --sha256 TRUSTED_SHA256 [--prefix DIR] [--allow-unsigned]' >&2; exit 2
fi
case "$expected_sha" in *[!0-9a-f]*|'') echo 'Expected lowercase SHA256 required.' >&2; exit 2 ;; esac
[ "${#expected_sha}" -eq 64 ] || { echo 'Expected 64-character SHA256.' >&2; exit 2; }
[ "$(uname -s)" = Darwin ] || { echo 'M5 macOS release only.' >&2; exit 1; }
actual_sha="$(/usr/bin/shasum -a 256 "$archive")"; actual_sha="${actual_sha%% *}"
[ "$actual_sha" = "$expected_sha" ] || { echo 'Archive SHA256 mismatch; nothing installed.' >&2; exit 1; }
case "$prefix" in /*) ;; *) echo 'Installation prefix must be absolute.' >&2; exit 2 ;; esac
[ ! -L "$prefix" ] || { echo 'Installation prefix must not be a symlink.' >&2; exit 1; }
mkdir -p "$prefix"; prefix="$(cd -P "$prefix" && pwd)"
mkdir "$prefix/.install-lock" 2>/dev/null || { echo 'Another install/rollback is active. Check the install lock.' >&2; exit 1; }
stage=''
cleanup() { [ -z "$stage" ] || rm -rf -- "$stage"; rmdir "$prefix/.install-lock"; }
trap cleanup EXIT
stage="$(/usr/bin/mktemp -d "$prefix/.stage.XXXXXX")"
# Inspect paths and member types before extracting. Builder emits no links.
/usr/bin/tar -tzf "$archive" > "$stage/members"
archive_root=''
while IFS= read -r member; do
    case "$member" in /*|../*|*/../*|*/..|*\\*|*//* ) echo 'Unsafe archive path.' >&2; exit 1 ;; esac
    top="${member%%/*}"
    case "$top" in pi-bolt-m5-*) ;; *) echo 'Unexpected archive root.' >&2; exit 1 ;; esac
    if [ -z "$archive_root" ]; then archive_root="$top"; fi
    [ "$top" = "$archive_root" ] || { echo 'Multiple archive roots.' >&2; exit 1; }
done < "$stage/members"
[ -n "$archive_root" ] || { echo 'Empty archive.' >&2; exit 1; }
/usr/bin/tar -tvzf "$archive" > "$stage/types"
while IFS= read -r member; do
    case "$member" in -*|d*) ;; *) echo 'Archive links/special files forbidden.' >&2; exit 1 ;; esac
done < "$stage/types"
/usr/bin/tar -xzf "$archive" -C "$stage" --no-same-owner
package="$stage/$archive_root"
[ -f "$package/release.json" ] && [ -f "$package/SHA256SUMS" ] || { echo 'Release metadata missing.' >&2; exit 1; }
(cd "$package" && /usr/bin/shasum -a 256 -c SHA256SUMS > "$stage/checksums.log") || { cat "$stage/checksums.log" >&2; exit 1; }
version="$(/usr/bin/plutil -extract version raw -o - "$package/release.json")"
valid_version "$version" || { echo 'Invalid release version.' >&2; exit 1; }
[ "$archive_root" = "pi-bolt-m5-$version" ] || { echo 'Version/archive root mismatch.' >&2; exit 1; }
[ "$(/usr/bin/plutil -extract schema raw -o - "$package/release.json")" = 1 ] &&
    [ "$(/usr/bin/plutil -extract target raw -o - "$package/release.json")" = darwin-arm64 ] &&
    [ "$(/usr/bin/plutil -extract pi_version raw -o - "$package/release.json")" = 1.0.0 ] || { echo 'Unknown release format/target.' >&2; exit 1; }
status="$(/usr/bin/plutil -extract release_status raw -o - "$package/release.json")"
case "$status" in
    unsigned-candidate) [ "$allow_unsigned" = true ] || { echo 'Unsigned candidate: public installation is blocked. Maintainer tests require --allow-unsigned.' >&2; exit 1; } ;;
    notarized)
        validate_notarized
        ;;
    *) echo 'Release status is not installable.' >&2; exit 1 ;;
esac
# Read-only smoke before activation. This also enforces CPU and minimum macOS.
[ "$("$package/bin/pi" --version)" = '1.0.0' ] || { echo 'Pi version check failed.' >&2; exit 1; }
[ ! -L "$prefix/packages" ] && [ ! -L "$prefix/bin" ] || { echo 'Managed directories may not be symlinks.' >&2; exit 1; }
for pointer in current previous; do
    if [ -e "$prefix/$pointer" ] || [ -L "$prefix/$pointer" ]; then
        validate_pointer "$pointer"
    fi
done
mkdir -p "$prefix/packages" "$prefix/bin"
[ ! -L "$prefix/packages" ] && [ ! -L "$prefix/bin" ] || { echo 'Managed directories may not be symlinks.' >&2; exit 1; }
[ ! -e "$prefix/packages/$archive_root" ] && [ ! -L "$prefix/packages/$archive_root" ] || { echo 'This version already has a destination. Use a new release version.' >&2; exit 1; }
for pair in 'pi-bolt:pi' 'pi-bolt-tier10000:pi-tier10000' 'pi-bolt-doctor:pi-doctor'; do
    validate_command "${pair%%:*}" "${pair#*:}"
done
mv "$package" "$prefix/packages/$archive_root"
if [ -L "$prefix/current" ]; then
    ln -s "$(readlink "$prefix/current")" "$stage/previous"
    mv -h -f "$stage/previous" "$prefix/previous"
fi
ln -s "packages/$archive_root" "$stage/current"
mv -h -f "$stage/current" "$prefix/current"
for pair in 'pi-bolt:pi' 'pi-bolt-tier10000:pi-tier10000' 'pi-bolt-doctor:pi-doctor'; do
    name="${pair%%:*}"; target="${pair#*:}"
    ln -s "../current/bin/$target" "$stage/$name"
    mv -h -f "$stage/$name" "$prefix/bin/$name"
done
printf 'Installed Pi-Bolt %s at %s\n' "$version" "$prefix"
printf 'Run: "%s/bin/pi-bolt"\nAdd "%s/bin" to your PATH.\n' "$prefix" "$prefix"
