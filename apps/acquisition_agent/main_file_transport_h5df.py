"""Compatibility entrypoint for the historical acquisition prototype."""

from waveform_storage.legacy.acquisition_agent_adapter import file_transport


def main():
    file_transport().main()


if __name__ == "__main__":
    main()
