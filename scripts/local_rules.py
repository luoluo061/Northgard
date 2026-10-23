"""Verify locally generated rule files, without accessing the game or network."""
import hashlib
import json
from pathlib import Path

NAMES=('unit.json','resource.json','labor_rules.json')


def verify_generated_rules(root):
    root=Path(root)
    try:
        manifest=json.loads((root/'.local/rules-manifest.json').read_text(encoding='utf-8'))
        profile=json.loads((root/'research/runtime/build_profile.json').read_text(encoding='utf-8'))
        if manifest.get('schema_version')!=1 or any(manifest.get(k)!=profile.get(k)
                for k in ('hlboot_sha256','data_cdb_sha256')) or set(manifest.get('files',{}))!=set(NAMES):
            return False
        for name in NAMES:
            path=root/'research/static'/name
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=manifest['files'][name]:
                return False
        return True
    except (OSError,ValueError,KeyError,TypeError):
        return False
