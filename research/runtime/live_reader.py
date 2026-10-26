"""Read-only Northgard state probe for the verified x64 build.

No screenshots/OCR, process writes, injection or remote function calls.
This development probe follows runtime type metadata rather than fixed offsets.
"""
from __future__ import annotations
import argparse
import ctypes as C
from ctypes import wintypes as W
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import struct
import sys
import time

from winmem import Reader, K
from runtime_types import HLReader, LayoutError

HERE=Path(__file__).resolve().parent

def find_pid():
    class ENTRY(C.Structure):
        _fields_=[('dwSize',W.DWORD),('cntUsage',W.DWORD),('pid',W.DWORD),
                  ('heap',C.c_size_t),('module',W.DWORD),('threads',W.DWORD),
                  ('parent',W.DWORD),('priority',W.LONG),('flags',W.DWORD),
                  ('exe',W.WCHAR*260)]
    K.CreateToolhelp32Snapshot.argtypes=[W.DWORD,W.DWORD]
    K.CreateToolhelp32Snapshot.restype=W.HANDLE
    K.Process32FirstW.argtypes=[W.HANDLE,C.POINTER(ENTRY)]
    K.Process32NextW.argtypes=[W.HANDLE,C.POINTER(ENTRY)]
    snap=K.CreateToolhelp32Snapshot(2,0)
    if snap==C.c_void_p(-1).value:raise OSError('Process inventory failed')
    hits=[]
    try:
        e=ENTRY();e.dwSize=C.sizeof(e)
        ok=K.Process32FirstW(snap,C.byref(e))
        while ok:
            if e.exe.lower()=='northgard.exe':hits.append(e.pid)
            ok=K.Process32NextW(snap,C.byref(e))
    finally:K.CloseHandle(snap)
    if len(hits)!=1:raise ValueError(f'Expected one Northgard.exe, found {len(hits)}')
    return hits[0]

def locate(r,h,profile,refresh=False):
    cache=HERE/'reader_locator_cache.json'
    def validate(base):
        for name,idx in profile['types'].items():
            if h.type_info(base+idx*32).name!=name:raise LayoutError('Type array mismatch')
        return base
    if cache.exists() and not refresh:
        try:
            data=json.loads(cache.read_text(encoding='utf-8'))
            if data['pid']==r.pid:return validate(data['type_base'])
        except (ValueError,OSError,KeyError,struct.error):pass
    name='ent.player.ResourcesComponent'
    # The UTF16 metadata name is unique in this verified build. Stop the scan
    # immediately rather than traversing graphics buffers after the match.
    first=next(r.scan({'name':(name+'\0').encode('utf-16-le')},max_total=4*1024**3),None)
    names=[first[1]] if first else []
    patterns={str(i):struct.pack('<Q',p) for i,p in enumerate(names)}
    if not patterns:raise LayoutError('Resource type name not found')
    for _,ref in r.scan(patterns,max_total=4*1024**3):
        if ref%8:continue
        obj=ref-16
        try:
            rt=r.u64(obj+72)
            if not rt or rt%8:continue
            tp=r.u64(rt)
            info=h.type_info(tp)
            if info.name!=name or info.parameter!=obj:continue
            base=validate(tp-profile['types'][name]*32)
            cache.write_text(json.dumps({'pid':r.pid,'type_base':base,
                'hlboot_sha256':profile['hlboot_sha256']},indent=2),encoding='utf-8')
            return base
        except (ValueError,OSError,struct.error):continue
    raise LayoutError('Validated runtime types not found')

def string(h,p):
    if not p:return None
    if h.object_type(p).name!='String':raise LayoutError('Expected Haxe String')
    n=h.field(p,'length')
    if not 0<=n<=512:raise LayoutError('Invalid string length')
    return h.read(h.field(p,'bytes'),n*2).decode('utf-16-le')

def objects(h,p,limit=10000):
    """Unwrap hxbit/Haxe arrays while respecting logical length, not capacity."""
    for _ in range(5):
        name=h.object_type(p).name
        if name=='hl.types.ArrayObj':
            n=h.field(p,'length')
            if not 0<=n<=limit:raise LayoutError('Invalid object array length')
            arr=h.native_array(h.field(p,'array'),limit=limit*2)
            if n>arr['size']:raise LayoutError('Length exceeds capacity')
            return arr['values'][:n]
        if name in ('hxbit.ArrayProxyData','hl.types.ArrayDyn'):
            p=h.field(p,'array');continue
        raise LayoutError('Unsupported array wrapper: '+name)
    raise LayoutError('Array wrapper nesting exceeded')


