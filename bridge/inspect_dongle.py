"""
Inspect EZSP dongle at /dev/ttyUSB0 (or --port override).
Reads firmware version, EUI64, network state, network parameters,
config values, and policies — all read-only, no changes made.

Usage:
    python -m bridge.inspect_dongle [--port /dev/ttyUSB0] [--baud 115200]
"""

import argparse
import asyncio
import sys

from bellows.ezsp import EZSP
import bellows.types as t


# Config IDs relevant to Little Printer network operation
RELEVANT_CONFIGS = [
    t.EzspConfigId.CONFIG_SECURITY_LEVEL,
    t.EzspConfigId.CONFIG_STACK_PROFILE,
    t.EzspConfigId.CONFIG_ADDRESS_TABLE_SIZE,
    t.EzspConfigId.CONFIG_TRUST_CENTER_ADDRESS_CACHE_SIZE,
    t.EzspConfigId.CONFIG_KEY_TABLE_SIZE,
    t.EzspConfigId.CONFIG_SOURCE_ROUTE_TABLE_SIZE,
    t.EzspConfigId.CONFIG_FRAGMENT_WINDOW_SIZE,
    t.EzspConfigId.CONFIG_FRAGMENT_DELAY_MS,
    t.EzspConfigId.CONFIG_END_DEVICE_POLL_TIMEOUT,
    t.EzspConfigId.CONFIG_END_DEVICE_POLL_TIMEOUT_SHIFT,
    t.EzspConfigId.CONFIG_TX_POWER_MODE,
    t.EzspConfigId.CONFIG_DISABLE_RELAY,
    t.EzspConfigId.CONFIG_MAX_HOPS,
]

# Expected values for Little Printer compatibility
EXPECTED_CONFIGS = {
    "CONFIG_SECURITY_LEVEL":                  5,
    "CONFIG_STACK_PROFILE":                   2,
    "CONFIG_FRAGMENT_WINDOW_SIZE":            8,
    "CONFIG_TX_POWER_MODE":                   1,
    "CONFIG_DISABLE_RELAY":                   1,
}

RELEVANT_POLICIES = [
    t.EzspPolicyId.TRUST_CENTER_POLICY,
    t.EzspPolicyId.TC_KEY_REQUEST_POLICY,
    t.EzspPolicyId.APP_KEY_REQUEST_POLICY,
    t.EzspPolicyId.BINDING_MODIFICATION_POLICY,
    t.EzspPolicyId.MESSAGE_CONTENTS_IN_CALLBACK_POLICY,
]

RELEVANT_VALUES = [
    t.EzspValueId.VALUE_MAXIMUM_INCOMING_TRANSFER_SIZE,
    t.EzspValueId.VALUE_MAXIMUM_OUTGOING_TRANSFER_SIZE,
    t.EzspValueId.VALUE_STACK_TOKEN_WRITING,
    t.EzspValueId.VALUE_STACK_IS_PERFORMING_REJOIN,
    t.EzspValueId.VALUE_FREE_BUFFERS,
    t.EzspValueId.VALUE_EXTENDED_SECURITY_BITMASK,
    t.EzspValueId.VALUE_VERSION_INFO,
]


NETWORK_STATE_NAMES = {
    0x00: "NO_NETWORK",
    0x01: "JOINING_NETWORK",
    0x02: "JOINED_NETWORK",
    0x03: "JOINED_NETWORK_NO_PARENT",
    0x04: "LEAVING_NETWORK",
}


def sep(title=""):
    width = 60
    if title:
        pad = (width - len(title) - 2) // 2
        print(f"\n{'─' * pad} {title} {'─' * (width - pad - len(title) - 2)}")
    else:
        print("─" * width)


