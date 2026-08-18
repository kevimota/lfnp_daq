#!/usr/bin/env python3
"""Standalone digitizer smoke test.

Exercises the full DigitizerScanner open -> configure -> acquire -> readout ->
ROOT-write -> close path against the physical board, without the HV/DB/FSM
stack. It is the quick regression check that the earlier
``malloc(): corrupted top size`` crash (the DT5742 on-board DRS4
correction path, see below) is gone, and that the per-board driver
(DT5742/DRS4 vs DT5743/SAMLONG) configures and reads out correctly.

Run inside the ``daq`` container (native CAEN libraries are required):

    uv run python scripts/smoke_digitizer.py --auto-link
    # specific device + default software-trigger config:
    uv run python scripts/smoke_digitizer.py --connection-type usb --arg 0 \
        --target 50 --freq 10.0 --post-trigger 0
    # hardware-trigger (external) mode, DT5743-style config if it's a SAM board:
    uv run python scripts/smoke_digitizer.py --arg 0 --mode external \
        --target 100 --sampling-frequency-hz 1600000000 --record-length 512

Exit code 0 on success + a filled ROOT file; 1 on any failure.

DT5742 DRS4 correction is applied OFFLINE in software: the on-board
``load/enable`` path corrupts the heap (``malloc(): corrupted top size``) and
the caen_libs ``get_correction_tables`` wrapper overruns a single-table buffer,
so the correction tables are read from flash with a full-size buffer and
cell/nsample/glitch/time corrections are applied to every waveform (uniform
time grid). The DT5743 SAM correction runs on-board as usual.
"""
import argparse
import asyncio
import logging
import os
import sys
import tempfile
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from caen_libs.caendigitizer import ConnectionType  # noqa: E402

from daq.hardware import DigitizerScanner  # noqa: E402
from daq.hardware.digitizer_interface import enumerate_digitizers  # noqa: E402

_LOG = logging.getLogger("smoke")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Digitizer smoke test")
    p.add_argument("--connection-type", default="USB", type=str.upper,
                   choices=[c.name for c in ConnectionType],
                   help="ConnectionType enum name (default: USB); case-insensitive")
    p.add_argument("--arg", default=None,
                   help="Link/argument to open (USB link index, optical name, ...). "
                        "Default: 0, or the first detected device with --auto-link.")
    p.add_argument("--auto-link", action="store_true",
                   help="Scan links 0..9 and use the first device found")
    p.add_argument("--conet-node", type=int, default=0)
    p.add_argument("--vme-base-address", type=int, default=0)

    p.add_argument("--mode", choices=["random", "external"], default="random",
                   help="random: internal periodic software trigger (default); "
                        "external: read the trigger input (needs a hardware signal)")
    p.add_argument("--target", type=int, default=50,
                   help="number_of_triggers to collect")
    p.add_argument("--freq", type=float, default=10.0,
                   help="software-trigger frequency in Hz (random mode)")
    p.add_argument("--sampling-frequency-hz", type=float, default=None,
                   help="Override sampling frequency; omitted -> board default "
                        "(5 GS/s for DT5742, 3.2 GS/s for DT5743). "
                        "DT5742: 5e9/2.5e9/1e9/750e6. DT5743: 3.2e9/1.6e9/800e6/400e6.")
    p.add_argument("--post-trigger", type=int, default=0,
                   help="post-trigger size as percentage 0-100 (default 0)")
    p.add_argument("--record-length", type=int, default=None,
                   help="record length (only used on boards that support it, e.g. DT5743)")
    p.add_argument("--channels", default=None,
                   help="comma-separated enabled channels, e.g. '0,1'. "
                        "Default: all channels.")
    p.add_argument("--input-range-vpp", type=float, default=None)
    p.add_argument("--output", default=None,
                   help="output directory (default: a temp dir under /tmp)")
    p.add_argument("--timeout", type=float, default=None,
                   help="max acquisition wall-clock seconds (default derived from "
                        "target/freq with margin)")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def _connection_type(name: str) -> int:
    return int(ConnectionType[name])


