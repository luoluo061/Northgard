"""Checked read-only decoders used by the match-history research exporter.

Layouts follow the already vendored HashLink 1.14/1.15 hl.h, maps.c and maps.h.
No game function calls, process writes or injected hooks.
"""
from contextlib import contextmanager
import struct

from runtime_types import LayoutError, SCALAR_FORMATS
from live_reader import objects, string


@contextmanager
def checked_map_backing(h, address, expected_type, limit=8):
    """Resolve MapData/virtual wrappers and reject observed retargeting.

    Only a caller-selected StringMap or IntMap is accepted. A null wrapper or
    storage is unavailable, never proof of an empty map. Map payload readers
    retain their own header/storage guards; this additionally checks every
    wrapper link and the concrete map's storage pointer after the read.
    """
    if expected_type not in ('haxe.ds.StringMap', 'haxe.ds.IntMap'):
        raise LayoutError('Unsupported checked map type')
    seen, signature = set(), []
    for _ in range(limit):
        if not address or address in seen:
            raise LayoutError('Null/cyclic checked map wrapper')
        seen.add(address)
        info = h.object_type(address)
        identity = (info.name, getattr(info, 'kind', None), getattr(info, 'address', None))
        if info.name == expected_type:
            link_kind, target = 'storage', h.field(address, 'h')
        elif info.name == 'hxbit.MapData':
            link_kind, target = 'map', h.field(address, 'map')
        elif getattr(info, 'kind', None) == 15:
            link_kind, target = 'virtual', h.ptr(address + 8)
        else:
            raise LayoutError('Unexpected checked map backing type')
        if not target:
            raise LayoutError('Null checked map backing/storage')
        signature.append((address, identity, link_kind, target))
        if link_kind == 'storage':
            backing = address
            break
        address = target
    else:
        raise LayoutError('Too many checked map wrappers')
    try:
        yield backing
    finally:
        for pointer, identity, link_kind, target in signature:
            info = h.object_type(pointer)
            if (info.name, getattr(info, 'kind', None), getattr(info, 'address', None)) != identity:
                raise LayoutError('Checked map wrapper type changed while reading')
            current = (h.ptr(pointer + 8) if link_kind == 'virtual' else
                       h.field(pointer, 'map' if link_kind == 'map' else 'h'))
            if current != target:
                raise LayoutError('Checked map wrapper/storage changed while reading')


def virtual_fields(h, address):
    info = h.object_type(address)
    if info.kind != 15:
        raise LayoutError('Expected virtual record')
    fields = h.ptr(info.parameter)
    count = h.unpack(info.parameter + 8, 'i')
    if not 0 <= count <= 128:
        raise LayoutError('Invalid virtual field count')
    result = {}
    for index in range(count):
        field = fields + index * 24
        name = h.utf16(h.ptr(field))
        tp = h.ptr(field + 8)
        slot = h.ptr(address + 24 + index * 8)
        if not slot:
            raise LayoutError('Virtual field requires unimplemented dynamic lookup: ' + name)
        result[name] = (tp, h.value_at(slot, tp))
    return result


def integer_map(h, address, limit=100000):
    if h.object_type(address).name != 'haxe.ds.IntMap':
        raise LayoutError('Expected IntMap')
    address = h.field(address, 'h')
    header = h.read(address, 64)
    cells, nexts, entries, values, free, head, nbuckets, ncells, nentries, capacity = struct.unpack(
        '<QQQQQiiiii4x', header)
    if not 0 <= nentries <= capacity <= limit or not 0 <= ncells <= limit:
        raise LayoutError('Invalid integer-map counts')
    if not nentries:
        return {}
    if not all((cells, nexts, entries, values, ncells)):
        raise LayoutError('Missing integer-map storage')
    fmt = 'b' if capacity < 128 else 'i'
    width = 1 if fmt == 'b' else 4
    buckets = h.read(cells, ncells * width)
    links = h.read(nexts, capacity * width)
    keys = h.read(entries, capacity * 4)
    vals = h.read(values, capacity * 8)
    active = set()
    result = {}
    for bucket in range(ncells):
        index = struct.unpack_from('<' + fmt, buckets, bucket * width)[0]
        while index >= 0:
            if index >= capacity or index in active:
                raise LayoutError('Invalid integer-map chain')
            active.add(index)
            key = struct.unpack_from('<i', keys, index * 4)[0]
            vp = struct.unpack_from('<Q', vals, index * 8)[0]
            if key in result:
                raise LayoutError('Duplicate integer-map key')
            if vp and h.object_type(vp).kind not in SCALAR_FORMATS:
                raise LayoutError('Non-scalar integer-map value is not supported')
            result[key] = h.dynamic(vp) if vp else None
            index = struct.unpack_from('<' + fmt, links, index * width)[0]
    if len(active) != nentries or h.read(address, 64) != header:
        raise LayoutError('Integer-map changed while reading')
    return result


