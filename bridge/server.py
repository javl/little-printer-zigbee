import asyncio
import io
import logging
import pathlib

from aiohttp import web
from PIL import Image

from . import config as cfg_module
from .protocol import prepare_print_job, prepare_print_job_from_pil

STATIC_DIR = pathlib.Path(__file__).parent / "static"

log = logging.getLogger(__name__)


def make_app(bridge, cfg, print_lock: asyncio.Lock) -> web.Application:
    app = web.Application()
    app["bridge"] = bridge
    app["cfg"] = cfg
    app["print_lock"] = print_lock
    app.router.add_get("/", handle_root)
    app.router.add_get("/status", handle_status)
    app.router.add_post("/print", handle_print)
    app.router.add_post("/print-image", handle_print_image)
    return app


async def handle_root(request: web.Request) -> web.Response:
    return web.FileResponse(STATIC_DIR / "index.html")


async def handle_status(request: web.Request) -> web.Response:
    bridge = request.app["bridge"]
    eui64 = next(iter(bridge._addr_map), None)
    return web.json_response({
        "network": "up" if bridge._network_up.is_set() else "down",
        "printer": eui64,
        "busy": request.app["print_lock"].locked(),
    })


async def handle_print(request: web.Request) -> web.Response:
    bridge = request.app["bridge"]
    cfg = request.app["cfg"]
    print_lock: asyncio.Lock = request.app["print_lock"]

    eui64 = next(iter(bridge._addr_map), None)
    if eui64 is None:
        return web.json_response({"error": "no printer connected"}, status=503)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    image = body.get("image")
    text = body.get("text")
    face = bool(body.get("face", False))
    dither = bool(body.get("dither", False))

    if not image and not text:
        return web.json_response({"error": "provide 'image' or 'text'"}, status=400)

    if print_lock.locked():
        return web.json_response({"error": "print in progress"}, status=409)

    async with print_lock:
        print_id = cfg_module.next_print_id(cfg)
        try:
            blocks = prepare_print_job(
                image_path=image, text=text, print_id=print_id, no_face=face, dither=dither
            )
            await bridge.send_print_job(eui64, blocks)
        except Exception as exc:
            log.error("Print failed: %s", exc)
            return web.json_response({"error": str(exc)}, status=500)

    return web.json_response({"status": "ok", "print_id": print_id})


async def handle_print_image(request: web.Request) -> web.Response:
    bridge = request.app["bridge"]
    cfg = request.app["cfg"]
    print_lock: asyncio.Lock = request.app["print_lock"]

    eui64 = next(iter(bridge._addr_map), None)
    if eui64 is None:
        return web.json_response({"error": "no printer connected"}, status=503)

    if print_lock.locked():
        return web.json_response({"error": "print in progress"}, status=409)

    if not request.content_type.startswith("multipart/"):
        return web.json_response({"error": "expected multipart/form-data"}, status=400)

    reader = await request.multipart()
    image_data = None
    dither = False
    field = await reader.next()
    while field is not None:
        if field.name == "image":
            image_data = await field.read()
        elif field.name == "dither":
            val = (await field.read()).decode().strip()
            dither = val.lower() in ("true", "1", "yes")
        field = await reader.next()

    if not image_data:
        return web.json_response({"error": "no image provided"}, status=400)

    async with print_lock:
        print_id = cfg_module.next_print_id(cfg)
        try:
            im = Image.open(io.BytesIO(image_data))
            if im.mode == "RGBA":
                bg = Image.new("L", im.size, 255)
                bg.paste(im.convert("L"), mask=im.split()[3])
                im = bg
            else:
                im = im.convert("L")
            blocks = prepare_print_job_from_pil(im, print_id=print_id, dither=dither)
            await bridge.send_print_job(eui64, blocks)
        except Exception as exc:
            log.error("Print failed: %s", exc)
            return web.json_response({"error": str(exc)}, status=500)

    return web.json_response({"status": "ok", "print_id": print_id})


async def run_server(app: web.Application, host: str, port: int) -> None:
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    log.info("HTTP server listening on http://%s:%d", host, port)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
