"""Check local setup without starting or controlling a game."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup_local import choose_installation, sha256_file
from local_rules import verify_generated_rules
from inspect_northgard_assets import read_pak_index, read_entry
import hashlib


def diagnose(game_directory=None):
    result = {'python_supported': sys.version_info >= (3, 11),
              'platform_supported': sys.platform == 'win32' and struct.calcsize('P') == 8,
              'game_process_required_for_this_check': False, 'network_upload': False,
              'generated_rules_present': verify_generated_rules(ROOT)}
    profile = json.loads((ROOT/'research/runtime/build_profile.json').read_text(encoding='utf-8'))
    try:
        game = choose_installation(game_directory)
        result['installed_bytecode_matches'] = sha256_file(game/'hlboot.dat') == profile['hlboot_sha256']
        result['resource_pack_present'] = (game/'res.client.pak').is_file()
        entry=next((e for e in read_pak_index(game/'res.client.pak')['entries'] if e['path']=='data.cdb'),None)
        result['installed_rules_match'] = entry is not None and hashlib.sha256(read_entry(game/'res.client.pak',entry)).hexdigest()==profile['data_cdb_sha256']
    except (ValueError, OSError, KeyError):
        result['installed_bytecode_matches'] = False
        result['resource_pack_present'] = False
        result['installed_rules_match'] = False
    result['status'] = 'ready_for_local_test' if all(result[k] for k in
        ['python_supported','platform_supported','generated_rules_present','installed_bytecode_matches','resource_pack_present','installed_rules_match']) else 'setup_required'
    result['live_validation'] = 'not_run'
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-dir',type=Path)
    data=diagnose(parser.parse_args().game_dir)
    print(json.dumps(data,ensure_ascii=False,indent=2))
    raise SystemExit(0 if data['status']=='ready_for_local_test' else 2)