def read_discovered_tiles(h, state, me, discovered):
    """Read own tile contents; every other discovered tile is an ID only.

    Discovery is not current visibility. In particular, an entry in the local
    discovered set never permits reading a non-owned zone's building array.
    All checks use stored fields; no native getters or game functions run.
    """
    if (type(discovered) is not list or len(discovered) > 10000
            or any(type(zid) is not int or not 0 <= zid < 2**31 for zid in discovered)
            or len(discovered) != len(set(discovered))):
        raise LayoutError('Invalid discovered zone IDs')
    wanted = set(discovered)
    if h.field(state, 'me') != me:
        raise LayoutError('Discovered tile scope is not the local player')
    world = h.field(me, 'world')
    if not world or h.object_type(world).name != 'ent.player.WorldComponent':
        raise LayoutError('Expected local WorldComponent for discovered tiles')

    def check_scope():
        if (h.field(state, 'me') != me or h.field(me, 'world') != world
                or h.int_array(h.field(world, 'discoveredIds')) != discovered):
            raise LayoutError('Discovered tile knowledge scope changed')

    check_scope()
    zone_array = h.field(state, 'zones')
    zone_pointers = objects(h, zone_array)
    if len(zone_pointers) != len(set(zone_pointers)):
        raise LayoutError('Duplicate zone object in current map')
    tiles, seen, owners, own_arrays = [], set(), [], []
    for zone in zone_pointers:
        if h.object_type(zone).name != 'ent.Zone':
            raise LayoutError('Unexpected zone type in current map')
        zid = h.field(zone, 'id')
        if type(zid) is not int or not 0 <= zid < 2**31:
            raise LayoutError('Invalid map zone ID')
        if zid not in wanted:
            continue
        if zid in seen:
            raise LayoutError('Duplicate discovered zone ID in current map')
        seen.add(zid)
        owner = h.field(zone, 'owner')
        owners.append((zone, zid, owner))
        tile = {'id': zid, 'knowledge_scope': 'discovered_id_only',
                'current_visibility': 'unverified'}
        if owner == me:
            capacity = h.field(zone, 'maxBuildings')
            if type(capacity) is not int or not 0 <= capacity <= 1000:
                raise LayoutError('Invalid own-zone building capacity')
            if h.field(zone, 'owner') != me:
                raise LayoutError('Own-zone ownership changed before contents read')
            building_array = h.field(zone, 'buildings')
            building_pointers = objects(h, building_array)
            if len(building_pointers) != len(set(building_pointers)):
                raise LayoutError('Duplicate own-zone building object')
            kinds = []
            for building in building_pointers:
                # Check the stored parent pointer before reading its kind.
                if h.field(zone, 'owner') != me or h.field(building, 'zone') != zone:
                    raise LayoutError('Building is not in the expected own zone')
                kind = string(h, h.field(building, 'kind'))
                if type(kind) is not str or not 0 < len(kind) <= 128:
                    raise LayoutError('Invalid own-zone building kind')
                if h.field(building, 'zone') != zone or h.field(zone, 'owner') != me:
                    raise LayoutError('Own building scope changed during read')
                kinds.append(kind)
            own_arrays.append((zone, building_array, building_pointers))
            tile = {'id': zid, 'knowledge_scope': 'owned_zone', 'owner': 'self',
                    'base_building_slots': capacity, 'buildings': kinds}
        tiles.append(tile)
    if seen != wanted:
        raise LayoutError('Discovered IDs missing from current map')
    check_scope()
    if (h.field(state, 'zones') != zone_array
            or objects(h, zone_array) != zone_pointers):
        raise LayoutError('Map zone array changed during discovered tile read')
    for zone, zid, owner in owners:
        final_id = h.field(zone, 'id')
        if type(final_id) is not int or final_id != zid or h.field(zone, 'owner') != owner:
            raise LayoutError('Discovered zone identity or ownership changed')
    for zone, array, pointers in own_arrays:
        if (h.field(zone, 'buildings') != array or objects(h, array) != pointers
                or any(h.field(building, 'zone') != zone for building in pointers)):
            raise LayoutError('Own-zone building membership changed')
    check_scope()
    if any(h.field(zone, 'owner') != owner for zone, _, owner in owners):
        raise LayoutError('Discovered zone ownership changed at read end')
    return tiles


