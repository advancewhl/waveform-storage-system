"""Exact legacy GUI capture behavior kept outside the Qt MainWindow."""

from __future__ import annotations

import os


def read_legacy_frequency(context_factory, *, timeout_address=None):
    if timeout_address is not None:
        os.environ["EPICS_CA_ADDR_LIST"] = timeout_address
    context = context_factory()
    frequency_pv = context.get_pvs("AC-LA-INJ1-TIM:WRN1:MCFQ8")[0]
    return frequency_pv.read().data[0]


def send_legacy_message(
    sender,
    *,
    server_host,
    server_port,
    message,
):
    return sender(
        server_host=server_host,
        server_port=server_port,
        message=message,
    )
