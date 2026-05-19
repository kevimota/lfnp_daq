import os
import json
import csv
from datetime import datetime, UTC


class DataWriter:
    def __init__(self, data_path: str = "/data/daq/raw"):
        self.data_path = data_path
        os.makedirs(self.data_path, exist_ok=True)

    def create_run_directory(self, run_id: int) -> str:
        run_dir = os.path.join(self.data_path, f"run_{run_id}")
        os.makedirs(run_dir, exist_ok=True)
        return run_dir

    def write_configuration(self, config: dict, run_dir: str):
        config_path = os.path.join(run_dir, "configuration.json")
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)

    def start_point_data(self, run_dir: str, point_index: int):
        csv_path = os.path.join(run_dir, f"power_data_point_{point_index}.csv")
        if os.path.isfile(csv_path):
            os.remove(csv_path)

    def write_power_data(self, data: dict, run_dir: str, point_index: int):
        csv_path = os.path.join(run_dir, f"power_data_point_{point_index}.csv")
        file_exists = os.path.isfile(csv_path)

        with open(csv_path, "a", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow([
                    "timestamp", "slot", "channel",
                    "voltage", "current",
                    "enabled", "overcurrent", "overvoltage",
                ])

            status = data.get("status", {})
            writer.writerow([
                data.get("timestamp", datetime.now(UTC).isoformat()),
                data.get("slot", 0),
                data.get("channel", 0),
                data.get("voltage", 0.0),
                data.get("current", 0.0),
                status.get("enabled", False),
                status.get("overcurrent", False),
                status.get("overvoltage", False),
            ])