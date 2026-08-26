# Candidate v2 Deepin 20.9 executable

The acquisition service is built as a Linux x86-64 PyInstaller `onedir`
artifact inside `python:3.10-buster` (Debian 10, glibc 2.28). This is the
compatibility baseline for the Deepin 20.9 waveform server. A Windows `.exe`
will not run on that host.

Build from the repository root:

```bash
bash packaging/build-acquisition-v2-debian10.sh
```

The output is written below `dist/deepin20.9/`. Upload the `.tar.gz` and its
`.sha256` file, verify the checksum on the server, and extract the complete
directory. Do not copy only the executable; `_internal` contains Python,
HDF5, cryptography, MongoDB client, EPICS and ZMQ runtime libraries.
`BUILD-INFO` records the source revision and compatibility baseline used for
the archive.

The executable removes the server's Python/Conda requirement. It does not
embed MongoDB, credentials, verified SSH host keys, HDF5 data, or service
configuration.

Run with an explicit server environment:

```bash
set -a
source /home/storage-user/waveform-storage-v2/service.env
set +a

./waveform-acquisition-v2/waveform-acquisition-v2 --check
./waveform-acquisition-v2/waveform-acquisition-v2
```