def _row(ct: int, arg: str, conet_node: int, vme_base_address: int) -> SimpleNamespace:
    """Minimal stand-in for a CaenDigitizer DB row (DigitizerScanner only reads
    these four attributes before open())."""
    return SimpleNamespace(
        connection_type=ct,
        arg=arg,
        conet_node=conet_node,
        vme_base_address=vme_base_address,
    )


def _channels(spec: str | None) -> list[dict]:
    if not spec:
        return []
    return [{"channel": int(ch.strip()), "enabled": True} for ch in spec.split(",") if ch.strip()]


async def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    ct = _connection_type(args.connection_type)
    arg = args.arg

    if args.auto_link or arg is None:
        devices = enumerate_digitizers(ct, args.conet_node, args.vme_base_address)
        _LOG.info("link scan found %d device(s): %s",
                  len(devices), [(d["link"], d["model_name"]) for d in devices])
        if not devices:
            _LOG.error("no digitizer detected on connection type %s (links 0..9); "
                       "check power/cable and `--connection-type`", args.connection_type)
            return 1
        arg = str(devices[0]["link"])
        _LOG.info("auto-selected link %s", arg)
    elif arg is None:
        arg = "0"

    scanner = DigitizerScanner(_row(ct, arg, args.conet_node, args.vme_base_address))

    run_dir = args.output or tempfile.mkdtemp(prefix="digitizer_smoke_")
    os.makedirs(run_dir, exist_ok=True)

    cfg = {
        "trigger_mode": args.mode,
        "trigger_frequency_hz": args.freq,
        "number_of_triggers": args.target,
        "post_trigger_size": args.post_trigger,
        "channels": _channels(args.channels),
        "input_range_vpp": args.input_range_vpp,
    }
    if args.sampling_frequency_hz is not None:
        cfg["sampling_frequency_hz"] = args.sampling_frequency_hz
    if args.record_length is not None:
        cfg["record_length"] = args.record_length

    try:
        await scanner.open()
        _LOG.info("board: %s model=%s channels(from info)=%s drs4=%s sam=%s",
                  scanner.info.model_name, int(scanner.info.model),
                  getattr(scanner.info, "channels", "?"), scanner.is_drs4, scanner.is_sam)

        await scanner.configure(cfg)
        _LOG.info("configured: enabled=%s record_length=%s calibrated=%s",
                  scanner.enabled_channels, scanner.record_length, scanner.calibrated)

        t_start = time.monotonic()
        await scanner.begin_point(run_dir, 0, cfg, [], run_id=0)

        deadline = t_start + (args.timeout or max(15.0, 2.0 * args.target / max(args.freq, 1e-9) + 15.0))
        while scanner.collected < args.target:
            if time.monotonic() > deadline:
                _LOG.error("timeout after %.0fs: collected=%s/%s (mode=%s) - "
                           "external mode needs hardware triggers, or the trigger "
                           "path is failing",
                           args.timeout or 0, scanner.collected, args.target, args.mode)
                return 1
            await scanner.step()
            await asyncio.sleep(0.02)

        result = await scanner.end_point()
        n = result["n_events"]
        path = result["path"]
        _LOG.info("acquired %s/%s events in %.1fs", n, args.target, time.monotonic() - t_start)

        ok = n >= args.target and os.path.isfile(path) and os.path.getsize(path) > 0
        _LOG.info("ROOT output: %s (%.0f bytes)", path, os.path.getsize(path) if os.path.isfile(path) else 0)
        if ok:
            _LOG.info("SMOKE TEST PASSED (%s/%s events, no malloc(): corrupted top size - "
                      "DT5742 offline DRS4 correction; DT5743 SAM per-group post-trigger)",
                      n, args.target)
        else:
            _LOG.error("SMOKE TEST FAILED: collected=%s target=%s file_ok=%s", n, args.target, os.path.isfile(path))
        return 0 if ok else 1
    finally:
        await scanner.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))