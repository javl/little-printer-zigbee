import json
import os
import secrets
import sys

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

BERG_EPAN_PREFIX = bytes([0x42, 0x45, 0x52, 0x47])  # "BERG"
BERG_CHANNELS = [11, 14, 15, 19, 20, 24, 25]
DEFAULT_CHANNEL = 15


def _defaults():
    default_port = "COM3" if sys.platform == "win32" else "/dev/ttyUSB0"
    return {
        "ezsp_port": default_port,
        "ezsp_baud": 115200,
        "channel": DEFAULT_CHANNEL,
        "extended_pan_id": (BERG_EPAN_PREFIX + secrets.token_bytes(4)).hex(),
        "network_key": secrets.token_hex(16),
        "print_id": 1,
        "devices": {},
    }


def load(path=CONFIG_PATH):
    if os.path.exists(path):
        with open(path) as f:
            cfg = json.load(f)
        defaults = _defaults()
        for k, v in defaults.items():
            if k not in cfg or cfg[k] == "":
                cfg[k] = v
        save(cfg, path)
        return cfg
    cfg = _defaults()
    save(cfg, path)
    return cfg


def save(cfg, path=CONFIG_PATH):
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)


def next_print_id(cfg, path=CONFIG_PATH):
    pid = cfg.get("print_id", 1)
    cfg["print_id"] = (pid % 0xFFFFFFFF) + 1
    save(cfg, path)
    return pid
