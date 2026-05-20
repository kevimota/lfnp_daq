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
    FINISHED = "fisished"
    FAILED = "failed"


class DAQFSM:
    def __init__(self, data_path: str = "/data/daq/raw"):
        self._state = DAQState.INITIALIZING
        self._configuration: Optional[dict] = None
        self._run_id: Optional[int] = None
        self._run_dir: Optional[str] = None
        self._current_point_index: int = 0
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

    def start(self, config: dict, run_id: int) -> bool:
        if self._state != DAQState.HALTED:
            return False

        self._run_id = run_id
        self._configuration = config
        self._current_point_index = 0

        self._run_dir = os.path.join(self._data_path, f"run_{run_id}")
        os.makedirs(self._run_dir, exist_ok=True)

        log_path = os.path.join(self._run_dir, "daq.log")
        self._log_file = open(log_path, "a")

        self.log_event(f"Run {run_id} started")
        self._set_state(DAQState.CONFIGURING)
        return True

    def configure_next_point(self) -> bool:
        if self._state != DAQState.RECORDING:
            return False

        self._current_point_index += 1

        if self._current_point_index >= self.total_points:
            self._set_state(DAQState.FINISHED)
            self.log_event("All points completed")
            self._close_log()
            return True

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
        sample_interval = self._configuration.get("sample_interval_seconds", 1)
        number_of_samples = self._configuration.get("number_of_samples", 60)
        duration = sample_interval*number_of_samples
        
        self.log_event(f"Recording started (point {point_num}, {duration}s)")
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