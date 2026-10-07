# Formal Candidate V2 acquisition server source package

Build with `python packaging/build-server-formal.py`. The archive contains an
explicit allowlist of server runtime source, the server registry, a launcher,
runtime requirements, and checksums. The packaged `config/devices.json` is
copied from the same approved `config/devices.probe47.json` used by the formal
GUI, so both sides refer to the same 47 capture devices. It does not contain the operator GUI,
legacy GUI, demo backend, tests, samples, credentials, or an interpreter.

A compatible Python environment and explicit service environment settings are
required. Supply the same approved values documented for Candidate V2 before
starting `./run-server.sh`. Run `./run-server.sh --check` first; it checks
configuration without opening the server socket. The build script performs
that check in an extracted temporary copy with temporary local data and host
keys. It does not validate production EPICS, SSH, MongoDB, or the 5001 listener.
