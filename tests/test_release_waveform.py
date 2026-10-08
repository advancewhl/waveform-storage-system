"""A release activation must reject corrupt binaries and invalid release paths."""
from pathlib import Path
from tempfile import TemporaryDirectory
import importlib.util
import json
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release_waveform', ROOT / 'tools/release_waveform.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseActivationTests(unittest.TestCase):
    def test_activation_verifies_artifacts_and_keeps_rollback_release(self):
        with TemporaryDirectory() as temp, patch.object(release, 'ROOT', Path(temp)):
            root = Path(temp)
            old = root / 'outputs/operator-local/releases/old/waveform-operator'
            new = root / 'outputs/operator-local/releases/new/waveform-operator'
            old.mkdir(parents=True)
            new.mkdir(parents=True)
            binary = new / 'waveform-operator'
            binary.write_bytes(b'new executable')
            info = {'binary_sha256': release.digest(binary)}
            (new / 'BUILD-INFO').write_text(json.dumps(info))
            (new / 'MANIFEST.sha256').write_text(
                f'{release.digest(binary)}  waveform-operator\n'
                f'{release.digest(new / "BUILD-INFO")}  BUILD-INFO\n')
            current = root / 'outputs/operator-local/current'
            current.symlink_to(old)
            evidence = release.activate_operator(new)
            self.assertEqual(current.resolve(), new)
            self.assertEqual(evidence['previous_operator_release'], str(old))
            self.assertTrue(old.is_dir())
            binary.write_bytes(b'corrupted')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                release.activate_operator(new)
            with self.assertRaisesRegex(ValueError, 'inside'):
                release.activate_operator(root / 'outside')
