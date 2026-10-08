"""Exercise desktop executable selection and GUI argument forwarding."""

from pathlib import Path
from tempfile import TemporaryDirectory
import os
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class OperatorLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "tools").mkdir()
        for name in ("launch_operator_desktop.sh", "run_candidate_gui_47.sh", "run_spectrum_preview.sh"):
            shutil.copy2(ROOT / "tools" / name, self.root / "tools" / name)
        (self.root / "config").mkdir()
        for name in ("devices.operator.json", "devices.probe47.json"):
            (self.root / "config" / name).write_text("{}")
        (self.root / ".env").write_text("")
        self.python = self.root / "healthy-python"
        self.python.write_text("#!/bin/sh\nexit 0\n")
        self.python.chmod(0o755)
        helper = self.root / "tools/waveform_ssh_secret.py"
        helper.write_text("#!/bin/sh\nprintf '%s\\n' 'storage-user@192.0.2.2'\n")
        helper.chmod(0o755)
        self.local = self.root / "outputs/operator-local/waveform-operator/waveform-operator"
        self.old = self.root / "dist/operator-debian10/waveform-operator/waveform-operator"
        for p in (self.local, self.old):
            self.make_executable(p)

    def make_executable(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('#!/bin/sh\nprintf "%s\\n" "$0" "$@" > "$WAVEFORM_LAUNCH_TRACE"\n')
        path.chmod(0o755)

    def launch(self, name, args, override=None):
        env = os.environ.copy()
        env.pop("WAVEFORM_OPERATOR_EXECUTABLE", None)
        env.update(WAVEFORM_GUI_PYTHON=str(self.python),
                   WAVEFORM_LAUNCH_TRACE=str(self.root / "trace"))
        if override:
            env["WAVEFORM_OPERATOR_EXECUTABLE"] = str(override)
        result = subprocess.run(["bash", str(self.root / "tools" / name), *args],
                                env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return (self.root / "trace").read_text().splitlines()

    def test_desktop_definition_routes_to_launcher_not_a_version_binary(self):
        definition = (ROOT / "tools/waveform-operator.desktop").read_text()
        executable = next(line[5:] for line in definition.splitlines() if line.startswith("Exec="))
        self.assertEqual(Path(executable), ROOT / "tools/launch_operator_desktop.sh")
        release = self.root / "outputs/operator-local/releases/contract/waveform-operator"
        self.make_executable(release / "waveform-operator")
        (self.root / "outputs/operator-local/current").symlink_to(release)
        self.assertEqual(self.launch(Path(executable).name, [])[0], str(release / "waveform-operator"))

    def test_desktop_prefers_local_bundle_and_forwards_gui_options(self):
        lines = self.launch("launch_operator_desktop.sh", ["--page", "spectrum"])
        self.assertEqual(lines[0], str(self.local))
        self.assertEqual(lines[-2:], ["--page", "spectrum"])

    def test_connected_preview_accepts_gui_options(self):
        lines = self.launch("run_spectrum_preview.sh", ["--connected", "--spectrum-input", "/tmp/input.h5"])
        self.assertEqual(lines[0], str(self.local))
        self.assertEqual(lines[-4:], ["--page", "spectrum", "--spectrum-input", "/tmp/input.h5"])

    def test_missing_local_bundle_falls_back_to_existing_desktop_bundle(self):
        self.local.unlink()
        lines = self.launch("launch_operator_desktop.sh", [])
        self.assertEqual(lines[0], str(self.old))

    def test_explicit_executable_override_is_preserved(self):
        custom = self.root / "custom-operator"
        self.make_executable(custom)
        lines = self.launch("launch_operator_desktop.sh", [], override=custom)
        self.assertEqual(lines[0], str(custom))

    def test_current_release_uses_resolved_path_and_preserves_previous_bundle(self):
        release = self.root / "outputs/operator-local/releases/new/waveform-operator"
        self.make_executable(release / "waveform-operator")
        (self.root / "outputs/operator-local/current").symlink_to(release, target_is_directory=True)
        for launcher, args in [("launch_operator_desktop.sh", ["--page", "spectrum"]),
                               ("run_spectrum_preview.sh", []),
                               ("run_candidate_gui_47.sh", ["--page", "spectrum"])]:
            lines = self.launch(launcher, args)
            self.assertEqual(lines[0], str(release / "waveform-operator"))
        self.assertTrue(self.local.is_file())


if __name__ == "__main__":
    unittest.main()
