# M5 release process

The first distributable is an **unsigned candidate**. A successful package/CLI check does not promote it to production. `release.json` deliberately retains `release_status: unsigned-candidate` and separate uncompleted production gates. The installer rejects it unless the user explicitly selects `--allow-unsigned`.

## Candidate checklist

1. Verify pinned compiler, image-cache compatibility, native selection and matched pair.
2. Copy runtime assets; package may contain no symlinks or runtime paths into the developer checkout.
3. Include Apache-2.0 project LICENSE/NOTICE, exact upstream notices, source pins, modifications and dependency evidence.
4. Verify full file/mode inventory and canonical SHA256SUMS; preserve build inputs.
5. Run both launchers/layouts from a relocated directory with source reads denied. Require every original worker and PTY gate, not a version-only smoke test.
6. Test external/internal checksum rejection, unsafe archive rejection, managed installs/upgrades, rollback, lock handling and unlink without touching user data.
7. Archive deterministically; record the external SHA256 and install that exact archive into a fresh temporary prefix. Retain original failed evidence.
8. Export the GitHub source tree and pass its CI checks from that tree. Put large binary/evidence archives in Release assets, not Git.

After `acceptance.py`, run `audit.py --package PACKAGE --receipt ACCEPTANCE.json --output AUDIT.json` before `record-acceptance.py --package PACKAGE --receipt ACCEPTANCE.json --audit AUDIT.json`. The independent audit rechecks the unchanged raw consumers, exact file inventory, all 120 assertions and 20 observed worker exits, then proves both native policies with source reads denied. The metadata recorder attaches that evidence without changing runtime files. Archive only after recording acceptance. Run these checks outside benchmark intervals.

## Production gates still required

- Developer ID Application signature and Hardened Runtime, final mapped-image/JIT/Wasm/native-addon acceptance under that signature.
- Successful Apple notarytool submission, final Gatekeeper assessment and quarantined-download installation. Do not label an ad-hoc signature notarized or remove quarantine as a release test.
- Validation on another M5 with macOS27, including fresh-user installation and documented M5-family coverage.
- Corresponding modified JSC source/relink delivery and reconciliation of actually linked third-party notices. The collector is a conservative inventory, not clearance.
- Real-provider/concurrency/long-session soak, extension/provider failure behavior and compatibility policy. Never call the candidate universally fastest or compatible with every npm package.

No valid Developer ID identity is installed on the current build host; the user explicitly chose to prepare the unsigned candidate first. The source pipeline and public candidate can be reviewed before that later signing step. Apple requires Hardened Runtime for its notarization workflow: [Apple notarization documentation](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution).

The pinned Bun `entitlements.plist` has JIT, unsigned-executable-memory, executable-page-protection, dyld-environment and library-validation exceptions. A separate temporary ad-hoc hardened copy passed mapped sidecar/native-selection and worker probes with exactly those entitlements. This is compatibility evidence only; minimal entitlements, final Developer ID and notarized operation remain untested. Keep any future entitlement choice visible for review.

## Failure behavior and performance limits

Launchers clear inherited JSC/AOT overrides, derive all runtime paths from their installed location, enforce macOS27/M5 and check executable/image sizes. Installer and doctor verify full hashes. Size checks alone do not detect same-size post-install corruption; hashes are not run on every interactive launch. The engine may fall back on missing/incompatible images; release acceptance must independently prove native selection and exact pair identity. A fixed virtual address reservation remains an engine startup requirement.

The launcher adds platform/size checks beyond direct-executable historical timing. Historical benchmarks describe their measured artifact, not this package's install/launcher overhead. Any release performance claim should include a fresh packaged-launcher cohort.
