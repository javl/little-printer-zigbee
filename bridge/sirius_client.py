import asyncio
import base64
import json
import logging
import struct
import time

import websockets

from . import config as cfg_module
from .protocol import split_into_blocks

log = logging.getLogger(__name__)

DEFAULT_SIRIUS_SERVER_URL = "wss://littleprinter.nordprojects.co/api/v1/connection"

# Device event codes (mirror sirius/coding/decoders.py)
_EVENT_HEARTBEAT = 0x0001
_EVENT_DID_PRINT = 0x0002


def _eui64_to_sirius(our_eui64_hex: str) -> str:
    """Convert our LE eui64_hex to the BE device_address sirius expects."""
    return bytes.fromhex(our_eui64_hex)[::-1].hex()


def _sirius_to_eui64(sirius_device_address: str) -> str:
    """Convert sirius BE device_address to our LE eui64_hex."""
    return bytes.fromhex(sirius_device_address)[::-1].hex()


class SiriusClient:
    def __init__(self, bridge, cfg: dict, server_url: str = DEFAULT_SIRIUS_SERVER_URL):
        self._bridge = bridge
        self._cfg = cfg
        self._bridge_address = cfg.get("extended_pan_id", "0000000000000000")
        self._server_url = server_url
        self._ws = None

    async def connect(self):
        log.info("Connecting to Nord server at %s", self._server_url)
        self._ws = await websockets.connect(
            self._server_url,
            subprotocols=["bergcloud-bridge-v1"],
        )
        log.info("Connected. Sending PowerOn.")
        await self._send_power_on()
        self._bridge.on_printer_event = self._on_printer_event

    async def _send(self, msg: dict):
        await self._ws.send(json.dumps(msg))

    def _base_event(self) -> dict:
        return {
            "type": "BridgeEvent",
            "bridge_address": self._bridge_address,
            "timestamp": int(time.time()),
        }

    async def _send_power_on(self):
        msg = self._base_event()
        msg["json_payload"] = {
            "name": "power_on",
            "model": "little-printer-zigbee",
            "firmware_version": "1.0.0",
            "ncp_version": "1.0.0",
            "local_ip_address": "127.0.0.1",
            "mac_address": "00:00:00:00:00:00",
            "uptime": 0,
            "uboot_environment": {},
            "network_info": {},
        }
        await self._send(msg)

    async def send_encryption_key_required(self, device_address: str):
        """device_address: sirius BE format."""
        msg = self._base_event()
        msg["json_payload"] = {
            "name": "encryption_key_required",
            "device_address": device_address,
        }
        log.info("→ EncryptionKeyRequired for %s", device_address)
        await self._send(msg)

    async def send_device_connect(self, device_address: str):
        """device_address: sirius BE format."""
        msg = self._base_event()
        msg["json_payload"] = {
            "name": "device_connect",
            "device_address": device_address,
        }
        log.info("→ DeviceConnect for %s", device_address)
        await self._send(msg)

    def _on_printer_event(self, eui64_hex, event_code: int, payload: bytes):
        """Called by LittlePrinterBridge for device events; schedules async forwarding."""
        if eui64_hex is None:
            return
        asyncio.get_event_loop().create_task(
            self._forward_device_event(eui64_hex, event_code, payload)
        )

    async def _forward_device_event(self, eui64_hex: str, event_code: int, payload: bytes):
        device_address = _eui64_to_sirius(eui64_hex)

        if event_code == _EVENT_HEARTBEAT:
            uptime = struct.unpack_from("<I", payload, 10)[0] if len(payload) >= 14 else 0
            binary = struct.pack("<HII", _EVENT_HEARTBEAT, 0, 4) + struct.pack("<I", uptime)
        elif event_code == _EVENT_DID_PRINT:
            if len(payload) >= 15:
                print_type = payload[10]
                print_id = struct.unpack_from("<I", payload, 11)[0]
            else:
                print_type, print_id = 0x01, 0
            binary = struct.pack("<HII", _EVENT_DID_PRINT, print_id, 5) + struct.pack("<BI", print_type, print_id)
        else:
            return

        msg = {
            "type": "DeviceEvent",
            "bridge_address": self._bridge_address,
            "device_address": device_address,
            "timestamp": int(time.time()),
            "binary_payload": base64.b64encode(binary).decode("utf-8"),
        }
        try:
            await self._send(msg)
        except Exception as exc:
            log.warning("Failed to forward device event: %s", exc)

    async def receive_forever(self):
        """Receive and dispatch commands from the Nord server indefinitely."""
        async for raw in self._ws:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                log.warning("Non-JSON from server: %s", exc)
                continue

            msg_type = data.get("type")
            command_id = data.get("command_id")

            if msg_type == "BridgeCommand":
                await self._handle_bridge_command(data, command_id)
            elif msg_type == "DeviceCommand":
                asyncio.get_event_loop().create_task(
                    self._handle_device_command(data, command_id)
                )
            else:
                log.info("Unhandled server message type: %s", msg_type)

    async def _handle_bridge_command(self, data: dict, command_id):
        payload = data.get("json_payload", {})
        name = payload.get("name")

        if name == "add_device_encryption_key":
            params = payload.get("params", {})
            sirius_addr = params["device_address"]
            key = base64.b64decode(params["encryption_key"])
            eui64_hex = _sirius_to_eui64(sirius_addr)
            eui64_le = bytes.fromhex(eui64_hex)
            log.info("← AddDeviceEncryptionKey for %s", sirius_addr)
            await self._bridge.install_link_key(eui64_le, key)
            self._cfg["devices"][eui64_hex] = {"link_key": key.hex()}
            cfg_module.save(self._cfg)
            log.info("Saved device %s to config", eui64_hex)
            await self._send_bridge_command_response(command_id)
        else:
            log.info("Unknown BridgeCommand name: %s", name)

    async def _handle_device_command(self, data: dict, command_id):
        sirius_addr = data["device_address"]
        eui64_hex = _sirius_to_eui64(sirius_addr)
        binary = base64.b64decode(data["binary_payload"])
        blocks = split_into_blocks(binary)
        log.info("← DeviceCommand (cmd_id=%s) for %s - %d block(s)", command_id, sirius_addr, len(blocks))
        try:
            await self._bridge.send_print_job(eui64_hex, blocks)
            return_code = 0
        except Exception as exc:
            log.error("Print job failed: %s", exc)
            return_code = 1
        await self._send_device_command_response(command_id, return_code)

    async def _send_bridge_command_response(self, command_id, return_code: int = 0):
        await self._send({
            "type": "BridgeCommandResponse",
            "bridge_address": self._bridge_address,
            "command_id": command_id,
            "return_code": return_code,
            "timestamp": int(time.time()),
        })

    async def _send_device_command_response(self, command_id, return_code: int = 0):
        await self._send({
            "type": "DeviceCommandResponse",
            "bridge_address": self._bridge_address,
            "command_id": command_id,
            "return_code": return_code,
            "timestamp": int(time.time()),
        })
