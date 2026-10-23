"""Create local metadata templates and commands for one experimental 3v3 match.

Nothing is installed, launched, recorded or uploaded. Each participant uses
their own computer and chooses their own anonymized team label and slot.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import uuid


def shared_uuid(value):
    parsed = uuid.UUID(str(value))
    if parsed.version != 4 or str(parsed) != str(value):
        raise ValueError('Use a canonical lowercase UUIDv4 for the shared match ID')
    return str(parsed)


def create_kit(output, *, shared_match_id=None):
    shared = shared_uuid(shared_match_id) if shared_match_id is not None else str(uuid.uuid4())
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Output already exists; create a new kit for each match')
    output.mkdir(parents=True, exist_ok=False)
    commands = []
    templates = []
    for team in ('A', 'B'):
        for slot in (1, 2, 3):
            seat = f'{team}{slot}'
            link = {'shared_match_id': shared, 'declared_format': '3v3',
                    'team_label': team, 'player_slot': slot}
            metadata = {'consent_to_share': False, 'data_source': 'own_human_game',
                        'mode': 'multiplayer', 'clan': None, 'difficulty': None,
                        'map': {'name': None, 'seed': None, 'size': None},
                        'experience': 'unknown',
                        'goal': '请填写本局的人口分配、经济或出兵目标。',
                        'result': 'unknown', 'tags': ['3v3试采', '人口分配', '本人视角'],
                        'feedback': [], 'match_link': link}
            name = seat + '.metadata.json'
            (output / name).write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            templates.append(name)
            commands.extend([f'# {seat}: run from your own collector checkout after local setup',
                f'python collector/collect.py --shared-match-id {shared} --team-label {team} '
                f'--player-slot {slot} --declared-format 3v3 --interval 0.5 --out-dir captures/{shared}-{seat}', ''])
    (output / 'commands.txt').write_text('\n'.join(commands), encoding='utf-8')
    (output / 'README.txt').write_text(
        'Experimental 3v3 own-player capture kit\n'
        f'Shared match ID: {shared}\n\n'
        'Make a new kit for each match. Give each willing participant their own metadata template and command.\n'
        'One team can participate alone; the other team does not need to install anything.\n'
        'A/B and slots 1..3 are participant labels, not verified native team IDs or account identifiers.\n'
        'Run setup_local.py and doctor.py in each participant\'s own checkout before collection.\n'
        'Commands are instructions only; this script never starts a game, collector or upload.\n'
        'Each computer records its own player. Shared IDs do not provide teammate or opponent data access.\n'
        'Native mode/roster checks remain separate from these contributor declarations.\n'
        'After stopping, review the capture and fill in your own metadata and feedback.\n'
        'consent_to_share stays false until the participant authorizes the reviewed public contribution.\n'
        'Use prepare_submission.py and validate_submission.py, then the documented fork/PR flow.\n'
        'Never join units by UID across computers or equate a shared ID with verified clock synchronization.\n', encoding='utf-8')
    return {'shared_match_id': shared, 'output_directory': str(output),
            'metadata_templates': templates, 'commands_file': 'commands.txt',
            'game_launched': False, 'capture_started': False, 'upload_performed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--shared-match-id', type=shared_uuid)
    args = parser.parse_args()
    try:
        print(json.dumps(create_kit(args.output, shared_match_id=args.shared_match_id), ensure_ascii=False, indent=2))
    except (ValueError, OSError) as error:
        parser.exit(2, 'Could not create match kit: ' + str(error) + '\n')


if __name__ == '__main__':
    main()
