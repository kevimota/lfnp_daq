import asyncio
import logging
import os
import time
from datetime import datetime, UTC

from ..core.fsm import DAQFSM, DAQState
from ..hardware import DigitizerScanner
from .current_scan import CurrentScanner

_LOG = logging.getLogger("daq.scan")


class DigitizerScan(CurrentScanner):
    """Digitizer acquisition scan, optionally combined with HV control.

    When the selected points contain HV channels the parent's HV logic is
    used (ramp, wait, power sampling); when they do not, the power supply is
    left untouched and only waveforms are acquired.

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

    async def run_current_scan(
        self,
        config: dict,
        run_id: int,
        run_points: list[int] | None = None,
        mode: str = "start",
    ) -> dict:
        return await super().run_current_scan(
            config, run_id, run_points=run_points, mode=mode
        )

    async def _prepare(self, config: dict, run_id: int):
        await self.digitizer.open()
        driver = self.digitizer.driver
        _LOG.info(
            "run=%s: digitizer board %s (model=%s, %s groups x %s channels = %s total, "
            "drs4=%s sam=%s)",
            run_id,
            getattr(self.digitizer.info, "model_name", "?"),
            int(driver.model),
            driver.n_groups,
            driver.channels_per_group,
            driver.n_total,
            driver.drs4,
            driver.sam,
        )
        await self.digitizer.configure(config)

    async def _cleanup(self):
        await self.digitizer.close()

    def _power_enabled(
        self, voltage_points: list, run_points: list[int]
    ) -> bool:
        """Digitizer scans may run with no HV channels at all."""
        if self.power is None:
            return False
        return any(voltage_points[i] for i in run_points)

    def _digitizer_payload(self) -> dict:
        """Build the JSON-serializable digitizer section of a live broadcast."""
        return {
            "channels": {
                f"ch{ch}": (
                    self.digitizer._waveforms[-1][i].tolist()
                    if self.digitizer._waveforms
                    and self.digitizer._waveforms[-1]
                    and i < len(self.digitizer._waveforms[-1])
                    and self.digitizer._waveforms[-1][i] is not None
                    else None
                )
                for i, ch in enumerate(self.digitizer.enabled_channels)
            },
            "tr0": (
                {
                    "tr0_g0": (
                        self.digitizer._tr0_waveforms[-1][0].tolist()
                        if self.digitizer._tr0_waveforms
                        and self.digitizer._tr0_waveforms[-1]
                        and len(self.digitizer._tr0_waveforms[-1]) > 0
                        and self.digitizer._tr0_waveforms[-1][0] is not None
                        else None
                    ),
                    "tr0_g1": (
                        self.digitizer._tr0_waveforms[-1][1].tolist()
                        if self.digitizer._tr0_waveforms
                        and self.digitizer._tr0_waveforms[-1]
                        and len(self.digitizer._tr0_waveforms[-1]) > 1
                        and self.digitizer._tr0_waveforms[-1][1] is not None
                        else None
                    ),
                }
                if getattr(self.digitizer.driver, "fast_trigger_digitizing", False)
                else None
            ),
        }

    async def _record_point(self, run_dir: str, point_index: int, point_config: list, config: dict):
        sample_interval = config.get("sample_interval_seconds", 1)

        all_channels = list({(ch["slot"], ch["channel"]) for ch in point_config})
        channel_list = [{"slot": s, "channel": c} for s, c in all_channels]
        has_power = bool(channel_list) and self.power is not None

        target = int(config.get("number_of_triggers", 0))
        run_id = self.fsm.run_id or 0

        self.data_writer.start_point_data(run_dir, point_index)
        self.fsm.to_recording()
        await self.digitizer.begin_point(run_dir, point_index, config, point_config, run_id)

        samples_recorded = 0
        last_sample_time = None
        last_readings = None
        done = target <= 0

        point_started = time.monotonic()
        _LV_LOG_SECONDS = 5.0
        _STALL_LOG_SECONDS = 30.0

        async def _liveness():
            cnt = self.digitizer.collected
            changed_at = point_started
            while not self._stop_requested:
                await asyncio.sleep(_LV_LOG_SECONDS)
                new_cnt = self.digitizer.collected
                now = time.monotonic()
                if new_cnt != cnt:
                    cnt = new_cnt
                    changed_at = now
                stale = now - changed_at
                _LOG.info(
                    "run=%s point=%s: waiting… physical=%s/%s random=%s total=%s elapsed=%.0fs",
                    run_id,
                    point_index,
                    self.digitizer.goal_collected,
                    target,
                    self.digitizer.random_collected,
                    cnt,
                    now - point_started,
                )
                if stale > _STALL_LOG_SECONDS and self.fsm.state == DAQState.RECORDING:
                    _LOG.warning(
                        "run=%s point=%s: no new events for %.0fs "
                        "(physical=%s/%s random=%s) - send_sw_trigger/read_data likely blocked",
                        run_id,
                        point_index,
                        stale,
                        self.digitizer.goal_collected,
                        target,
                        self.digitizer.random_collected,
                    )

        liveness = asyncio.create_task(_liveness())
        try:
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
                    last_readings = None
                    await self.digitizer.begin_point(run_dir, point_index, config, point_config, run_id)

                now = time.monotonic()
                if last_sample_time is None or now - last_sample_time >= sample_interval:
                    readings = None
                    if has_power:
                        readings = await self._read_hv_sample(
                            channel_list, run_id, point_index
                        )
                        if readings is not None:
                            for reading in readings:
                                self.data_writer.write_power_data(
                                    reading, run_dir, point_index
                                )
                            last_readings = readings

                    data = {"digitizer": self._digitizer_payload()}
                    if readings:
                        data["power"] = readings
                    elif last_readings:
                        # Keep the last good readings visible during a
                        # transient read failure instead of hiding the table.
                        data["power"] = last_readings
                    await self.broadcaster.broadcast({
                        "type": "digitizer_scan",
                        "point": point_index + 1,
                        "data": data,
                        "triggers": self.digitizer.goal_collected,
                        "target": target,
                        "random_triggers": self.digitizer.random_collected,
                        "total_triggers": self.digitizer.collected,
                        "count_physical_only": self.digitizer.count_physical_only,
                        "timestamp": datetime.now(UTC).isoformat(),
                    })

                    if not has_power or readings is not None:
                        samples_recorded += 1
                    last_sample_time = now

                progress = await self.digitizer.step()
                done = target > 0 and progress["collected"] >= target
                if done:
                    # Send a definitive final status over the websocket before
                    # the point finishes. Without this, the live-data card is
                    # left showing the last power-sample broadcast, which is
                    # typically one trigger short of the target.
                    try:
                        final_data = {"digitizer": self._digitizer_payload()}
                        if last_readings:
                            final_data["power"] = last_readings
                        await self.broadcaster.broadcast({
                            "type": "digitizer_scan",
                            "point": point_index + 1,
                            "data": final_data,
                            "triggers": self.digitizer.goal_collected,
                            "target": target,
                            "random_triggers": self.digitizer.random_collected,
                            "total_triggers": self.digitizer.collected,
                            "count_physical_only": self.digitizer.count_physical_only,
                            "timestamp": datetime.now(UTC).isoformat(),
                        })
                    except Exception as e:
                        _LOG.warning(
                            "run=%s point=%s: failed to send final live-data broadcast: %s",
                            self.fsm.run_id,
                            point_index,
                            e,
                        )
                    _LOG.info(
                        "run=%s point=%s: target reached (%s/%s triggers)",
                        self.fsm.run_id,
                        point_index,
                        progress["collected"],
                        target,
                    )
                    break
                await asyncio.sleep(0.02)
        finally:
            liveness.cancel()
            await asyncio.gather(liveness, return_exceptions=True)
            await self.digitizer.end_point()

        root_name = f"digitizer_point_{point_index}.root"
        csv_name = f"power_data_point_{point_index}.csv"
        if done and os.path.isfile(os.path.join(run_dir, root_name)):
            data_files = [root_name]
            if os.path.isfile(os.path.join(run_dir, csv_name)):
                data_files.append(csv_name)
            self.data_writer.mark_point_completed(
                run_dir,
                point_index,
                voltages=point_config,
                data_files=data_files,
                samples=samples_recorded,
                triggers=self.digitizer.goal_collected,
                target=target,
                random_triggers=self.digitizer.random_collected,
                total_triggers=self.digitizer.collected,
                count_physical_only=self.digitizer.count_physical_only,
            )