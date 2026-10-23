import os
import json
import csv
import logging
from datetime import datetime, UTC

_LOG = logging.getLogger("daq.data_writer")

PROGRESS_FILENAME = "progress.json"


def _now() -> str:
    return datetime.now(UTC).isoformat()


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

    # ── progress manifest ──────────────────────────────────────

    def progress_path(self, run_dir: str) -> str:
        return os.path.join(run_dir, PROGRESS_FILENAME)

    def read_progress(self, run_dir: str) -> dict:
        """Read the per-run progress manifest, returning an empty one if absent."""
        path = self.progress_path(run_dir)
        if not os.path.isfile(path):
            return {"version": 1, "run_id": None, "points": {}}
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {"version": 1, "run_id": None, "points": {}}
        if not isinstance(data, dict):
            return {"version": 1, "run_id": None, "points": {}}
        if not isinstance(data.get("points"), dict):
            data["points"] = {}
        data.setdefault("version", 1)
        return data

    def _write_progress(self, run_dir: str, progress: dict) -> None:
        os.makedirs(run_dir, exist_ok=True)
        progress["updated_at"] = _now()
        path = self.progress_path(run_dir)
        tmp_path = f"{path}.tmp"
        try:
            with open(tmp_path, "w") as f:
                json.dump(progress, f, indent=2)
            os.replace(tmp_path, path)
        except OSError as e:
            _LOG.warning("failed to write progress manifest %s: %s", path, e)

    def start_run_progress(
        self,
        run_dir: str,
        run_id: int,
        points: list[int],
        mode: str,
        start_point: int | None = None,
    ) -> None:
        progress = self.read_progress(run_dir)
        progress["version"] = 1
        progress["run_id"] = run_id
        progress["last_run"] = {
            "mode": mode,
            "points": list(points),
            "start_point": start_point,
            "started_at": _now(),
        }
        self._write_progress(run_dir, progress)

    def start_point_progress(
        self, run_dir: str, point_index: int, voltages: list[dict]
    ) -> None:
        progress = self.read_progress(run_dir)
        progress["points"][str(point_index)] = {
            "status": "running",
            "started_at": _now(),
            "voltages": [dict(item) for item in voltages],
        }
        self._write_progress(run_dir, progress)

    def mark_point_completed(
        self,
        run_dir: str,
        point_index: int,
        *,
        voltages: list[dict],
        data_files: list[str] | None = None,
        samples: int | None = None,
        triggers: int | None = None,
        target: int | None = None,
    ) -> None:
        progress = self.read_progress(run_dir)
        key = str(point_index)
        entry = progress["points"].get(key, {})
        entry.update({
            "status": "completed",
            "completed_at": _now(),
            "voltages": [dict(item) for item in voltages],
        })
        if data_files is not None:
            entry["data_files"] = list(data_files)
        if samples is not None:
            entry["samples"] = samples
        if triggers is not None:
            entry["triggers"] = triggers
        if target is not None:
            entry["target"] = target
        progress["points"][key] = entry
        self._write_progress(run_dir, progress)

    def finish_run_progress(self, run_dir: str, result: str) -> None:
        progress = self.read_progress(run_dir)
        last_run = progress.get("last_run")
        if isinstance(last_run, dict):
            last_run["finished_at"] = _now()
            last_run["result"] = result
            progress["last_run"] = last_run
        self._write_progress(run_dir, progress)

    def write_power_data(self, data: dict, run_dir: str, point_index: int):
        csv_path = os.path.join(run_dir, f"power_data_point_{point_index}.csv")
        file_exists = os.path.isfile(csv_path)

        with open(csv_path, "a", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow([
                    "timestamp", "slot", "channel",
                    "voltage", "current",
                    "is_on", "overcurrent", "overvoltage",
                ])

            status = data.get("status", {})
            writer.writerow([
                data.get("timestamp", datetime.now(UTC).isoformat()),
                data.get("slot", 0),
                data.get("channel", 0),
                data.get("voltage", 0.0),
                data.get("current", 0.0),
                status.get("is_on", False),
                status.get("overcurrent", False),
                status.get("overvoltage", False),
            ])