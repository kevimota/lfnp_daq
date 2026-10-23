from enum import Enum
from typing import Optional, TextIO
from datetime import datetime, UTC
import os


class DAQState(str, Enum):
    INITIALIZING = "initializing"
    HALTED = "halted"
    CONFIGURING = "configuring"
    WAITING = "waiting"
    RECORDING = "recording"
    PAUSED = "paused"
    FINISHED = "finished"
    FAILED = "failed"


class DAQFSM:
    def __init__(self, data_path: str = "/data/daq/raw"):
        self._state = DAQState.INITIALIZING
        self._configuration: Optional[dict] = None
        self._run_id: Optional[int] = None
        self._run_dir: Optional[str] = None
        self._current_point_index: int = 0
        self._run_points: list[int] = []
        self._run_position: int = 0
        self._log_file: Optional[TextIO] = None
        self._data_path = data_path

        os.makedirs(self._data_path, exist_ok=True)

    @property
    def state(self) -> DAQState:
        return self._state

    def get_state(self) -> str:
        return self._state.value

    @property
    def run_id(self) -> Optional[int]:
        return self._run_id

    @property
    def current_point_index(self) -> int:
        return self._current_point_index

    @property
    def total_points(self) -> int:
        if self._configuration and "voltage_points" in self._configuration:
            return len(self._configuration["voltage_points"])
        return 0

    def _set_state(self, new_state: DAQState):
        old_state = self._state
        self._state = new_state
        self.log_event(f"{old_state.value} \u2192 {new_state.value}")

    def initialize(self):
        self._set_state(DAQState.HALTED)

    def log_event(self, event: str):
        if self._log_file:
            timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
            self._log_file.write(f"{timestamp} | {event}\n")
            self._log_file.flush()

    def log_warning(self, message: str):
        """Write a warning to the run log (daq.log).

        Unlike :meth:`log_event`, this also records the warning when the
        run log has already been closed (e.g. during post-run cleanup).
        """
        event = f"WARNING: {message}"
        if self._log_file:
            timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
            self._log_file.write(f"{timestamp} | {event}\n")
            self._log_file.flush()
            return
        if not self._run_dir:
            return
        os.makedirs(self._run_dir, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        with open(os.path.join(self._run_dir, "daq.log"), "a") as log_file:
            log_file.write(f"{timestamp} | {event}\n")

    def start(
        self,
        config: dict,
        run_id: int,
        run_points: list[int] | None = None,
        mode: str = "start",
    ) -> bool:
        if self._state != DAQState.HALTED:
            return False

        self._run_id = run_id
        self._configuration = config
        total = len(config.get("voltage_points", []))
        self._run_points = list(run_points) if run_points else list(range(total))
        self._run_position = 0
        self._current_point_index = self._run_points[0] if self._run_points else 0

        self._run_dir = os.path.join(self._data_path, f"run_{run_id}")
        os.makedirs(self._run_dir, exist_ok=True)

        log_path = os.path.join(self._run_dir, "daq.log")
        self._log_file = open(log_path, "a")

        self.log_event(f"Run {run_id} started")
        if mode == "resume":
            self.log_event(f"Resuming from point {self._current_point_index + 1}")
        elif mode == "redo":
            selected = ", ".join(str(i + 1) for i in self._run_points)
            self.log_event(f"Redoing points: {selected}")
        elif self._current_point_index > 0:
            self.log_event(f"Starting from point {self._current_point_index + 1}")

        self._set_state(DAQState.CONFIGURING)
        self.log_event(f"Configuring Point {self._current_point_index + 1}")
        return True

    def configure_next_point(self) -> bool:
        if self._state != DAQState.RECORDING:
            return False

        self._run_position += 1

        if self._run_position >= len(self._run_points):
            full_run = len(self._run_points) == self.total_points
            self._set_state(DAQState.FINISHED)
            self.log_event(
                "All points completed" if full_run else "Selected points completed"
            )
            self._close_log()
            return True

        self._current_point_index = self._run_points[self._run_position]
        self.log_event(f"Configuring Point {self._current_point_index + 1}")
        self._set_state(DAQState.CONFIGURING)
        return True

    def to_waiting(self) -> bool:
        if self._state != DAQState.CONFIGURING:
            return False

        point_num = self._current_point_index + 1
        wait_time = self._configuration.get("wait_time_seconds", 0)
        self.log_event(f"Point {point_num} configured, waiting {wait_time}s")
        self._set_state(DAQState.WAITING)
        return True

    def to_recording(self) -> bool:
        if self._state != DAQState.WAITING:
            return False

        point_num = self._current_point_index + 1
        self.log_event(f"Recording started (point {point_num})")
        self._set_state(DAQState.RECORDING)
        return True

    def pause(self) -> bool:
        if self._state not in [DAQState.RECORDING, DAQState.WAITING, DAQState.CONFIGURING]:
            return False

        self.log_event("Recording paused (data discarded)")
        self._set_state(DAQState.PAUSED)
        return True

    def resume(self) -> bool:
        if self._state != DAQState.PAUSED:
            return False

        self.log_event("Resuming, redoing current point")
        self._set_state(DAQState.CONFIGURING)
        return True

    def stop(self) -> bool:
        if self._state not in [DAQState.CONFIGURING, DAQState.WAITING, DAQState.RECORDING, DAQState.PAUSED]:
            return False

        self.log_event("Stopped by user")
        self._set_state(DAQState.HALTED)
        self._close_log()
        self._reset_run()
        return True

    def fail(self, error_message: str) -> bool:
        if self._state not in [DAQState.CONFIGURING, DAQState.WAITING, DAQState.RECORDING]:
            return False

        self.log_event(f"FAILED: {error_message}")
        self._set_state(DAQState.FAILED)
        self._close_log()
        return True

    def _close_log(self):
        if self._log_file:
            self._log_file.close()
            self._log_file = None

    def _reset_run(self):
        self._run_id = None
        self._configuration = None
        self._current_point_index = 0
        self._run_points = []
        self._run_position = 0
        self._run_dir = None

    def get_info(self) -> dict:
        return {
            "state": self._state.value,
            "run_id": self._run_id,
            "current_point": self._current_point_index + 1 if self._current_point_index < self.total_points else None,
            "total_points": self.total_points,
            "run_dir": self._run_dir,
        }

    def get_run_dir(self) -> Optional[str]:
        return self._run_dir