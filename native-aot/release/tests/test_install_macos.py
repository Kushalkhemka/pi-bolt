#!/usr/bin/env python3
"""Benign installer integration checks: tiny local archives, isolated HOME/prefix.

No custom runtime, downloads, signing mutation, publication or user data access.
Run: python3 -m unittest discover -s native-aot/release/tests -v
"""
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import tarfile
import tempfile
import unittest


RELEASE = Path(__file__).resolve().parents[1]


@unittest.skipUnless(platform.system() == "Darwin", "macOS installer uses system tools")
class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pi-bolt installer fixture ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "fixture home"
        self.home.mkdir()
        self.data = self.home / ".pi" / "sessions" / "retain.txt"
        self.data.parent.mkdir(parents=True)
        self.data.write_text("SESSION_DATA_RETAINED\n")
        self.prefix = self.root / "install prefix π"
        self.env = {**os.environ, "HOME": str(self.home)}
        self.counter = 0

    def archive(self, version="1.0.0-m5.1", *, tamper=False, status="unsigned-candidate",
                extra=None, special=None, metadata_overrides=None):
        self.counter += 1
        directory = self.root / ("package fixture " + str(self.counter))
        directory.mkdir()
        package = directory / ("pi-bolt-m5-" + version)
        (package / "bin").mkdir(parents=True)
        (package / "pi-native").write_text(
            "#!/bin/bash\nset -eu\n"
            "if [ \"${1-}\" = --version ]; then printf '1.0.0\\n'; "
            "else printf 'fixture " + version + "\\n'; fi\n")
        (package / "pi-native").chmod(0o755)
        (package / "pi-native.aot").write_text("BENIGN_FIXTURE_SIDECAR\n")
        for name in ("pi", "pi-tier10000", "pi-doctor"):
            (package / "bin" / name).write_text(
                '#!/bin/bash\nset -eu\nexec "$(cd -P "$(dirname "$0")/.." && pwd)/pi-native" "$@"\n')
            (package / "bin" / name).chmod(0o755)
        files = {p.relative_to(package).as_posix(): {
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            "bytes": p.stat().st_size, "mode": p.stat().st_mode & 0o777}
            for p in package.rglob("*") if p.is_file()}
        metadata = {"schema": 1, "version": version, "pi_version": "1.0.0",
                    "target": "darwin-arm64", "minimum_macos": "27.0",
                    "cpu_family": "Apple M5", "release_status": status,
                    "signing": {"developer_id": False, "notarized": status == "notarized"},
                    "package_root": ".", "executable": "pi-native", "image": "pi-native.aot",
                    "launchers": {"hybrid": "bin/pi", "tier10000": "bin/pi-tier10000"},
                    "files": files}
        metadata.update(metadata_overrides or {})
        (package / "release.json").write_text(json.dumps(metadata, sort_keys=True) + "\n")
        rows = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(package).as_posix()}\n"
                for p in sorted(package.rglob("*")) if p.is_file()]
        (package / "SHA256SUMS").write_text("".join(rows))
        if tamper:
            (package / "pi-native.aot").write_text("CORRUPTED_FIXTURE\n")
        archive = directory / "release.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(package, arcname=package.name)
            if extra:
                name, content = extra
                info = tarfile.TarInfo(name)
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
            if special:
                kind, target = special
                info = tarfile.TarInfo(package.name + "/benign-link")
                info.type = kind
                info.linkname = target
                tar.addfile(info)
        return archive

    def install(self, archive, *, allow=True, expected=None):
        args = ["/bin/bash", str(RELEASE / "install.sh"), "--archive", str(archive),
                "--sha256", expected or hashlib.sha256(archive.read_bytes()).hexdigest(),
                "--prefix", str(self.prefix)]
        if allow:
            args.append("--allow-unsigned")
        return subprocess.run(args, env=self.env, capture_output=True, text=True, timeout=20)

    def manage(self, action):
        return subprocess.run(["/bin/bash", str(RELEASE / "manage.sh"), action, str(self.prefix)],
                              env=self.env, capture_output=True, text=True, timeout=20)

    def assert_pass(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_rejected(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_inactive(self):
        self.assertFalse((self.prefix / "current").exists())
        self.assertFalse((self.prefix / "current").is_symlink())
        self.assertEqual(self.data.read_text(), "SESSION_DATA_RETAINED\n")

    def test_unsigned_requires_explicit_opt_in(self):
        self.assert_rejected(self.install(self.archive(), allow=False))
        self.assert_inactive()

    def test_install_spaces_unicode_and_links(self):
        self.assert_pass(self.install(self.archive()))
        self.assertEqual((self.prefix / "current").readlink(), Path("packages/pi-bolt-m5-1.0.0-m5.1"))
        expected = {"pi-bolt": "pi", "pi-bolt-tier10000": "pi-tier10000", "pi-bolt-doctor": "pi-doctor"}
        for name, target in expected.items():
            self.assertEqual((self.prefix / "bin" / name).readlink(), Path("../current/bin/" + target))
        self.assertEqual(self.data.read_text(), "SESSION_DATA_RETAINED\n")

    def test_external_archive_hash_mismatch(self):
        self.assert_rejected(self.install(self.archive(), expected="0" * 64))
        self.assert_inactive()

    def test_member_checksum_tamper(self):
        self.assert_rejected(self.install(self.archive(tamper=True)))
        self.assert_inactive()

    def test_path_traversal_rejected_before_extraction(self):
        archive = self.archive(extra=("pi-bolt-m5-1.0.0-m5.1/../benign-outside.txt", b"fixture"))
        self.assert_rejected(self.install(archive))
        self.assert_inactive()
        self.assertFalse((self.prefix / "benign-outside.txt").exists())

    def test_archive_symlink_rejected(self):
        self.assert_rejected(self.install(self.archive(special=(tarfile.SYMTYPE, "pi-native.aot"))))
        self.assert_inactive()

    def test_archive_hardlink_rejected(self):
        self.assert_rejected(self.install(self.archive(special=(tarfile.LNKTYPE, "pi-native.aot"))))
        self.assert_inactive()

    def test_notarization_claim_without_valid_signature_rejected(self):
        result = self.install(self.archive(status="notarized"), allow=False)
        self.assert_rejected(result)
        self.assertIn("Notarized release gates are incomplete", result.stderr)
        self.assert_inactive()

    def test_complete_notarization_claim_still_requires_real_signature(self):
        metadata = {"signing": {"developer_id": True, "notarized": True, "team_id": "FIXTURE123"},
                    "production_gates": {key: True for key in (
                        "portable_acceptance", "signed_hardened_acceptance", "notarization",
                        "quarantined_install", "second_m5", "source_relink_delivery", "provider_soak")}}
        result = self.install(self.archive(status="notarized", metadata_overrides=metadata), allow=False)
        self.assert_rejected(result)
        self.assert_inactive()

    def test_unknown_status_not_overridden_by_unsigned_opt_in(self):
        self.assert_rejected(self.install(self.archive(status="incomplete")))
        self.assert_inactive()

    def test_unmanaged_command_file_preserved(self):
        (self.prefix / "bin").mkdir(parents=True)
        original = self.prefix / "bin" / "pi-bolt"
        original.write_text("UNMANAGED\n")
        self.assert_rejected(self.install(self.archive()))
        self.assertEqual(original.read_text(), "UNMANAGED\n")
        self.assert_inactive()

    def test_managed_directory_symlink_rejected(self):
        outside = self.root / "benign external bin"
        outside.mkdir()
        self.prefix.mkdir()
        (self.prefix / "bin").symlink_to(outside, target_is_directory=True)
        self.assert_rejected(self.install(self.archive()))
        self.assertEqual(list(outside.iterdir()), [])
        self.assert_inactive()

    def test_upgrade_rollback_and_unlink_retain_data(self):
        self.assert_pass(self.install(self.archive()))
        self.assert_pass(self.install(self.archive("1.0.0-m5.2")))
        self.assertEqual((self.prefix / "current").readlink().name, "pi-bolt-m5-1.0.0-m5.2")
        self.assertEqual((self.prefix / "previous").readlink().name, "pi-bolt-m5-1.0.0-m5.1")
        self.assert_pass(self.manage("rollback"))
        self.assertEqual((self.prefix / "current").readlink().name, "pi-bolt-m5-1.0.0-m5.1")
        self.assertEqual((self.prefix / "previous").readlink().name, "pi-bolt-m5-1.0.0-m5.2")
        self.assert_pass(self.manage("unlink"))
        self.assertFalse((self.prefix / "bin" / "pi-bolt").exists())
        self.assertTrue((self.prefix / "packages" / "pi-bolt-m5-1.0.0-m5.1").is_dir())
        self.assertTrue((self.prefix / "packages" / "pi-bolt-m5-1.0.0-m5.2").is_dir())
        self.assertEqual(self.data.read_text(), "SESSION_DATA_RETAINED\n")

    def test_bad_upgrade_does_not_activate(self):
        self.assert_pass(self.install(self.archive()))
        self.assert_rejected(self.install(self.archive("1.0.0-m5.2", tamper=True)))
        self.assertEqual((self.prefix / "current").readlink().name, "pi-bolt-m5-1.0.0-m5.1")

    def test_rollback_refuses_changed_previous(self):
        self.assert_pass(self.install(self.archive()))
        self.assert_pass(self.install(self.archive("1.0.0-m5.2")))
        prior = self.prefix / "packages" / "pi-bolt-m5-1.0.0-m5.1"
        (prior / "pi-native.aot").write_text("CORRUPTED\n")
        self.assert_rejected(self.manage("rollback"))
        self.assertEqual((self.prefix / "current").readlink().name, "pi-bolt-m5-1.0.0-m5.2")

    def test_unmanaged_current_pointer_rejected(self):
        self.prefix.mkdir()
        (self.prefix / "current").symlink_to("elsewhere")
        self.assert_rejected(self.install(self.archive()))
        self.assertEqual((self.prefix / "current").readlink(), Path("elsewhere"))

    def test_wrong_command_target_rejected(self):
        (self.prefix / "bin").mkdir(parents=True)
        (self.prefix / "bin" / "pi-bolt").symlink_to("../current/bin/pi-doctor")
        self.assert_rejected(self.install(self.archive()))
        self.assertEqual((self.prefix / "bin" / "pi-bolt").readlink(), Path("../current/bin/pi-doctor"))
        self.assert_inactive()

    def test_unlink_validates_all_commands_before_changes(self):
        self.assert_pass(self.install(self.archive()))
        link = self.prefix / "bin" / "pi-bolt-tier10000"
        link.unlink()
        link.symlink_to("benign-unmanaged")
        self.assert_rejected(self.manage("unlink"))
        self.assertTrue((self.prefix / "bin" / "pi-bolt").is_symlink())
        self.assertEqual(link.readlink(), Path("benign-unmanaged"))

    def test_lock_refuses_operation(self):
        self.prefix.mkdir()
        (self.prefix / ".install-lock").mkdir()
        self.assert_rejected(self.install(self.archive()))
        self.assert_inactive()
        self.assertTrue((self.prefix / ".install-lock").is_dir())

    def test_dangling_current_pointer_rejected(self):
        self.prefix.mkdir()
        target = "packages/pi-bolt-m5-1.0.0-m5.0"
        (self.prefix / "current").symlink_to(target)
        self.assert_rejected(self.install(self.archive()))
        self.assertEqual((self.prefix / "current").readlink(), Path(target))

    def test_package_destination_dangling_link_rejected(self):
        (self.prefix / "packages").mkdir(parents=True)
        destination = self.prefix / "packages" / "pi-bolt-m5-1.0.0-m5.1"
        destination.symlink_to("benign-missing-package")
        self.assert_rejected(self.install(self.archive()))
        self.assertEqual(destination.readlink(), Path("benign-missing-package"))
        self.assert_inactive()

    def test_unlink_refuses_symlinked_managed_bin_directory(self):
        self.prefix.mkdir()
        outside = self.root / "benign external bin"
        outside.mkdir()
        (self.prefix / "bin").symlink_to(outside, target_is_directory=True)
        self.assert_rejected(self.manage("unlink"))
        self.assertEqual(list(outside.iterdir()), [])

    def test_pointer_requires_real_package_directory(self):
        self.assert_pass(self.install(self.archive()))
        target = self.prefix / "packages" / "pi-bolt-m5-1.0.0-m5.1"
        retained = self.root / "benign relocated fixture"
        target.rename(retained)
        target.symlink_to(retained, target_is_directory=True)
        self.assert_rejected(self.manage("unlink"))
        self.assertTrue((self.prefix / "bin" / "pi-bolt").is_symlink())

    def test_pointer_extra_component_rejected(self):
        self.assert_pass(self.install(self.archive()))
        current = self.prefix / "current"
        current.unlink()
        current.symlink_to("packages/pi-bolt-m5-1.0.0-m5.1/bin")
        self.assert_rejected(self.manage("unlink"))
        self.assertTrue((self.prefix / "bin" / "pi-bolt").is_symlink())

    def test_archive_backslash_name_rejected(self):
        self.assert_rejected(self.install(self.archive(extra=("pi-bolt-m5-1.0.0-m5.1/benign\\name", b"fixture"))))
        self.assert_inactive()


if __name__ == "__main__":
    unittest.main(verbosity=2)
