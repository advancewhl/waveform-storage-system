"""Compatibility import for the former experimental UI module."""

from waveform_storage.gui.operator_window import BeamlineAreaModel, OperatorMainWindow

ExperimentalMainWindow = OperatorMainWindow

__all__ = ["BeamlineAreaModel", "ExperimentalMainWindow"]
