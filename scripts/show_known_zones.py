"""Inspect one recorded known-zone snapshot. No game/process/network access."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from companion.core.known_zone_details import validate_known_zone_details

KINDS_ZH = {'Stones': '石矿', 'IronDeposit': '铁矿', 'Fertility': '农田',
            'Shoal': '鱼源', 'Grove': '森林', 'RuneStone': '符文石',
            'CircleStone': '石圈', 'DeerSpawner': '鹿源', 'Wolf': '狼', 'Bear': '熊'}
STATES_ZH = {'ok': '支持范围内已读', 'partial': '部分可读', 'unavailable': '读取不可用',
             'unsupported_context': '本场景尚未支持', 'rejected': '校验未通过',
             'missing': '此帧没有该组件'}
IDENTITIES = ('session_key', 'capture_run_id', 'capture_segment_id')


def game_calendar(seconds):
    if (type(seconds) not in (float, int) or not 0 <= seconds < 2**53
            or not math.isfinite(seconds)):
        raise ValueError('Invalid game clock')
    whole_months = int(seconds // 60)
    month_index = 800 * 12 + 2 + whole_months
    phase = ('初', '中', '末')[min(2, int((seconds % 60) // 20))]
    return f'A{month_index // 12}年{month_index % 12 + 1}月{phase}'


def select_observation(path, *, at=None, session=None):
    """Use the latest segment; never fill a failed/missing component from history."""
    if at is not None and (type(at) not in (float, int) or not math.isfinite(at) or at < 0):
        raise ValueError('Invalid requested game clock')
    selected = None
    scope = None
    last_clock = None
    with Path(path).open('rb') as stream:
        while True:
            raw = stream.readline(2 * 1024 * 1024 + 1)
            if not raw:
                break
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError('Recorded observation exceeds limit')
            # Reading an actively appended file can encounter an unfinished
            # last record. Do not reinterpret its partial fields as a sample.
            if not raw.endswith(b'\n'):
                raise ValueError('Last observation is incomplete; retry after the write finishes')
            row = json.loads(raw)
            if type(row) is not dict or row.get('status') != 'ok':
                continue
            if session is not None and row.get('session_key') != session:
                continue
            current = tuple(row.get(k) for k in IDENTITIES)
            if any(type(item) is not str or not item for item in current):
                raise ValueError('Recorded observation has no capture identity')
            when = row.get('game_time_seconds')
            game_calendar(when)
            if current != scope:
                selected, last_clock, scope = None, None, current
            if last_clock is not None and when < last_clock:
                raise ValueError('Game clock rolled back without a new segment')
            last_clock = when
            if at is None or when <= at:
                selected = row
    if selected is None:
        raise ValueError('No observation matches the requested latest segment/time')
    return selected


def build_report(row):
    if type(row) is not dict or row.get('status') != 'ok':
        raise ValueError('A recorded accepted observation is required')
    when = row.get('game_time_seconds')
    report = {'report_kind': 'recorded_observation_not_live_world_state',
              'game_time_seconds': when, 'game_calendar': game_calendar(when),
              'component_status': 'missing', 'zones': [],
              'reason_codes': ['known_zone_details_not_in_this_observation'],
              'limits': ['not_a_complete_resource_or_neutral_roster',
                         'unknown_is_not_zero', 'configuration_is_not_actual_production',
                         'no_historical_component_backfill']}
    component = row.get('known_zone_details')
    if component is None:
        return report
    validate_known_zone_details(component, row, projected=True)
    report['component_status'] = component['status']
    report['zones'] = deepcopy(component.get('zones', []))
    report['reason_codes'] = list(component.get('reason_codes', []))
    return report


def format_report(report):
    lines = [f"历史采集快照：{report['game_calendar']}（游戏秒 {report['game_time_seconds']:.3f}）",
             '这是该时刻保存的观察，不代表游戏此刻的实时状态。',
             f"地块详情：{STATES_ZH.get(report['component_status'], report['component_status'])}"]
    for zone in report['zones']:
        lines.append(f"\n地块 {zone['zone_id']}：资源{STATES_ZH.get(zone['resources_status'], zone['resources_status'])}；"
                     f"野兽{STATES_ZH.get(zone['wildlife_status'], zone['wildlife_status'])}")
        for resource in zone['resources']:
            kind = resource['kind']
            text = f"  {KINDS_ZH[kind]} × {resource['object_count']}"
            if kind in ('Stones', 'IronDeposit'):
                amount = resource['remaining_total_raw']
                text += '；剩余总量 ' + ('未知' if amount is None else f'{amount:g}')
                progress = [d['next_extraction_progress_raw'] for d in resource['deposits']]
                text += '；各矿下一单位进度 ' + ', '.join('未知' if v is None else f'{v:g}' for v in progress)
            else:
                text += '；矿藏式剩余量不适用'
            lines.append(text)
        if not zone['resources']:
            lines.append('  本帧没有可展示的资源条目；结合资源覆盖状态解释。')
        for group in zone['wildlife']:
            count = group['count']
            lines.append(f"  {KINDS_ZH[group['kind']]}：{'未知' if count is None else count}")
        lines.append('  静态岗位/加成属性见 --json 中的 reference_properties；不代表实际产率。')
    if report['reason_codes']:
        lines.append('\n原因代码：' + ', '.join(report['reason_codes']))
    lines.append('\n未覆盖种类和特殊状态仍未知；狼熊计数不能独立证明可以殖民。')
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', type=Path, required=True, help='Local capture directory')
    parser.add_argument('--game-seconds', type=float, help='Latest recorded observation at/before this simulation time')
    parser.add_argument('--session', help='Optional exact local capture session')
    parser.add_argument('--json', action='store_true', help='Include reviewed default resource properties')
    args = parser.parse_args(argv)
    try:
        row = select_observation(args.capture / 'player-observations.ndjson',
                                 at=args.game_seconds, session=args.session)
        report = build_report(row)
    except (ValueError, KeyError, TypeError, OSError):
        parser.exit(2, '无法读取或校验该时刻的地块详情；请检查目录、时间及采集文件是否已写完。\n')
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
          if args.json else format_report(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