async def inspect(port: str, baud: int):
    print(f"Connecting to {port} at {baud} baud...")
    ezsp = EZSP({"path": port, "baudrate": baud, "flow_control": None})
    await ezsp.connect()

    # ── Firmware / version ────────────────────────────────────────────────────
    sep("FIRMWARE")
    try:
        ver = await ezsp.getVersionStruct()
        print(f"  EZSP version      : {ezsp.ezsp_version}")
        print(f"  NCP stack version : {ver}")
    except Exception as exc:
        print(f"  version           : (error: {exc})")
        print(f"  EZSP version      : {ezsp.ezsp_version}")

    # ── Identity ──────────────────────────────────────────────────────────────
    sep("IDENTITY")
    try:
        (eui64,) = await ezsp.getEui64()
        eui64_bytes = bytes(eui64)
        print(f"  EUI64 (LE hex)    : {eui64_bytes.hex()}")
        print(f"  EUI64 (BE hex)    : {eui64_bytes[::-1].hex()}")
    except Exception as exc:
        print(f"  EUI64             : (error: {exc})")

    try:
        (node_id,) = await ezsp.getNodeId()
        print(f"  Node ID           : 0x{int(node_id):04x}")
    except Exception as exc:
        print(f"  Node ID           : (error: {exc})")

    # ── Network state ─────────────────────────────────────────────────────────
    sep("NETWORK STATE")
    try:
        (state,) = await ezsp.networkState()
        state_int = int(state)
        state_name = NETWORK_STATE_NAMES.get(state_int, f"UNKNOWN(0x{state_int:02x})")
        print(f"  State             : {state_name} (0x{state_int:02x})")
        network_up = state_int == 0x02
    except Exception as exc:
        print(f"  State             : (error: {exc})")
        network_up = False

    # ── Network parameters ────────────────────────────────────────────────────
    sep("NETWORK PARAMETERS")
    if network_up:
        try:
            (status, params) = await ezsp.getNetworkParameters()
            if int(status) == 0:
                print(f"  PAN ID            : 0x{int(params.panId):04x}")
                print(f"  Extended PAN ID   : {bytes(params.extendedPanId).hex()}")
                print(f"  Channel           : {int(params.radioChannel)}")
                print(f"  TX power          : {int(params.radioTxPower)} dBm")
                print(f"  Join method       : {params.joinMethod}")
                print(f"  NWK manager ID    : 0x{int(params.nwkManagerId):04x}")
                print(f"  NWK update ID     : {int(params.nwkUpdateId)}")
                print(f"  Channels mask     : 0x{int(params.channels):08x}")
            else:
                print(f"  getNetworkParameters status: {status}")
        except Exception as exc:
            print(f"  (error: {exc})")
    else:
        print("  No network formed — skipping (state != JOINED_NETWORK)")

        # Try to read stored parameters anyway (may work on some firmware)
        try:
            (status, params) = await ezsp.getNetworkParameters()
            if int(status) == 0:
                print(f"  [stored] PAN ID          : 0x{int(params.panId):04x}")
                print(f"  [stored] Extended PAN ID : {bytes(params.extendedPanId).hex()}")
                print(f"  [stored] Channel         : {int(params.radioChannel)}")
                print(f"  [stored] TX power        : {int(params.radioTxPower)} dBm")
        except Exception:
            pass

    # ── Config values ─────────────────────────────────────────────────────────
    sep("CONFIG VALUES")
    for cid in RELEVANT_CONFIGS:
        name = cid.name
        try:
            (status, value) = await ezsp.getConfigurationValue(cid)
            if int(status) == 0:
                expected = EXPECTED_CONFIGS.get(name)
                flag = ""
                if expected is not None and int(value) != expected:
                    flag = f"  ← MISMATCH (want {expected})"
                elif expected is not None:
                    flag = "  ✓"
                print(f"  {name:<48}: {int(value)}{flag}")
            else:
                print(f"  {name:<48}: (status={status})")
        except Exception as exc:
            print(f"  {name:<48}: (error: {exc})")

    # ── Value (large/variable) ─────────────────────────────────────────────────
    sep("VALUES")
    for vid in RELEVANT_VALUES:
        name = vid.name
        try:
            (status, data) = await ezsp.getValue(vid)
            if int(status) == 0:
                val_int = int.from_bytes(bytes(data), "little")
                print(f"  {name:<48}: {val_int}")
            else:
                print(f"  {name:<48}: (status={status})")
        except Exception as exc:
            print(f"  {name:<48}: (error: {exc})")

    # ── Policies ──────────────────────────────────────────────────────────────
    sep("POLICIES")
    for pid in RELEVANT_POLICIES:
        name = pid.name
        try:
            (status, decision) = await ezsp.getPolicy(pid)
            if int(status) == 0:
                print(f"  {name:<48}: {decision.name} ({int(decision)})")
            else:
                print(f"  {name:<48}: (status={status})")
        except Exception as exc:
            print(f"  {name:<48}: (error: {exc})")

    # ── Key table ─────────────────────────────────────────────────────────────
    sep("KEY TABLE")
    try:
        count = 0
        async for entry in ezsp.read_link_keys():
            eui = bytes(entry.partner_ieee).hex()
            key = bytes(entry.key).hex()
            print(f"  [{count}] partner={eui}  key={key}  tx_fc={entry.tx_counter}  rx_fc={entry.rx_counter}")
            count += 1
        if count == 0:
            print("  (empty)")
    except Exception as exc:
        print(f"  (error: {exc})")

    # ── Security keys ─────────────────────────────────────────────────────────
    sep("SECURITY KEYS")
    try:
        nk = await ezsp.get_network_key()
        print(f"  Network key               : {bytes(nk.key).hex()}")
        print(f"  Network key seq           : {nk.seq}")
        print(f"  Network key frame counter : {nk.tx_counter}")
    except Exception as exc:
        print(f"  Network key               : (error: {exc})")

    try:
        tc = await ezsp.get_tc_link_key()
        print(f"  TC link key               : {bytes(tc.key).hex()}")
    except Exception as exc:
        print(f"  TC link key               : (error: {exc})")

    # ── Radio power ───────────────────────────────────────────────────────────
    sep("RADIO")
    try:
        (status, power) = await ezsp.getRadioParameters(0)
        if int(status) == 0:
            print(f"  Radio TX power    : {int(power.radioTxPower)} dBm")
    except Exception:
        pass  # getRadioParameters not available on all EZSP versions

    sep()
    await ezsp.disconnect()


def main():
    parser = argparse.ArgumentParser(description="Inspect EZSP Zigbee dongle.")
    parser.add_argument("--port", default="/dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=115200)
    args = parser.parse_args()

    try:
        asyncio.run(inspect(args.port, args.baud))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"\nFatal: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
