import struct

from . import image_encoding
from os import path

LITTLE_PRINTER_DEVICE_TYPE         = 0x01
CMD_SET_DELIVERY_AND_PRINT         = 0x0001  # delivery + print, shows face
CMD_SET_DELIVERY_AND_PRINT_NO_FACE = 0x0011  # delivery + print, no face
CMD_SET_PERSONALITY                = 0x0102  # 4-image personality setup

PRINTER_CONTROL = struct.pack(
    "<13B",
    0x1D, 0x73, 0x03, 0xE8,   # max printer speed: 1000
    0x1D, 0x61, 0xD0,         # acceleration: 208
    0x1D, 0x2F, 0x0F,         # peak current: 15
    0x1D, 0x44, 0x80,         # max intensity: 128
)

MAX_BLOCK_SIZE = 512


def build_print_payload(pixel_count: int, rle_bytes: bytes) -> bytes:
    """Assemble the full binary payload to send to the printer for one image."""
    byte_count = pixel_count // 8
    n3, rem = divmod(byte_count, 65536)
    n2, n1 = divmod(rem, 256)
    printer_data = struct.pack("<8B", 0x1B, 0x2A, n1, n2, n3, 0, 0, 48)

    # inner region: reserved byte + length + control bytes + data command
    inner = PRINTER_CONTROL + printer_data
    header_region = struct.pack("<BI", 0, len(inner)) + inner

    # full payload: length (includes trailing rle_bytes) + reserved + header + rle
    payload = struct.pack("<IB", len(header_region) + len(rle_bytes) + 1, 0)
    payload += header_region + rle_bytes
    return payload


def build_command(command_id: int, print_id: int, payload: bytes) -> bytes:
    """Wrap a payload in the 12-byte command header."""
    header = struct.pack(
        "<BBHII",
        LITTLE_PRINTER_DEVICE_TYPE,
        0,           # reserved
        command_id,
        print_id,
        0,           # CRC (unused)
    )
    return header + struct.pack("<I", len(payload)) + payload


def split_into_blocks(data: bytes) -> list[bytes]:
    return [data[i:i + MAX_BLOCK_SIZE] for i in range(0, len(data), MAX_BLOCK_SIZE)]


def prepare_print_job(
    image_path: str | None,
    text: str | None,
    print_id: int,
    no_face: bool = False,
    max_height: int | None = None,
    dither: bool = False,
) -> list[bytes]:
    """Return ordered list of blocks ready to send to the printer.

    no_face=False uses command 0x0001 (printer shows the face set via
    set_personality at the top of the printout). no_face=True uses 0x0011.
    """
    if image_path:
        im = image_encoding.load_image(image_path, max_height=max_height)
    elif text:
        im = image_encoding.text_to_image(text)
    else:
        raise ValueError("provide image_path or text")

    pixel_count, rle_bytes = image_encoding.image_to_rle(im, dither=dither)
    payload = build_print_payload(pixel_count, rle_bytes)
    cmd = CMD_SET_DELIVERY_AND_PRINT_NO_FACE if no_face else CMD_SET_DELIVERY_AND_PRINT
    command = build_command(cmd, print_id, payload)
    return split_into_blocks(command)


def prepare_print_job_from_pil(
    im,
    print_id: int,
    no_face: bool = False,
    dither: bool = False,
) -> list[bytes]:
    """Like prepare_print_job but accepts a PIL Image directly."""
    pixel_count, rle_bytes = image_encoding.image_to_rle(im, dither=dither)
    payload = build_print_payload(pixel_count, rle_bytes)
    cmd = CMD_SET_DELIVERY_AND_PRINT_NO_FACE if no_face else CMD_SET_DELIVERY_AND_PRINT
    command = build_command(cmd, print_id, payload)
    return split_into_blocks(command)


def prepare_personality_job(face_images_path: str, print_id: int) -> list[bytes]:
    """Build a set_personality command (0x0102) with four image slots.

    Slot 1: face (user-provided image)
    Slots 2-4: nothing-to-print / can't-see-bridge / can't-see-internet
                (blank white images; replace with real images if desired)
    """

    # Note: this error is raised to prevent this function from being
    # accidentally used: see README.md before removing!

    personality_im = image_encoding.load_image(path.join(face_images_path, "personality.png"))

    nothing_to_print_im = image_encoding.load_image(path.join(face_images_path, "nothing_to_print.png"))
    no_bridge_im = image_encoding.load_image(path.join(face_images_path, "no_bridge.png"))
    no_internet_im = image_encoding.load_image(path.join(face_images_path, "no_internet.png"))

    payload = b""
    for im in [personality_im, nothing_to_print_im, no_bridge_im, no_internet_im]:
        pixel_count, rle_bytes = image_encoding.image_to_rle(im)
        payload += build_print_payload(pixel_count, rle_bytes)

    command = build_command(CMD_SET_PERSONALITY, print_id, payload)
    return split_into_blocks(command)
