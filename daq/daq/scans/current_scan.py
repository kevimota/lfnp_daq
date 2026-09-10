import asyncio
from datetime import datetime, UTC

from ..core.fsm import DAQFSM, DAQState
from ..core.data_writer import DataWriter
from ..core.websocket import DataBroadcaster
from ..hardware import CaenPSInterface


class CurrentScanner:
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
            self.power.power_all_on(voltage_points[0])

            for point_index, point_config in enumerate(voltage_points):
                if self._stop_requested:
                    break

                # ── Configure / ramp ──
                self.power.set_all_voltages(point_config)
                configured = False
                while not configured and not self._stop_requested:
                    await asyncio.sleep(5)
                    for item in point_config:
                        status = self.power.read_status(item["slot"], item["channel"])
                        if not (status["ramping_up"] or status["ramping_down"]):
                            configured = True
                    if self.fsm.state == DAQState.PAUSED:
                        await self._wait_for_resume()
                        if self._stop_requested:
                            break
                        self.power.set_all_voltages(point_config)
                        configured = False

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
            await self._cleanup()
            if voltage_points:
                if end_voltage < 100:
                    for item in voltage_points[0]:
                        item['voltage'] = 15
                    self.power.set_all_voltages(voltage_points[0])
                    self.power.power_all_off(voltage_points[0])
                else:
                    for item in voltage_points[0]:
                        item['voltage'] = end_voltage
                    self.power.set_all_voltages(voltage_points[0])
            self.power.disconnect()
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

            readings = self.power.read_all_channels(channel_list)

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