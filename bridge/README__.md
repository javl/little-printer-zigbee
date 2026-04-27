# Little Printer Bridge

Replaces the Berg bridge device with a Python script and an EZSP USB Zigbee dongle.

## Install

```bash
cd little-printer/bridge
pip install -r requirements.txt
```

## Run

```bash
# Print an image:
python -m bridge.main --image photo.png

# Print text:
python -m bridge.main --text "Hello from Linux"

# Override serial port:
python -m bridge.main --port /dev/ttyUSB1 --text "test"

# Exit after printing (instead of staying alive for heartbeats):
python -m bridge.main --text "test" --once

# Print with a face (sends set_personality first, then the delivery):
python -m bridge.main --face face.png --text "Hello"
python -m bridge.main --face face.png --image content.png
```

## Faces / personality

**IMPORTANT: read the note at the end of this section!**

Originally the Little Printer would have a "personality": its face would change every now and then: the hair would grow, or they'd get a haircut, etc.
The "personality" (the face image printed at the top of each delivery) plus three status images (nothing to print, can't see bridge, can't see internet) are stored in the printer's flash memory.

You can update the personality and status images using `--face PATH`, where `PATH` points to the image you want to use as the face. When you pass this argument, two commands are sent before the content:

1. `set_personality` (command `0x0102`): uploads the face image and blank white placeholders(!) for the three status slots.
2. The delivery uses command `0x0001` (with-face) instead of `0x0011` (no-face).

Without `--face`, command `0x0011` is used and no face is shown.

To use real images for the status slots, edit `prepare_personality_job` in `protocol.py`. The four `im` values in the list correspond to: face, nothing-to-print, can't-see-bridge, can't-see-internet.

----

> **Note: DANGER!**
>
> Using `set_personality` will overwrite the printer's flash! So this will overwrite whatever faces are currently stored on the printer! Currently the script will sent the personality image you provide, and send three fully white images for the status faces (as I wasn't able to find proper files for the original faces).
>
> To prevent any accidents, the option to use custom faces is currently disabled in the code. You need to remove the `raise NotImplementedError` in the `prepare_personality_job()` method in `protocol.py`.
>
> If you don't mind running the risk of overwriting the original faces, do give it a try and please report back so we can update this information!

----

## First-run flow

1. The script uses the EZSP USB dongle to create a new Zigbee network with a BERG-prefixed Extended PAN ID. This allows the printer to recognize the network as one it can connect to.
2. Open your printer, press the reset button on the inside and unplug the printer. Close the printer and plug it back in. The LED will flash while it searched for a network.
3. When the LED turns off (or turns solid? need to check) the printer has found the network and has generated a claim code. Press the button on the printer to print the code.
4. At the same time the script will prompt you to enter the code: `Claim code: _` Dashes are optional.
5. The script derives a link key from the code, installs it and saves it to `config.json`.
6. The printer will try to rejoin and thanks to the code it will succeed.
7. Print job is sent in blocks. Script waits for a print-done confirmation from the printer.

Subsequent runs skip pairing entirely, the key is pre-installed from config.

## config.json

Generated automatically on first run. Fields:

| Field | Description |
|---|---|
| `ezsp_port` | Serial port of the EZSP dongle (e.g. `/dev/ttyUSB0`) |
| `ezsp_baud` | Baud rate (typically 115200) |
| `channel` | Zigbee channel (one of 11, 14, 15, 19, 20, 24, 25) |
| `extended_pan_id` | 8-byte hex. First 4 bytes are always `42455247` ("BERG"): the printer scans for this prefix |
| `network_key` | 16-byte hex AES network key, randomly generated |
| `print_id` | Auto-incrementing counter, used to match print confirmations |
| `devices` | Dict of EUI64 → `{claim_code, link_key}` for each paired printer |

Do not change `extended_pan_id` or `network_key` after a printer has been paired. The printer would need to be re-paired.

## Caveats / things to check when testing

**bellows type names**: `EmberInitialSecurityBitmask`, `EzspDecisionId`, `EmberApsOption` etc.
are in `bellows/types/named_array.py`. Names can differ between bellows versions. If you get
`AttributeError` on startup, check that file for the correct spelling.

**`messageSentHandler` signature**: bellows changed the argument order across versions.
`zigbee.py:_handle_message_sent` reads args positionally (index 3 = tag, index 4 = status).
If MAC-level delivery confirmation stops working, print the raw `args` tuple in that handler
to check the actual order your version uses.

**EZSP config values**: `CONFIG_FRAGMENT_MAX_PACKET_SIZE` and similar constants may not exist
on all NCP firmware versions. They are logged at DEBUG level if they fail, so startup will
continue regardless.

**Printer claim code timing**: the printer prints the claim code when it fails to join (no key
found). You have roughly 30–60 seconds before the printer stops retrying. If you miss the
window, power-cycle the printer to start again.

**Re-pairing**: if you need to re-pair a printer (e.g. wrong claim code saved), delete its
entry from `config.json` under `"devices"` and power-cycle the printer.

**Different dongle, same printer**: if you switch to a new dongle, the NCP key table is empty
but `config.json` still has the link keys. The script pre-installs all known keys on startup,
so the printer should rejoin without needing to re-enter the claim code.
