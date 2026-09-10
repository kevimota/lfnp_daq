from fastapi import APIRouter

from ..hardware.digitizer_interface import test_connection_by_id

router = APIRouter()


@router.post("/digitizers/test")
async def test_digitizer(req: dict):
    return test_connection_by_id(int(req["digitizer_id"]))


@router.post("/digitizers/scan")
async def scan_digitizers(req: dict):
    from ..hardware.digitizer_interface import enumerate_digitizers

    devices = enumerate_digitizers(
        connection_type=int(req.get("connection_type", 0)),
        conet_node=int(req.get("conet_node", 0)),
        vme_base_address=int(req.get("vme_base_address", 0)),
    )
    return {"devices": devices}