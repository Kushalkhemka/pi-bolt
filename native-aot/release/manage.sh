#!/bin/bash
# Keep version directories and user ~/.pi credentials/sessions intact.
set -eu
[ "$#" -eq 2 ] || { echo 'Usage: bash manage.sh rollback|unlink /absolute/install/prefix' >&2; exit 2; }
action="$1"; prefix="$2"
case "$prefix" in /*) ;; *) exit 2 ;; esac
[ ! -L "$prefix" ] && [ -d "$prefix" ] || exit 1
prefix="$(cd -P "$prefix" && pwd)"
[ ! -L "$prefix/packages" ] && [ ! -L "$prefix/bin" ] || { echo 'Managed directories may not be symlinks.' >&2; exit 1; }
mkdir "$prefix/.install-lock" 2>/dev/null || { echo 'Another installation operation is active.' >&2; exit 1; }
stage=''
trap '[ -z "$stage" ] || rm -rf -- "$stage"; rmdir "$prefix/.install-lock"' EXIT
stage="$(mktemp -d "$prefix/.manage.XXXXXX")"
for pointer in current previous; do
    if [ -L "$prefix/$pointer" ]; then
        target="$(/usr/bin/readlink "$prefix/$pointer")"
        [[ "$target" =~ ^packages/pi-bolt-m5-[0-9]+\.[0-9]+\.[0-9]+-m5\.[0-9]+$ ]] || { echo 'Unmanaged pointer.' >&2; exit 1; }
        [ ! -L "$prefix/$target" ] && [ -d "$prefix/$target" ] || { echo 'Pointer must refer to a real installed package directory.' >&2; exit 1; }
    elif [ -e "$prefix/$pointer" ]; then echo 'Unmanaged pointer.' >&2; exit 1
    fi
done
# Validate the complete set before any rollback or unlink mutation.
for pair in 'pi-bolt:pi' 'pi-bolt-tier10000:pi-tier10000' 'pi-bolt-doctor:pi-doctor'; do
    name="${pair%%:*}"; target="${pair#*:}"
    if [ -e "$prefix/bin/$name" ] || [ -L "$prefix/bin/$name" ]; then
        [ -L "$prefix/bin/$name" ] && [ "$(/usr/bin/readlink "$prefix/bin/$name")" = "../current/bin/$target" ] || { echo 'Unmanaged command path.' >&2; exit 1; }
    fi
done
case "$action" in
    rollback)
        [ -L "$prefix/current" ] && [ -L "$prefix/previous" ] || { echo 'No previous version.' >&2; exit 1; }
        old="$(readlink "$prefix/current")"; prior="$(readlink "$prefix/previous")"
        (cd "$prefix/$prior" && /usr/bin/shasum -a 256 -c SHA256SUMS >/dev/null)
        [ "$("$prefix/$prior/bin/pi" --version)" = '1.0.0' ] || exit 1
        ln -s "$old" "$stage/previous";ln -s "$prior" "$stage/current"
        mv -h -f "$stage/current" "$prefix/current"; mv -h -f "$stage/previous" "$prefix/previous"
        printf 'Rolled back to %s\n' "$prior" ;;
    unlink)
        for name in pi-bolt pi-bolt-tier10000 pi-bolt-doctor; do
            if [ -L "$prefix/bin/$name" ]; then
                rm "$prefix/bin/$name"
            fi
        done
        printf 'Removed managed command links. Version files and user data remain at %s.\n' "$prefix" ;;
    *) exit 2 ;;
esac
