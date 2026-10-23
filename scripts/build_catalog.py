"""Build a small deterministic index from validated contributions; no uploads."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from validate_submission import validate_tree
from submission_contract import clock,json_bytes


def build_catalog(root):
    return {'schema_version':1,'matches':[{
        'match_id':item['match_id'],'created_date':item['created_date'],
        'clan':item['metadata']['clan'],'mode':item['metadata']['mode'],
        'difficulty':item['metadata']['difficulty'],'experience':item['metadata']['experience'],
        'result':item['metadata']['result'],'tags':item['metadata']['tags'],
        'collector_version':item['collector_version'],'game_build':item['game_build'],
        'game_fingerprint':item['game_fingerprint'],
        'start_clock':clock(item['time_start_game_seconds']),'end_clock':clock(item['time_end_game_seconds']),
        'feedback_count':len(item['metadata']['feedback']),
        'path':f"data/submissions/{item['created_date'][:4]}/{item['match_id']}"}
        for item in validate_tree(root)]}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('data/submissions'))
    parser.add_argument('--output',type=Path,default=Path('data/catalog.json'))
    parser.add_argument('--check',action='store_true',help='Fail if the existing index differs; do not write')
    args=parser.parse_args()
    output=build_catalog(args.root)
    raw=json_bytes(output)
    if args.check:
        if not args.output.is_file() or args.output.is_symlink() or args.output.read_bytes()!=raw:
            parser.exit(2,'Catalog is missing, stale or contains unvalidated fields; regenerate it.\n')
    else:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_bytes(raw)
    print(json.dumps({'matches':len(output['matches']),'output':str(args.output)}))
