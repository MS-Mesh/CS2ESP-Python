"""
Local client.dll pattern scanner.

When public dumper data trails a CS2 patch, this scanner derives critical
RVAs from the already-loaded client.dll. Failed patterns leave the current
offset values untouched.
"""

from re import DOTALL, compile
from struct import unpack_from

from pymem import Pymem, process


def _resolve_rip_relative_rva(
    image: bytes, match_offset: int, displacement_offset: int,
) -> int | None:
    """Resolve a RIP-relative LEA/MOV at 'match_offset + displacement_offset'."""
    next_instruction_offset = displacement_offset + 4
    if match_offset + next_instruction_offset > len(image):
        return None
    displacement = unpack_from("<i", image, match_offset + displacement_offset)[0]
    return match_offset + next_instruction_offset + displacement


def _read_unsigned_int32(
    image: bytes, match_offset: int, value_offset: int,
) -> int | None:
    if match_offset + value_offset + 4 > len(image):
        return None
    return unpack_from("<I", image, match_offset + value_offset)[0]


_CLIENT_OFFSET_PATTERNS = (
    ("dwLocalPlayerController",
     b"\x48\x8B\x05....\x41\x89\xBE",
     3),
    ("dwEntityList",
     b"\x48\x89\x0D....\xE9....\xCC",
     3),
    ("dwViewMatrix",
     b"\x48\x8D\x0D....\x48\xC1\xE0\x06",
     3),
)

# dwLocalPlayerPawn = dwPrediction + nearby u32 field offset.
_PREDICTION_PATTERN = (b"\x48\x8D\x05....\xC3"
                       b"\xCC\xCC\xCC\xCC\xCC\xCC\xCC\xCC"
                       b"\x40\x53\x56\x41\x54")
_LOCAL_PLAYER_PAWN_PATTERN = b"\x4C\x39\xB6....\x74.\x44\x88\xBE"

# dwViewAngles = dwCSGOInput + nearby u32 field offset.
_CSGO_INPUT_PATTERN = b"\x48\x89\x05....\x0F\x57\xC0\x0F\x11\x05"
_VIEW_ANGLES_PATTERN = b"\xF2\x42\x0F\x10\x84\x28...."


def scan_client(process_memory: Pymem) -> dict:
    """Scan loaded client.dll and return matched '{name: rva}' pairs."""
    resolved_offsets: dict = {}
    try:
        client_module = process.module_from_name(process_memory.process_handle, "client.dll")
    except Exception as error:
        print(f"[sigscan] Could not enumerate client.dll: {error}")
        return resolved_offsets

    client_base = client_module.lpBaseOfDll
    client_size = client_module.SizeOfImage

    try:
        image = process_memory.read_bytes(client_base, client_size)
    except Exception as error:
        print(f"[sigscan] read_bytes(client.dll) failed: {error}")
        return resolved_offsets

    def _find_pattern(pattern: bytes) -> int | None:
        regex_bytes = bytearray()
        for byte_value in pattern:
            if byte_value == 0x2E:
                regex_bytes.append(0x2E)
            else:
                regex_bytes.extend(f"\\x{byte_value:02x}".encode("ascii"))
        match = compile(bytes(regex_bytes), flags=DOTALL).search(image)
        return match.start() if match else None

    for offset_name, pattern, displacement_offset in _CLIENT_OFFSET_PATTERNS:
        match_offset = _find_pattern(pattern)
        if match_offset is None:
            print(f"[sigscan] pattern not found: {offset_name}")
            continue
        resolved_rva = _resolve_rip_relative_rva(
            image, match_offset, displacement_offset,
        )
        if resolved_rva is None:
            print(f"[sigscan] extractor failed: {offset_name}")
            continue
        resolved_offsets[offset_name] = resolved_rva

    # dwLocalPlayerPawn = dwPrediction + u32
    prediction_match_offset = _find_pattern(_PREDICTION_PATTERN)
    if prediction_match_offset is not None:
        prediction_rva = _resolve_rip_relative_rva(image, prediction_match_offset, 3)
        pawn_match_offset = _find_pattern(_LOCAL_PLAYER_PAWN_PATTERN)
        if prediction_rva is not None and pawn_match_offset is not None:
            pawn_field_offset = _read_unsigned_int32(image, pawn_match_offset, 3)
            if pawn_field_offset is not None:
                resolved_offsets["dwLocalPlayerPawn"] = prediction_rva + pawn_field_offset
            else:
                print("[sigscan] dwLocalPlayerPawn field offset read failed")
        else:
            print("[sigscan] dwLocalPlayerPawn: secondary pattern not found")
    else:
        print("[sigscan] dwPrediction pattern not found")

    # dwViewAngles = dwCSGOInput + u32
    input_match_offset = _find_pattern(_CSGO_INPUT_PATTERN)
    if input_match_offset is not None:
        input_rva = _resolve_rip_relative_rva(image, input_match_offset, 3)
        view_angles_match_offset = _find_pattern(_VIEW_ANGLES_PATTERN)
        if input_rva is not None and view_angles_match_offset is not None:
            view_angles_field_offset = _read_unsigned_int32(image, view_angles_match_offset, 6)
            if view_angles_field_offset is not None:
                resolved_offsets["dwViewAngles"] = input_rva + view_angles_field_offset
            else:
                print("[sigscan] dwViewAngles field offset read failed")
        else:
            print("[sigscan] dwViewAngles: secondary pattern not found")
    else:
        print("[sigscan] dwCSGOInput pattern not found")

    return resolved_offsets
