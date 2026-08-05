from dataclasses import dataclass
from typing import Optional

from caen_libs.caendigitizer import Device, Error, ConnectionType


_NUMERIC_ARG_TYPES = {
    ConnectionType.USB,
    ConnectionType.OPTICAL_LINK,
    ConnectionType.USB_A4818,
    ConnectionType.USB_V4718,
}

_STRING_ARG_TYPES = {ConnectionType.ETH_V4718}

_LINK_SCAN_LIMIT = 10


@dataclass(slots=True)
class DigitizerConnectionInfo:
    model_name: str
    board_model: int
    serial_number: int
    firmware_code: int
    adc_n_bits: int
    channels: int
    link_used: str


def _arg_requires_string(connection_type: ConnectionType) -> bool:
    return connection_type in _STRING_ARG_TYPES


def _normalize_arg(connection_type: ConnectionType, arg: str) -> str:
    if _arg_requires_string(connection_type):
        return arg.strip()
    return arg.strip() or "0"


def open_device(
    connection_type: int,
    arg: str,
    conet_node: int = 0,
    vme_base_address: int = 0,
) -> Device:
    ctype = ConnectionType(connection_type)
    l_arg = _normalize_arg(ctype, arg)
    return Device.open(ctype, l_arg, conet_node, vme_base_address)


def _read_info(device: Device) -> DigitizerConnectionInfo:
    info = device.get_info()
    return DigitizerConnectionInfo(
        model_name=info.model_name,
        board_model=int(info.model),
        serial_number=info.serial_number,
        firmware_code=int(info.firmware_code),
        adc_n_bits=info.adc_n_bits,
        channels=info.channels,
        link_used=str(device.arg),
    )


_ERROR_HINTS = {
    Error.Code.DIGITIZER_NOT_FOUND: "No device detected. Check power, the USB/optical cable, and that the connection type matches the hardware.",
    Error.Code.COMM_ERROR: "Communication failed. Check cables and drivers, then retry.",
    Error.Code.INVALID_LINK_TYPE: "Connection type doesn't match the hardware.",
    Error.Code.INVALID_PARAM: "Invalid connection parameters. Review the argument, conet node, and VME base address.",
    Error.Code.UNSUPPORTED_BASE_ADDRESS: "The VME base address may be wrong or unsupported.",
    Error.Code.DIGITIZER_ALREADY_OPEN: "The device is already in use by another process.",
    Error.Code.TIMEOUT: "The device did not respond in time.",
}


def _error_hint(errors: list[Error]) -> str:
    codes = {e.code for e in errors}
    for code in (_ERROR_HINTS.keys() & codes):
        return _ERROR_HINTS[code]
    return "Connection failed. Review the digitizer settings and try again."


def _aggregate_errors(attempts: list[tuple[str, Error]]) -> str:
    groups: dict[tuple[str, int], list[str]] = {}
    for link, e in attempts:
        key = (e.func, int(e.code.value))
        groups.setdefault(key, []).append(link)

    parts: list[str] = []
    for (func, code), links in groups.items():
        label = Error.Code(code).name
        if len(links) == 1:
            parts.append(f"link {links[0]}: {label}")
        else:
            first, *rest = links
            if all(int(l) == int(first) + i + 1 for i, l in enumerate(rest)):
                span = f"{first}-{rest[-1]}"
                parts.append(f"links {span}: {label}")
            else:
                parts.append(f"links {', '.join(links)}: {label}")
    return " | ".join(parts)


def test_connection(
    connection_type: int,
    arg: str,
    conet_node: int = 0,
    vme_base_address: int = 0,
    link_scan: bool = True,
) -> dict:
    ctype = ConnectionType(connection_type)

    candidates: list[str] = [_normalize_arg(ctype, arg)]
    if link_scan and ctype in _NUMERIC_ARG_TYPES:
        candidates += [str(i) for i in range(_LINK_SCAN_LIMIT) if str(i) not in candidates]

    attempts: list[tuple[str, Error]] = []
    for link in candidates:
        device = None
        try:
            device = Device.open(ctype, link, conet_node, vme_base_address)
            info = _read_info(device)
            return {
                "success": True,
                **info.__dict__,
            }
        except Error as e:
            attempts.append((link, e))
        finally:
            if device is not None:
                device.close()

    return {
        "success": False,
        "error": _aggregate_errors(attempts),
        "hint": _error_hint([e for _, e in attempts]),
        "suggested_arg": candidates[0] if candidates else "",
    }


def enumerate_digitizers(
    connection_type: int,
    conet_node: int = 0,
    vme_base_address: int = 0,
    max_links: int = _LINK_SCAN_LIMIT,
) -> list[dict]:
    ctype = ConnectionType(connection_type)
    devices: list[dict] = []
    for link in range(max_links):
        device = None
        try:
            device = Device.open(ctype, str(link), conet_node, vme_base_address)
            info = _read_info(device)
            devices.append(
                {
                    "link": str(link),
                    "model_name": info.model_name,
                    "serial_number": info.serial_number,
                }
            )
        except Error:
            continue
        finally:
            if device is not None:
                device.close()
    return devices


def test_connection_by_id(digitizer_id: int) -> dict:
    from .db import get_session, CaenDigitizer

    with get_session() as session:
        row = session.get(CaenDigitizer, digitizer_id)
        if row is None:
            return {"success": False, "error": f"Digitizer {digitizer_id} not found"}

        return test_connection(
            connection_type=row.connection_type,
            arg=row.arg,
            conet_node=row.conet_node,
            vme_base_address=row.vme_base_address,
        )
