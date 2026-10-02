import importlib.util
from pathlib import Path
import tempfile
import unittest

MODULE=Path(__file__).resolve().parents[1]/'package.py'
spec=importlib.util.spec_from_file_location('release_package',MODULE)
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)

class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'pi-bolt-m5-1.0.0-m5.1';self.root.mkdir()
        (self.root/'bin').mkdir();(self.root/'bin/pi').write_text('fixture');(self.root/'bin/pi').chmod(0o755)
        (self.root/'pi-native').write_bytes(b'executable');(self.root/'pi-native.aot').write_bytes(b'image')
        (self.root/'asset with space').write_text('asset')
        self.manifest={'schema':1,'target':'darwin-arm64','executable':'pi-native','image':'pi-native.aot','package_root':'.','launchers':{'hybrid':'bin/pi'}}
        p.seal(self.root,self.manifest)
    def test_verified_complete_inventory(self):
        self.assertEqual(p.verify(self.root)['target'],'darwin-arm64')
    def test_same_size_tamper_rejected(self):
        (self.root/'pi-native.aot').write_bytes(b'wrong')
        with self.assertRaises(ValueError):p.verify(self.root)
    def test_extra_file_rejected(self):
        (self.root/'undeclared').write_text('extra')
        with self.assertRaises(ValueError):p.verify(self.root)
    def test_symlink_rejected(self):
        (self.root/'link').symlink_to('/tmp')
        with self.assertRaises(ValueError):p.verify(self.root)
    def test_permission_change_rejected(self):
        (self.root/'bin/pi').chmod(0o644)
        with self.assertRaises(ValueError):p.verify(self.root)
    def test_checksum_table_tamper_rejected(self):
        (self.root/'SHA256SUMS').write_text('forged')
        with self.assertRaises(ValueError):p.verify(self.root)
    def test_archive_determinism_and_clean_members(self):
        first=Path(self.temp.name)/'first.tar.gz';second=Path(self.temp.name)/'second.tar.gz'
        p.archive(self.root,first);p.archive(self.root,second)
        self.assertEqual(first.read_bytes(),second.read_bytes())
        import tarfile
        with tarfile.open(first) as archive:
            for m in archive:
                self.assertTrue(m.isfile() or m.isdir());self.assertEqual(m.uid,0);self.assertEqual(m.mtime,0)
                self.assertFalse(m.name.startswith('/'));self.assertNotIn('..',Path(m.name).parts)

if __name__=='__main__':unittest.main()