class Probe:
    def __init__(self,r,refresh=False):
        self.r=r;self.h=HLReader(r.read)
        self.profile=json.loads((HERE/'build_profile.json').read_text(encoding='utf-8'))
        self.game_root=Path(r.image_path()).resolve().parent
        if Path(r.image_path()).name.lower()!='northgard.exe':
            raise ValueError('Process image is not Northgard.exe')
        actual=hashlib.sha256((self.game_root/'hlboot.dat').read_bytes()).hexdigest()
        if actual!=self.profile['hlboot_sha256']:
            raise ValueError('Game bytecode differs from verified build; regenerate/verify profile')
        self.compatibility={'hlboot_sha256':actual,'data_cdb_verified':False}
        expected_cdb=self.profile.get('data_cdb_sha256')
        if expected_cdb:
            sys.path.insert(0,str(HERE.parent))
            from inspect_northgard_assets import read_pak_index,read_entry
            pak=self.game_root/'res.client.pak'
            index=read_pak_index(pak)
            entry=next((e for e in index['entries'] if e['path']=='data.cdb'),None)
            if entry is None:raise ValueError('Installed resource pack lacks data.cdb')
            actual_cdb=hashlib.sha256(read_entry(pak,entry)).hexdigest()
            if actual_cdb!=expected_cdb:
                raise ValueError('Game data.cdb differs from verified rules; collection disabled')
            self.compatibility.update(data_cdb_sha256=actual_cdb,data_cdb_verified=True)
        self.base=locate(r,self.h,self.profile,refresh)
    def sample(self):
        h=self.h
        game_type=h.type_info(self.base+self.profile['types']['Game']*32)
        static=self.r.u64(self.r.u64(game_type.parameter+56))
        if h.object_type(static).name!='$Game':raise LayoutError('Static Game class mismatch')
        game=h.field(static,'inst')
        state=h.field(game,'state') if game else 0
        me=h.field(state,'me') if state else 0
        if not me:return {'status':'not_in_match','source':'ReadProcessMemory'}
        if h.object_type(me).name!='ent.Player':raise LayoutError('Local player mismatch')
        t0=h.field(state,'time')
        res=h.field(me,'res');world=h.field(me,'world')
        raw=h.string_map(h.field(res,'resources'))
        stocks={'Food':h.field(res,'food'),'Wood':h.field(res,'wood'),
                'Money':h.field(res,'money')}
        # getResource default map path converts null to 0 (verified bytecode + cast.c).
        for key in ('Stone','Iron','Lore'):
            stocks[key]=raw.get(key,0.0)
        discovered=h.int_array(h.field(world,'discoveredIds'))
        tiles=read_discovered_tiles(h,state,me,discovered)
        population=None;population_error=None
        try:
            from population_read import read_population
            population=read_population(h,me)
        except (ImportError,ValueError,OSError,KeyError) as e:
            population_error=str(e)
        t1=h.field(state,'time')
        if h.field(static,'inst')!=game or h.field(game,'state')!=state or h.field(state,'me')!=me:
            raise LayoutError('Match changed during sample')
        return {'schema_version':1,'status':'ok','source':'ReadProcessMemory',
                'captured_at':datetime.now(timezone.utc).isoformat(),
                'pid':self.r.pid,'game_build':self.profile['game_build'],
                'game_time_seconds':t1,'sample_span_game_seconds':t1-t0,
                'atomic':False,'clan':string(h,h.field(me,'clan')),
                'calendar':{'year':800+int(t1/60+2)//12,'month':int(t1/60+2)%12+1},
                'in_winter':h.field(state,'inWinter'),
                'stocks':stocks,'raw_resource_map':raw,
                'population':population['population'] if population else None,
                'population_cap':h.field(res,'nbMaxPop'),
                'population_details':population,'population_error':population_error,
                'discovered_zone_ids':discovered,'discovered_tiles':tiles,
                'scope':'local player; exported tiles filtered by discovered IDs; no enemy names or account IDs'}

def main():
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pid',type=int);p.add_argument('--seconds',type=float,default=10)
    p.add_argument('--interval',type=float,default=1);p.add_argument('--refresh',action='store_true')
    p.add_argument('--out',type=Path,default=HERE/'captures'/'probe.ndjson')
    a=p.parse_args()
    if not (0<=a.seconds<=3600 and 0.1<=a.interval<=60):p.error('Invalid sampling duration/interval')
    a.out.parent.mkdir(parents=True,exist_ok=True)
    with Reader(a.pid or find_pid()) as r:
        probe=Probe(r,a.refresh);end=time.monotonic()+a.seconds;seq=0
        with a.out.open('w',encoding='utf-8') as f:
            while True:
                start=time.monotonic()
                try:row=probe.sample()
                except (OSError,ValueError,KeyError,struct.error) as e:
                    row={'status':'read_error','error':str(e),'source':'ReadProcessMemory'}
                row['sequence']=seq;seq+=1
                f.write(json.dumps(row,ensure_ascii=False)+'\n');f.flush()
                latest=a.out.with_suffix('.latest.json');tmp=latest.with_suffix('.tmp')
                tmp.write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(latest)
                print(json.dumps({k:row.get(k) for k in ('sequence','status','game_time_seconds',
                      'stocks','population','population_cap','discovered_zone_ids','error')},ensure_ascii=False),flush=True)
                if time.monotonic()>=end:break
                time.sleep(max(0,a.interval-(time.monotonic()-start)))

if __name__=='__main__':main()
