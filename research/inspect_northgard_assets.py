"""Read-only local Northgard inspection; never changes the game installation.

PAK v0 layout follows the Reader shipped with Northgard's own NGEditor
package.nw/hide.js (hxd.fmt.pak.Reader). Only the index is read by default.
"""
from __future__ import annotations

import argparse
import collections
import csv
import io
import json
from pathlib import Path
import struct
import xml.etree.ElementTree as ET


def read_pak_index(path: Path):
    with path.open('rb') as f:
        magic, version, header_size, data_size = struct.unpack('<3sBII', f.read(12))
        if magic != b'PAK' or version != 0:
            raise ValueError('Unsupported PAK format')
        if not 16 <= header_size <= min(path.stat().st_size, 64 * 1024 * 1024):
            raise ValueError('Invalid header size')
        buf = io.BytesIO(f.read(header_size - 16))
        if f.read(4) != b'DATA':
            raise ValueError('Missing DATA marker')

    def unpack(fmt):
        size = struct.calcsize(fmt)
        data = buf.read(size)
        if len(data) != size:
            raise ValueError('Truncated index')
        return struct.unpack(fmt, data)[0]

    entries = []

    def recurse(parent):
        name = buf.read(unpack('<B')).decode('utf-8')
        flags = unpack('<B')
        rel = '/'.join(p for p in (parent, name) if p)
        if flags & 1:
            count = unpack('<I')
            for _ in range(count):
                recurse(rel)
        else:
            pos = unpack('<d') if flags & 2 else unpack('<I')
            size, checksum = unpack('<I'), unpack('<I')
            absolute = header_size + int(pos)
            if absolute < header_size or absolute + size > path.stat().st_size:
                raise ValueError('Entry outside archive: ' + rel)
            entries.append(dict(path=rel, size=size, offset=absolute, checksum=checksum))

    recurse('')
    return dict(version=version, header_size=header_size, data_size=data_size,
                actual_size=path.stat().st_size, entries=entries)


def read_entry(pak: Path, entry, max_size=32 * 1024 * 1024):
    if entry['size'] > max_size:
        raise ValueError('Refusing oversized extraction')
    with pak.open('rb') as f:
        f.seek(entry['offset'])
        return f.read(entry['size'])


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--extract', action='append', default=[])
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    pak = args.root / 'res.client.pak'
    parsed = read_pak_index(pak)
    with (args.out / 'pak_index.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['path', 'size', 'offset', 'checksum'])
        writer.writeheader()
        writer.writerows(parsed['entries'])
    exts = collections.Counter(Path(e['path']).suffix for e in parsed['entries'])
    summary = {k: v for k, v in parsed.items() if k != 'entries'}
    summary['entry_count'] = len(parsed['entries'])
    summary['extensions'] = dict(exts.most_common())
    summary['data_candidates'] = [e for e in parsed['entries'] if
        Path(e['path']).suffix in ('.cdb', '.xml', '.json', '.hx') or
        any(s in e['path'].lower() for s in ('localiz', 'locale', 'database'))]
    api = args.root / 'NGEditor' / 'res' / 'api.xml'
    tree = ET.parse(api)
    api_summary = []
    for cls in tree.getroot():
        if cls.get('path', '') in ('GameState', 'Player', 'Zone', 'ResourceKind',
                'ClanKind', 'BuildingKind', 'UnitKind', 'ScriptApi', 'Building', 'Unit'):
            fields = []
            for field in cls:
                if field.tag in ('meta', 'haxe_doc', 'implements', 'extends'):
                    continue
                doc = field.findtext('haxe_doc', '').strip()
                fields.append(dict(name=field.tag, attributes=field.attrib, doc=doc))
            api_summary.append(dict(kind=cls.tag, name=cls.get('path'), fields=fields))
    (args.out / 'official_api_summary.json').write_text(
        json.dumps(api_summary, ensure_ascii=False, indent=2), encoding='utf-8')
    for rel in args.extract:
        entry = next((e for e in parsed['entries'] if e['path'] == rel), None)
        if entry is None:
            raise ValueError('No such entry: ' + rel)
        target = (args.out / 'extracted' / rel).resolve()
        allowed = (args.out / 'extracted').resolve()
        if not target.is_relative_to(allowed):
            raise ValueError('Unsafe relative path')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(read_entry(pak, entry))
    (args.out / 'pak_summary.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in summary.items() if k != 'data_candidates'},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
