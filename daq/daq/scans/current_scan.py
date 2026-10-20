import asyncio
import logging
from datetime import datetime, UTC

from ..core.fsm import DAQFSM, DAQState
from ..core.data_writer import DataWriter
from ..core.websocket import DataBroadcaster
from ..hardware import CaenPSInterface, is_not_present_error

_LOG = logging.getLogger("daq.scan")


class CurrentScanner:
    #: Delay between retries when the CAEN mainframe reports a transient
    #: "board/slot not present" error.
    hv_retry_seconds = 10
    #: Interval between ramp-status polls while configuring a point.
    hv_ramp_poll_seconds = 5
    #: Number of attempts made to apply the final HV state during cleanup.
    hv_cleanup_attempts = 3

    def __init__(
        self,
        fsm: DAQFSM,
        power_interface: CaenPSInterface,
        data_writer: DataWriter,
        broadcaster: DataBroadcaster,
    ):
        self.fsm = fsm
        self.power = power_interface
        self.data_writer = data_writer
        self.broadcaster = broadcaster
        self._stop_requested = False

    async def _wait_for_resume(self):
        while self.fsm.state == DAQState.PAUSED and not self._stop_requested:
            await asyncio.sleep(0.5)

    def _hv_disconnect(self):
        """Best-effort HV disconnect; never raises."""
        try:
            self.power.disconnect()
        except Exception:
            pass

    def _log_hv_not_present(self, message: str) -> None:
        """Record a not-present warning in the run log (daq.log)."""
        self.fsm.log_warning(message)

    async def _hv_config_call(
        self, fn, *args, run_id: int, point_index: int, description: str
    ):
        """Run a configuration-phase HV call, retrying transient not-present
        errors until it succeeds or the scan is stopped.

        Returns the call's result, or None if the scan was stopped.
        """
        while not self._stop_requested:
            try:
                return fn(*args)
            except Exception as e:
                if not is_not_present_error(e):
                    raise
                _LOG.warning(
                    "run=%s point=%s: CAEN HV not present while %s: %s; "
                    "retrying in %ss",
                    run_id,
                    point_index + 1,
                    description,
                    e,
                    self.hv_retry_seconds,
                )
                self._log_hv_not_present(
                    f"CAEN HV not present while {description} "
                    f"(point {point_index + 1}): {e}; "
                    f"retrying in {self.hv_retry_seconds}s"
                )
                self._hv_disconnect()
                await asyncio.sleep(self.hv_retry_seconds)
        return None

    async def _configure_point(
        self, point_config: list, run_id: int, point_index: int
    ) -> bool:
        """Apply the point voltages and wait for the ramp to settle.

        Transient "board not present" errors are retried (re-applying the
        full configuration) until the board responds or the scan is stopped.
        """
        while not self._stop_requested:
            if not await self._hv_config_call(
                self.power.set_all_voltages,
                point_config,
                run_id=run_id,
                point_index=point_index,
                description="configuring voltages",
            ):
                return False

            configured = False
            while not configured and not self._stop_requested:
                await asyncio.sleep(self.hv_ramp_poll_seconds)
                if self.fsm.state == DAQState.PAUSED:
                    await self._wait_for_resume()
                    if self._stop_requested:
                        return False
                    # Re-apply the configuration after resuming.
                    break
                try:
                    configured = True
                    for item in point_config:
                        status = self.power.read_status(item["slot"], item["channel"])
                        if status["ramping_up"] or status["ramping_down"]:
                            configured = False
                            break
                except Exception as e:
                    if not is_not_present_error(e):
                        raise
                    _LOG.warning(
                        "run=%s point=%s: CAEN HV not present while waiting "
                        "for ramp: %s; reconfiguring in %ss",
                        run_id,
                        point_index + 1,
                        e,
                        self.hv_retry_seconds,
                    )
                    self._log_hv_not_present(
                        f"CAEN HV not present while waiting for ramp "
                        f"(point {point_index + 1}): {e}; "
                        f"reconfiguring in {self.hv_retry_seconds}s"
                    )
                    self._hv_disconnect()
                    await asyncio.sleep(self.hv_retry_seconds)
                    break

            if configured:
                return True
        return False

    async def _apply_final_state(
        self, point_config: list, end_voltage: float, run_id: int
    ) -> None:
        """Apply the desired final HV state, retrying transient not-present
        errors up to ``hv_cleanup_attempts`` times."""
        for attempt in range(1, self.hv_cleanup_attempts + 1):
            try:
                if end_voltage < 100:
                    for item in point_config:
                        item["voltage"] = 15
                    self.power.set_all_voltages(point_config)
                    self.power.power_all_off(point_config)
                else:
                    for item in point_config:
                        item["voltage"] = end_voltage
                    self.power.set_all_voltages(point_config)
                _LOG.info("run=%s: desired final HV state applied", run_id)
                return
            except Exception as e:
                if not is_not_present_error(e):
                    _LOG.warning(
                        "run=%s: failed to set final HV state: %s", run_id, e
                    )
                    return
                _LOG.warning(
                    "run=%s: CAEN HV not present while setting final state "
                    "(attempt %s/%s): %s",
                    run_id,
                    attempt,
                    self.hv_cleanup_attempts,
                    e,
                )
                self._log_hv_not_present(
                    f"CAEN HV not present while setting final state "
                    f"(attempt {attempt}/{self.hv_cleanup_attempts}): {e}"
                )
                if attempt < self.hv_cleanup_attempts:
                    self._hv_disconnect()
                    await asyncio.sleep(self.hv_retry_seconds)
        _LOG.warning(
            "run=%s: could not reach desired final HV state after %s attempts",
            run_id,
            self.hv_cleanup_attempts,
        )
        self._log_hv_not_present(
            f"could not reach desired final HV state after "
            f"{self.hv_cleanup_attempts} attempts"
        )

    async def run_current_scan(self, config: dict, run_id: int) -> dict:
        voltage_points = config.get("voltage_points", [])
        wait_time = config.get("wait_time_seconds", 30)
        end_voltage = config.get("end_voltage", 0)

        if not voltage_points:
            self.fsm.fail("No voltage points defined")
            return {"success": False, "error": "No voltage points defined"}

        run_dir = self.data_writer.create_run_directory(run_id)
        self.data_writer.write_configuration(config, run_dir)

        if not self.fsm.start(config, run_id):
            return {"success": False, "error": "Failed to start scan"}

        try:
            await self._prepare(config, run_id)
            if not await self._hv_config_call(
                self.power.power_all_on,
                voltage_points[0],
                run_id=run_id,
                point_index=0,
                description="powering on channels",
            ):
                if self._stop_requested:
                    return {"success": False, "error": "Stopped by user"}
                return {"success": False, "error": "Could not power on HV channels"}

            for point_index, point_config in enumerate(voltage_points):
                if self._stop_requested:
                    break

                # ── Configure / ramp ──
                if not await self._configure_point(point_config, run_id, point_index):
                    if self._stop_requested:
                        break
                    continue

                if self._stop_requested:
                    break

                # ── Wait ──
                self.fsm.to_waiting()
                if wait_time > 15:
                    self.power.disconnect()
                waited = 0
                while waited < wait_time and not self._stop_requested:
                    await asyncio.sleep(1)
                    waited += 1
                    if self.fsm.state == DAQState.PAUSED:
                        await self._wait_for_resume()
                        if self._stop_requested:
                            break
                        self.fsm.to_waiting()
                        waited = 0

                if self._stop_requested:
                    break

                # ── Record ──
                await self._record_point(run_dir, point_index, point_config, config)

                if self._stop_requested:
                    break

                self.fsm.configure_next_point()

            if self.fsm.state == DAQState.FINISHED:
                return {"success": True, "run_id": run_id, "data_path": run_dir}
            return {"success": False, "error": "Stopped by user" if self._stop_requested else "Unexpected state"}

        except Exception as e:
            self.fsm.fail(str(e))
            return {"success": False, "error": str(e)}
        finally:
            try:
                await self._cleanup()
            except Exception as e:
                _LOG.warning("run=%s: cleanup failed: %s", run_id, e)
            if voltage_points:
                await self._apply_final_state(
                    voltage_points[0], end_voltage, run_id
                )
            self._hv_disconnect()
            self._stop_requested = False

    async def _prepare(self, config: dict, run_id: int):
        """Hook run after the FSM has started; any exception is recorded as a scan failure."""
        pass

    async def _cleanup(self):
        """Hook run in the finally block; release any hardware opened in _prepare."""
        pass

    async def _record_point(self, run_dir: str, point_index: int, point_config: list, config: dict):
        """Sample power data for the current point until number_of_samples is reached."""
        number_of_samples = config.get("number_of_samples", 60)
        sample_interval = config.get("sample_interval_seconds", 1)
        run_id = self.fsm.run_id or 0

        all_channels = list({(ch["slot"], ch["channel"]) for ch in point_config})
        channel_list = [{"slot": s, "channel": c} for s, c in all_channels]

        self.data_writer.start_point_data(run_dir, point_index)
        self.fsm.to_recording()
        samples_recorded = 0

        while samples_recorded < number_of_samples and not self._stop_requested:
            while self.fsm.state == DAQState.PAUSED and not self._stop_requested:
                await asyncio.sleep(0.5)
            if self._stop_requested:
                break

            if self.fsm.state != DAQState.RECORDING:
                self.data_writer.start_point_data(run_dir, point_index)
                self.fsm.to_recording()
                samples_recorded = 0

            try:
                readings = self.power.read_all_channels(channel_list)
            except Exception as e:
                if not is_not_present_error(e):
                    raise
                _LOG.warning(
                    "run=%s point=%s: CAEN HV not present while recording: %s; "
                    "skipping sample",
                    run_id,
                    point_index + 1,
                    e,
                )
                self._log_hv_not_present(
                    f"CAEN HV not present while recording "
                    f"(point {point_index + 1}): {e}; skipping sample"
                )
                self._hv_disconnect()
                await asyncio.sleep(sample_interval)
                continue

            for reading in readings:
                self.data_writer.write_power_data(reading, run_dir, point_index)

            await self.broadcaster.broadcast({
                "type": "current_scan",
                "point": point_index + 1,
                "data": readings,
                "timestamp": datetime.now(UTC).isoformat(),
            })

            samples_recorded += 1
            await asyncio.sleep(sample_interval)

    def stop(self):
        self._stop_requested = True
        self.fsm.stop()