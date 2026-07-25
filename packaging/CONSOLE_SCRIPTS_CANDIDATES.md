# Console script candidates

No production entrypoint is changed in this stage. The current scripts remain:

- `python apps/operator_gui/main.py`
- `python apps/acquisition_agent/main_file_transport_h5df.py`
- `python tools/device_registry.py`

Candidates for a later, field-reviewed packaging stage:

- `waveform-operator-gui`
- `waveform-acquisition-agent`
- `waveform-device-registry`
- `waveform-quality`

Before enabling these in `pyproject.toml`, Linux Qt resources, configuration
discovery, deployment ownership, production protocol and rollback must be
validated. No production package or executable is generated here.
