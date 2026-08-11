import asyncio
import time
from datetime import datetime, UTC

from ..core.fsm import DAQFSM, DAQState
from ..hardware import DigitizerScanner
from .current_scan import CurrentScanner


class DigitizerScan(CurrentScanner):
    """HV scan that also acquires digitizer waveforms during the RECORDING phase.

    The record phase is trigger-driven: it lasts until the configured number of
    triggers/waveforms is captured (or a safety timeout), while power data keeps
    being sampled on the sample interval during that window.
    """

    def __init__(
        self,
        fsm: DAQFSM,
        power_interface,
        data_writer,
        broadcaster,
        digitizer: DigitizerScanner,
    ):
        super().__init__(fsm, power_interface, data_writer, broadcaster)
        self.digitizer = digitizer

    async def run_current_scan(self, config: dict, run_id: int) -> dict:
        self.digitizer.open()
        self.digitizer.configure(config)
        try:
            return await super().run_current_scan(config, run_id)
        finally:
            self.digitizer.close()

    async def _record_point(self, run_dir: str, point_index: int, point_config: list, config: dict):
        sample_interval = config.get("sample_interval_seconds", 1)

        all_channels = list({(ch["slot"], ch["channel"]) for ch in point_config})
        channel_list = [{"slot": s, "channel": c} for s, c in all_channels]

        target = int(config.get("number_of_triggers", 0))
        run_id = self.fsm.run_id or 0

        self.data_writer.start_point_data(run_dir, point_index)
        self.fsm.to_recording()
        self.digitizer.begin_point(run_dir, point_index, config, point_config, run_id)

        samples_recorded = 0
        last_sample_time = None
        done = target <= 0

        while not done and not self._stop_requested:
            while self.fsm.state == DAQState.PAUSED and not self._stop_requested:
                await asyncio.sleep(0.5)
            if self._stop_requested:
                break

            if self.fsm.state != DAQState.RECORDING:
                # Resumed: redo the current point from scratch.
                self.data_writer.start_point_data(run_dir, point_index)
                self.fsm.to_recording()
                samples_recorded = 0
                last_sample_time = None
                self.digitizer.begin_point(run_dir, point_index, config, point_config, run_id)

            now = time.monotonic()
            if last_sample_time is None or now - last_sample_time >= sample_interval:
                readings = self.power.read_all_channels(channel_list)

                for reading in readings:
                    self.data_writer.write_power_data(reading, run_dir, point_index)

                await self.broadcaster.broadcast({
                    "type": "current_scan",
                    "point": point_index + 1,
                    "data": readings,
                    "digi_triggers": self.digitizer.collected,
                    "digi_target": target,
                    "timestamp": datetime.now(UTC).isoformat(),
                })

                samples_recorded += 1
                last_sample_time = now

            progress = await self.digitizer.step()
            done = target > 0 and (progress["collected"] >= target or progress["timed_out"])
            if done:
                break
            await asyncio.sleep(0.02)

        await self.digitizer.end_point()