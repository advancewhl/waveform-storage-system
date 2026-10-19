from __future__ import annotations

from types import SimpleNamespace
import unittest

from waveform_storage.gui.history_smoke import (
    _continue_alignment_preflight,
    _lock_capture_start_for_smoke,
)
from waveform_storage.gui.plotting import _wrap_device_label


class _Button:
    def __init__(self, text: str, enabled: bool = True):
        self._text = text
        self.enabled = enabled
        self.click_count = 0

    def isEnabled(self):
        return self.enabled

    def text(self):
        return self._text

    def click(self):
        self.click_count += 1


class HistorySmokePreflightTests(unittest.TestCase):
    def test_capture_remains_disabled_across_busy_lifecycle(self):
        start_button = _Button("开始采集", enabled=True)

        class _Window:
            def __init__(self):
                self.start_button = start_button

            def _set_busy(self, busy, _message):
                self.start_button.setEnabled(not busy)

        # Match Qt's button API for this lightweight lifecycle fixture.
        start_button.setEnabled = lambda enabled: setattr(start_button, "enabled", enabled)
        window = _Window()
        _lock_capture_start_for_smoke(window)

        window._set_busy(True, "处理中")
        window._set_busy(False, "就绪")

        self.assertFalse(start_button.isEnabled())

    def test_wrapped_device_label_preserves_identifier_characters(self):
        device_id = "AC_LA_L1_BI_COLDBPM05"
        label = _wrap_device_label(device_id)
        self.assertEqual(label.replace("\n", ""), device_id)
        self.assertEqual(label.splitlines(), ["AC_LA_L1_BI_", "COLDBPM05"])

    def test_records_identity_and_explicitly_continues_preflight(self):
        identity = ("2026-10-12", "request-1", 0)
        window = SimpleNamespace(
            _preflight_confirmation_required=True,
            _raw_step_report=SimpleNamespace(
                raw_step_compatible=False,
                summary="步进疑点；时间基准待核实",
            ),
            _history_key=lambda: identity,
            start_button=_Button("开始采集", enabled=False),
            align_button=_Button("继续严格对齐"),
        )
        evidence = {"identity": list(identity), "steps": {}}

        self.assertTrue(_continue_alignment_preflight(window, evidence))
        self.assertEqual(window.align_button.click_count, 1)
        self.assertEqual(evidence["preflight"], {
            "confirmation_required": True,
            "identity": list(identity),
            "raw_step_compatible": False,
            "summary": "步进疑点；时间基准待核实",
        })
        self.assertTrue(evidence["steps"]["raw_step_preflight"])

    def test_no_warning_does_not_click_or_claim_manual_confirmation(self):
        window = SimpleNamespace(
            _preflight_confirmation_required=False,
            align_button=_Button("检查并对齐"),
        )
        self.assertFalse(_continue_alignment_preflight(window, {"steps": {}}))
        self.assertEqual(window.align_button.click_count, 0)


if __name__ == "__main__":
    unittest.main()
