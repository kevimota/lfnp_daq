import asyncio
import logging
from datetime import datetime, UTC

from fastapi import APIRouter

from ..core.data_writer import DataWriter
from ..core.db import get_session, DAQRuns
from ..core.fsm import DAQFSM
from ..core.websocket import DataBroadcaster
from ..scans import CurrentScanner

_LOG = logging.getLogger("daq.api")


class ScanContext:
    fsm: DAQFSM
    power_interface: object
    scanner: CurrentScanner
    task: asyncio.Task
    broadcaster: DataBroadcaster

    def __init__(
        self,
        fsm: DAQFSM,
        power_interface: object,
        scanner: CurrentScanner,
        task: asyncio.Task,
        broadcaster: DataBroadcaster,
    ):
        self.fsm = fsm
        self.power_interface = power_interface
        self.scanner = scanner
        self.task = task
        self.broadcaster = broadcaster


data_writer = DataWriter()
scan_manager: dict[int, ScanContext] = {}

router = APIRouter()


async def _background_scan(config: dict, run_id: int, ps_row) -> None:
    """Run scan in background and clean up on completion."""
    ctx = scan_manager.get(run_id)
    if ctx is None:
        return

    _LOG.info("run=%s: background scan started (type=%s)", run_id, config.get("type"))

    try:
        result = await ctx.scanner.run_current_scan(config, run_id)
        with get_session() as session:
            run = session.get(DAQRuns, run_id)
            if run:
                if result.get("success"):
                    run.status = "finished"
                elif result.get("error") == "Stopped by user":
                    run.status = "stopped"
                else:
                    run.status = "failed"
                run.stopped_at = datetime.now(UTC)
                if result.get("data_path"):
                    run.data_path = result["data_path"]
                session.add(run)
                session.commit()
        _LOG.info(
            "run=%s: background scan done success=%s error=%s",
            run_id,
            result.get("success"),
            result.get("error"),
        )
    except Exception as e:
        fsm = ctx.fsm
        fsm.fail(str(e))
        with get_session() as session:
            run = session.get(DAQRuns, run_id)
            if run:
                run.status = "failed"
                run.stopped_at = datetime.now(UTC)
                session.add(run)
                session.commit()
        _LOG.exception("run=%s: background scan failed: %s", run_id, e)
    finally:
        scan_manager.pop(run_id, None)


# Sub-routers (imported at the end to avoid circular imports)
from .runs import router as runs_router  # noqa: E402
from .files import router as files_router  # noqa: E402
from .digitizers import router as digitizers_router  # noqa: E402

router.include_router(runs_router)
router.include_router(files_router)
router.include_router(digitizers_router)