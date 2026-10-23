"""Regenerate reviewable JSON Schema documents from the frozen Python contract."""
from pathlib import Path
from submission_contract import MANIFEST_SCHEMA,METADATA,RECORD_SCHEMAS,SUBMISSION_SCHEMA,json_bytes


def documents():
    return {'metadata-v1.schema.json':METADATA,'submission-v1.schema.json':SUBMISSION_SCHEMA,
            'capture-manifest-v1.schema.json':MANIFEST_SCHEMA,
            **{'record-'+name.split('.')[0]+'-v1.schema.json':schema for name,schema in RECORD_SCHEMAS.items()}}


if __name__=='__main__':
    target=Path(__file__).resolve().parents[1]/'schemas'
    target.mkdir(exist_ok=True)
    for name,schema in documents().items():
        (target/name).write_bytes(json_bytes({'$schema':'https://json-schema.org/draft/2020-12/schema',
            '$comment':'Generated from scripts/submission_contract.py; do not edit this copy alone.',**schema}))