def enum_values(h, address):
    info = h.object_type(address)
    if info.kind != 18:
        raise LayoutError('Expected enum')
    count = h.unpack(info.parameter + 8, 'i')
    index = h.unpack(address + 8, 'i')
    if not 0 <= index < count <= 1000:
        raise LayoutError('Invalid enum constructor')
    construct = h.ptr(info.parameter + 16) + index * 40
    name = h.utf16(h.ptr(construct))
    nparams = h.unpack(construct + 8, 'i')
    if not 0 <= nparams <= 32:
        raise LayoutError('Invalid enum parameter count')
    types = h.ptr(construct + 16)
    offsets = h.ptr(construct + 32)
    args = []
    for i in range(nparams):
        tp = h.ptr(types + i * 8)
        offset = h.unpack(offsets + i * 4, 'i')
        if not 8 <= offset <= 4096:
            raise LayoutError('Invalid enum parameter offset')
        args.append((tp, h.value_at(address + offset, tp)))
    return name, args


def safe_value(h, tp, value, depth=0):
    """Normalize selected values, excluding names, accounts and memory addresses."""
    if depth > 5:
        raise LayoutError('History value nesting exceeded')
    info = h.type_info(tp)
    if info.kind in SCALAR_FORMATS or info.kind == 0:
        return value
    if not value:
        return None
    if info.kind == 9:
        actual = h.object_type(value)
        if actual.kind in SCALAR_FORMATS:
            return h.dynamic(value)
        return safe_value(h, actual.address, value, depth + 1)
    actual = h.object_type(value)
    if actual.name == 'String':
        return string(h, value)
    if actual.kind == 15:
        return {k: safe_value(h, t, v, depth + 1)
                for k, (t, v) in virtual_fields(h, value).items()}
    if actual.kind == 18:
        name, args = enum_values(h, value)
        return {'kind': name, 'args': [safe_value(h, t, v, depth + 1) for t, v in args]}
    if actual.name in ('hl.types.ArrayObj', 'hl.types.ArrayDyn', 'hxbit.ArrayProxyData'):
        result = []
        for p in objects(h, value):
            if not p:
                result.append(None)
            else:
                result.append(safe_value(h, h.object_type(p).address, p, depth + 1))
        return result
    if actual.name == 'hl.types.ArrayBytes_Int':
        return h.int_array(value)
    if actual.name == 'haxe.ds.IntMap':
        return integer_map(h, value)
    if actual.name == 'haxe.ds.StringMap':
        result = h.string_map(value)
        # h.string_map unboxes primitives; complex entries retain object metadata.
        if any(isinstance(x, dict) for x in result.values()):
            return {'status': 'complex_map_not_exported', 'keys': sorted(result)}
        return result
    if actual.name == 'ent.Zone':
        return {'entity_type': actual.name, 'zone_id': h.field(value, 'id')}
    if actual.name in ('ent.Player', 'ent.Faction', 'ent.NeutralFaction'):
        out = {'entity_type': actual.name, 'entity_uid': h.field(value, '__uid')}
        if actual.name == 'ent.Player':
            out['clan'] = string(h, h.field(value, 'clan'))
        return out
    return {'entity_type': actual.name, 'status': 'unsupported'}
