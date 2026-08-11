from .current_scan import CurrentScanner
from .digitizer_scan import DigitizerScan

SCAN_TYPES = {
    "hv_scan": CurrentScanner,
    "digitizer_scan": DigitizerScan,
}

__all__ = ["CurrentScanner", "DigitizerScan", "SCAN_TYPES"]