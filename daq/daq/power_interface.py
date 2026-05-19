from datetime import datetime, UTC
from caen_libs import caenhvwrapper as hv
from caen_libs._caenhvwrappertypes import SystemType, LinkType


class PowerSystemInterface:
    """Interface for CAEN power supply control."""

    def __init__(
        self,
        system_type: SystemType,
        link_type: LinkType,
        arg: str,
        username: str,
        password: str,
    ):
        self.system_type = system_type
        self.link_type = link_type
        self.arg = arg
        self.username = username
        self.password = password
        self.device: hv.Device | None = None

    def connect(self) -> bool:
        """Connect to the CAEN power supply device."""
        self.device = hv.Device.open(
            self.system_type, self.link_type, self.arg, self.username, self.password
        )

    def disconnect(self):
        self.device.close()
        self.device = None

    def is_connected(self) -> bool:
        """Check if connected to the device."""
        return self.device is not None

    def set_voltage(self, slot: int, channel: int, voltage: float) -> bool:
        """Set voltage on a specific slot and channel."""
        if self.device is None:
            return False
        else:
            self.device.set_ch_param(slot, [channel], "V0Set", voltage)
        return True

    def read_voltage(self, slot: int, channel: int) -> float:
        """Read voltage from a specific slot and channel."""
        if self.device is None:
            return -99.0
        else:
            [voltage] = self.device.get_ch_param(slot, [channel], "VMon")
            return voltage

    def read_current(self, slot: int, channel: int) -> float:
        """Read current from a specific slot and channel."""
        if self.device is None:
            return -99.0
        else:
            [current] = self.device.get_ch_param(slot, [channel], "IMon")
            return current

    def power_on(self, slot: int, channel: int) -> bool:
        """Power Channel On"""
        if self.device is None:
            return False
        else:
            self.device.set_ch_param(slot, [channel], "Pw", True)
        return True

    def power_off(self, slot: int, channel: int) -> bool:
        """Power Channel Off"""
        if self.device is None:
            return False
        else:
            self.device.set_ch_param(slot, [channel], "Pw", False)
        return True

    def read_status(self, slot: int, channel: int) -> dict:
        """Read status from a specific slot and channel."""
        if self.device is None:
            return {}
        else:
            [status] = self.device.get_ch_param(slot, [channel], "Status")
        return {
            "is_on": (status & (1 << 0)) != 0,
            "ramping_up": (status & (1 << 1)) != 0,
            "ramping_down": (status & (1 << 2)) != 0,
            "overcurrent": (status & (1 << 3)) != 0,
            "overvoltage": (status & (1 << 4)) != 0,
            "undervoltage": (status & (1 << 5)) != 0,
            "external_trip": (status & (1 << 6)) != 0,
            "HVMax": (status & (1 << 7)) != 0,
        }

    def power_all_on(self, channels: list[dict]) -> bool:
        """Power on multiple channels simultaneously."""
        if self.device is None:
            self.connect()
        for ch in channels:
            self.power_on(ch["slot"], ch["channel"])
        return True

    def power_all_off(self, channels: list[dict]) -> bool:
        """Power off multiple channels simultaneously."""
        if self.device is None:
            self.connect()
        for ch in channels:
            self.power_off(ch["slot"], ch["channel"])
        return True

    def set_all_voltages(self, channels: list[dict]) -> bool:
        """Set multiple channels simultaneously."""
        if self.device is None:
            self.connect()
        for ch in channels:
            self.set_voltage(ch["slot"], ch["channel"], ch["voltage"])
        return True

    def read_all_channels(self, channel_config: list[dict]) -> list[dict]:
        """Read voltage and current from multiple channels."""
        results = []
        if self.device is None:
            self.connect()
        for ch in channel_config:
            results.append({
                "slot": ch["slot"],
                "channel": ch["channel"],
                "voltage": self.read_voltage(ch["slot"], ch["channel"]),
                "current": self.read_current(ch["slot"], ch["channel"]),
                "status": self.read_status(ch["slot"], ch["channel"]),
                "timestamp": datetime.now(UTC).isoformat(),
            })
        return results