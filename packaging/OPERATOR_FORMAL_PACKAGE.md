# Formal operator source package

Build with `python packaging/build-operator-formal.py`. The generated archive
contains only the allowlisted Candidate V2 GUI source, two device registries,
a launcher, runtime requirements, and a checksum manifest. It does not contain
the former Designer GUI, demo backend, test data, documents, local settings,
credentials, or a Python interpreter.

Install the listed Python packages in a compatible environment. To inspect
the formal interface without a service connection, run
`./run-operator.sh --ui-only`; capture, retrieval and export controls are disabled.

For connected operation, set the Candidate V2 endpoint and start the GUI:

```sh
python3 -m pip install -r requirements-operator-formal.txt
export WAVEFORM_CANDIDATE_ZMQ_ENDPOINT=tcp://127.0.0.1:5001
./run-operator.sh
```

The workstation launcher `tools/run_candidate_gui_47.sh` still performs the
SSH tunnel and server health check before launching the same formal entry.
The bundle does not establish that tunnel by itself. Its capture registry
matches the approved 47-device file; other displayed devices remain view-only.
When running through the workstation launcher, history-file downloads reuse
the authenticated SSH control socket. A standalone installation may set
`WAVEFORM_STORAGE_SSH_CONTROL_SOCKET` to an existing OpenSSH control socket;
otherwise it needs direct storage-server SSH credentials and reachability.

On the history page, choose the capture's start date, retrieve its available
times, select a time, and click **选择BPM**. The picker lists only BPMs with
waveform files at that time. Check the devices to align or download, then use
**确认并对齐**. History BPM selection is independent of the capture-area tree.

`MANIFEST.sha256` records every package file besides itself and `BUILD-INFO`.
The build script extracts the actual archive in a temporary directory, checks
file hashes and exclusions, imports the formal runtime without old/demo modules,
and renders an offscreen screenshot without sending a capture request.
