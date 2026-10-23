"""Read-only HashLink x64 metadata decoder.

The caller supplies ``read(address, count) -> bytes`` (for example a checked
ReadProcessMemory wrapper). This module has no process access, writes, native
calls, or object mutation. Layouts are derived from HaxeFoundation/hashlink
1.14 and 1.15 src/hl.h and src/std/obj.c. Metadata must still be validated
against the target build; never treat an arbitrary pointer as a valid object.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import struct
from typing import Callable


KIND_NAMES = (
    "void", "ui8", "ui16", "i32", "i64", "f32", "f64", "bool",
    "bytes", "dynamic", "function", "object", "native_array", "type",
    "reference", "virtual", "dynamic_object", "abstract", "enum", "null",
    "method", "struct", "packed", "guid",
)
SCALAR_FORMATS = {1: "B", 2: "H", 3: "i", 4: "q", 5: "f", 6: "d", 7: "?", 23: "q"}


@dataclass(frozen=True)
class TypeInfo:
    address: int
    kind: int
    parameter: int
    name: str
    super_type: int = 0
    fields_ptr: int = 0
    own_count: int = 0
    runtime_ptr: int = 0
    field_count: int = 0
    object_size: int = 0
    indexes_ptr: int = 0


@dataclass(frozen=True)
class FieldInfo:
    name: str
    type_ptr: int
    owner: str
    absolute_index: int
    offset: int


class LayoutError(ValueError):
    pass


class HLReader:
    """Decode checked metadata. Cache types, but never cache mutable values."""

    def __init__(self, read: Callable[[int, int], bytes]):
        self.read = read
        self._types: dict[int, TypeInfo] = {}
        self._fields: dict[int, tuple[FieldInfo, ...]] = {}

    def unpack(self, address: int, fmt: str):
        size = struct.calcsize("<" + fmt)
        data = self.read(address, size)
        if len(data) != size:
            raise LayoutError(f"Short read at {address:#x}: {len(data)}/{size}")
        result = struct.unpack("<" + fmt, data)
        return result[0] if len(result) == 1 else result

    def ptr(self, address: int) -> int:
        return self.unpack(address, "Q")

    def utf16(self, address: int, max_chars: int = 512) -> str:
        if not address:
            return ""
        # Small page-bounded reads avoid one ReadProcessMemory syscall per char.
        result = bytearray()
        while len(result) // 2 < max_chars:
            pos = address + len(result)
            count = min(128, 4096 - pos % 4096, (max_chars - len(result) // 2) * 2)
            try:
                data = self.read(pos, count)
            except Exception:
                data = self.read(pos, 2)
            if len(data) < 2:
                raise LayoutError(f"Short UTF16 read at {pos:#x}")
            for i in range(0, len(data) - 1, 2):
                pair = data[i:i + 2]
                if pair == b"\0\0":
                    return result.decode("utf-16-le", errors="strict")
                result.extend(pair)
        raise LayoutError(f"Unterminated/too long UTF16 at {address:#x}")

    @staticmethod
    def _ptr_ok(address: int) -> bool:
        return 0x10000 <= address < 0x0000800000000000 and address % 8 == 0

    def type_info(self, address: int, _depth: int = 0) -> TypeInfo:
        if address in self._types:
            return self._types[address]
        if not self._ptr_ok(address) or _depth > 32:
            raise LayoutError(f"Invalid/cyclic type address: {address:#x}")
        kind = self.unpack(address, "i")
        if not 0 <= kind < len(KIND_NAMES):
            raise LayoutError(f"Invalid type kind {kind} at {address:#x}")
        parameter = self.ptr(address + 8)
        if kind not in (11, 21):
            name = KIND_NAMES[kind]
            if kind == 17 and parameter:
                name = "abstract:" + self.utf16(parameter)
            elif kind == 18 and parameter:
                name = "enum:" + self.utf16(self.ptr(parameter))
            info = TypeInfo(address, kind, parameter, name)
            self._types[address] = info
            return info
        if not self._ptr_ok(parameter):
            raise LayoutError(f"Invalid hl_type_obj pointer: {parameter:#x}")
        own_count, nproto, nbindings = self.unpack(parameter, "iii")
        if not (0 <= own_count <= 10000 and 0 <= nproto <= 100000 and 0 <= nbindings <= 10000):
            raise LayoutError("Invalid object metadata counts")
        name = self.utf16(self.ptr(parameter + 16))
        if not name or any(ord(c) < 32 for c in name):
            raise LayoutError("Invalid object type name")
        super_type = self.ptr(parameter + 24)
        fields_ptr = self.ptr(parameter + 32)
        runtime_ptr = self.ptr(parameter + 72)
        if runtime_ptr:
            rt_type = self.ptr(runtime_ptr)
            field_count = self.unpack(runtime_ptr + 8, "i")
            object_size = self.unpack(runtime_ptr + 16, "i")
            indexes_ptr = self.ptr(runtime_ptr + 40)
            if rt_type != address or not own_count <= field_count <= 10000 or not 0 <= object_size <= 16_777_216:
                raise LayoutError("Runtime metadata does not match type")
        else:
            field_count = object_size = indexes_ptr = 0
        info = TypeInfo(address, kind, parameter, name, super_type, fields_ptr,
                        own_count, runtime_ptr, field_count, object_size, indexes_ptr)
        self._types[address] = info
        return info

    def fields(self, type_ptr: int, _seen: frozenset[int] = frozenset()) -> tuple[FieldInfo, ...]:
        if type_ptr in self._fields:
            return self._fields[type_ptr]
        if type_ptr in _seen or len(_seen) > 32:
            raise LayoutError("Cyclic/too deep inheritance")
        info = self.type_info(type_ptr)
        if info.kind not in (11, 21) or not info.runtime_ptr:
            raise LayoutError("No initialized object runtime")
        inherited = self.fields(info.super_type, _seen | {type_ptr}) if info.super_type else ()
        count_before = len(inherited)
        if count_before + info.own_count != info.field_count:
            raise LayoutError("Inherited field count mismatch")
        result = list(inherited)
        offsets = self.unpack(info.indexes_ptr, "i" * info.field_count) if info.field_count else ()
        if info.field_count == 1:
            offsets = (offsets,)
        # All offsets, including parent fields, come from this class's runtime.
        result = [FieldInfo(f.name, f.type_ptr, f.owner, i, offsets[i]) for i, f in enumerate(result)]
        for i in range(info.own_count):
            field_addr = info.fields_ptr + i * 24
            name = self.utf16(self.ptr(field_addr))
            field_type = self.ptr(field_addr + 8)
            absolute = count_before + i
            offset = offsets[absolute]
            if not 0 <= offset <= info.object_size:
                raise LayoutError(f"Invalid field offset {name}: {offset}")
            result.append(FieldInfo(name, field_type, info.name, absolute, offset))
        fields = tuple(result)
        self._fields[type_ptr] = fields
        return fields

    def object_type(self, address: int) -> TypeInfo:
        return self.type_info(self.ptr(address))

    def value_at(self, address: int, type_ptr: int):
        info = self.type_info(type_ptr)
        if info.kind in SCALAR_FORMATS:
            value = self.unpack(address, SCALAR_FORMATS[info.kind])
            if isinstance(value, float) and not math.isfinite(value):
                return {"non_finite": repr(value)}
            return value
        if info.kind == 0:
            return None
        if info.kind == 22:
            return {"packed_address": address, "type_ptr": info.parameter}
        return self.ptr(address)

    def object_values(self, address: int) -> dict:
        info = self.object_type(address)
        result = {"_address": address, "_type": info.name, "_type_ptr": info.address}
        for field in self.fields(info.address):
            key = field.name if field.name not in result else field.owner + "." + field.name
            result[key] = self.value_at(address + field.offset, field.type_ptr)
        return result

    def field(self, address: int, name: str):
        info = self.object_type(address)
        # Last declaration wins if an inherited name is shadowed.
        field = next((f for f in reversed(self.fields(info.address)) if f.name == name), None)
        if field is None:
            raise KeyError(f"{info.name}.{name}")
        return self.value_at(address + field.offset, field.type_ptr)

    def native_array(self, address: int, limit: int = 10000) -> dict:
        """HL varray header is 24 bytes on x64; Haxe Array is a wrapper object."""
        header_type = self.type_info(self.ptr(address))
        if header_type.kind != 12:
            raise LayoutError("Object is not a native HL array")
        element_type = self.ptr(address + 8)
        size = self.unpack(address + 16, "i")
        if not 0 <= size <= limit:
            raise LayoutError(f"Invalid/too large native array size: {size}")
        info = self.type_info(element_type)
        stride = struct.calcsize("<" + SCALAR_FORMATS[info.kind]) if info.kind in SCALAR_FORMATS else 8
        if info.kind in (0, 22):
            raise LayoutError("Unsupported native array element kind")
        values = [self.value_at(address + 24 + stride * i, element_type) for i in range(size)]
        return {"_address": address, "element_type": info.name, "size": size, "values": values}

    def dynamic(self, address: int):
        """Unbox vdynamic scalar payload; object values are already pointers."""
        info = self.object_type(address)
        if info.kind in SCALAR_FORMATS:
            return self.value_at(address + 8, info.address)
        if info.kind in (11, 21):
            return self.object_values(address)
        return {"_address": address, "_type": info.name, "payload": self.ptr(address + 8)}

    def string_map(self, address: int, limit: int = 10000) -> dict:
        """Read hxbit.MapData / virtual / haxe.ds.StringMap without native calls.

        Hash table layout comes from HashLink 1.14 std/maps.c + maps.h. Only
        indices reachable from live cells are read; deleted/stale slots are
        excluded. Missing entries are omitted, not guessed as zero.
        """
        seen = set()
        for _ in range(8):
            if not address or address in seen:
                raise LayoutError("Null/cyclic string map wrapper")
            seen.add(address)
            info = self.object_type(address)
            if info.kind == 15:  # vvirtual.value is concrete object at +8.
                address = self.ptr(address + 8)
            elif info.name == "hxbit.MapData":
                address = self.field(address, "map")
            elif info.name == "haxe.ds.StringMap":
                address = self.field(address, "h")
                break
            else:
                raise LayoutError(f"Unexpected string map wrapper {info.name}")
        else:
            raise LayoutError("Too many string map wrappers")
        header = self.read(address, 64)
        if len(header) != 64:
            raise LayoutError("Short HashLink bytes-map header")
        cells, nexts, entries, values, free, head, nbuckets, ncells, nentries, maxentries = struct.unpack(
            "<QQQQQiiiii4x", header)
        if not 0 <= nentries <= maxentries <= limit or not 0 <= ncells <= limit:
            raise LayoutError("Invalid bytes-map counts")
        if nentries == 0:
            return {}
        if not cells or not nexts or not values or ncells == 0:
            raise LayoutError("Invalid bytes-map storage")
        fmt = "b" if maxentries < 128 else "i"
        width = 1 if fmt == "b" else 4
        cell_data = self.read(cells, ncells * width)
        next_data = self.read(nexts, maxentries * width)
        value_data = self.read(values, maxentries * 16)
        if len(cell_data) != ncells * width or len(next_data) != maxentries * width or len(value_data) != maxentries * 16:
            raise LayoutError("Short bytes-map backing storage")
        active = set()
        result = {}
        for bucket in range(ncells):
            index = struct.unpack_from("<" + fmt, cell_data, bucket * width)[0]
            while index >= 0:
                if index >= maxentries or index in active:
                    raise LayoutError("Invalid/cyclic bytes-map entry chain")
                active.add(index)
                key_ptr, value_ptr = struct.unpack_from("<QQ", value_data, index * 16)
                key = self.utf16(key_ptr)
                if key in result:
                    raise LayoutError("Duplicate string-map key")
                result[key] = self.dynamic(value_ptr) if value_ptr else None
                index = struct.unpack_from("<" + fmt, next_data, index * width)[0]
        if len(active) != nentries:
            raise LayoutError("Bytes-map entry count changed/mismatched")
        if self.read(address, 64) != header:
            raise LayoutError("Bytes-map resized during sampling")
        return result

    def int_array(self, address: int, limit: int = 10000) -> list[int]:
        """Unwrap hxbit.ArrayProxyData -> ArrayDyn -> ArrayBytes_Int."""
        seen = set()
        for _ in range(8):
            if not address or address in seen:
                raise LayoutError("Null/cyclic integer array wrapper")
            seen.add(address)
            info = self.object_type(address)
            if info.name in ("hxbit.ArrayProxyData", "hl.types.ArrayDyn"):
                address = self.field(address, "array")
                continue
            if info.name != "hl.types.ArrayBytes_Int":
                raise LayoutError(f"Unexpected integer array type: {info.name}")
            length = self.field(address, "length")
            capacity = self.field(address, "size")
            raw_ptr = self.field(address, "bytes")
            if not 0 <= length <= capacity <= limit:
                raise LayoutError("Invalid integer array length/capacity")
            if not length:
                return []
            data = self.read(raw_ptr, length * 4)
            if len(data) != length * 4:
                raise LayoutError("Short integer array backing storage")
            return list(struct.unpack("<" + "i" * length, data))
        raise LayoutError("Too many integer array wrappers")


def layout_constants() -> dict:
    return {
        "pointer_size": 8,
        "hl_type_size": 32,
        "hl_type_kind": 0,
        "hl_type_obj": 8,
        "hl_type_obj_size": 80,
        "hl_type_obj_name": 16,
        "hl_type_obj_super": 24,
        "hl_type_obj_fields": 32,
        "hl_type_obj_runtime": 72,
        "hl_obj_field_size": 24,
        "hl_runtime_obj_size": 120,
        "hl_runtime_obj_nfields": 8,
        "hl_runtime_obj_size_field": 16,
        "hl_runtime_obj_fields_indexes": 40,
        "hl_runtime_obj_parent": 56,
        "varray_size": 24,
        "varray_element_type": 8,
        "varray_length": 16,
        "vdynamic_payload": 8,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(layout_constants(), indent=2))
