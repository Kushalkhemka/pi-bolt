# Install Pi-Bolt

The `1.0.0-m5.1` package contains Pi 1.0.0 and its runtime/assets. It requires native Apple M5/macOS 27+. Installation needs built-in macOS tools; no Node, Bun, npm, Python or sudo is required.

Use the pinned download commands in the [README](../README.md#getting-started). Keep the installer and archive from the same release. Trust the release channel supplying checksums; checksums alone are not an independent publisher signature.

Installed commands live in `$HOME/.local/pi-bolt/bin`. Add that directory to your own PATH if desired. Start `pi-bolt` in your project, run `/login`, and use the normal Pi CLI flags. The installer does not alter your existing `pi` command or shell profile.

```bash
"$HOME/.local/pi-bolt/bin/pi-bolt" --version
"$HOME/.local/pi-bolt/bin/pi-bolt" --help
"$HOME/.local/pi-bolt/bin/pi-bolt" --tui-mode regular
"$HOME/.local/pi-bolt/bin/pi-bolt-tier10000"
```

`pi-bolt` uses Pi's normal settings and session storage. Extensions and tools run with your account's permissions. This runtime adds no permission sandbox.

## Integrity, update and rollback

Download `manage.sh` from the same release and verify it against the release checksum table before using it.

```bash
# Full file hashes, signature structure, version and Gatekeeper assessment.
"$HOME/.local/pi-bolt/bin/pi-bolt-doctor"

# After installing another candidate into this prefix:
bash manage.sh rollback "$HOME/.local/pi-bolt"

# Remove only managed command links; retain package versions and user data.
bash manage.sh unlink "$HOME/.local/pi-bolt"
```

The unsigned candidate requires `--allow-unsigned`. Doctor's exit 3 after an integrity pass reports the expected unsigned Gatekeeper rejection. The installer never removes quarantine or disables security settings. A quarantined download may remain blocked; build locally or wait for a signed release.

Packages are stored in version directories with atomic `current`/`previous` links. The installer rejects unmanaged conflicting commands, unsafe archive paths and failed checksums before activation. `--prefix /absolute/path` selects a different install prefix.
