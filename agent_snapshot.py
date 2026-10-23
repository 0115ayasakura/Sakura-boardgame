"""Versioned, lossless history encoding for Agent output only.

The engine/save/UI protocol remains unchanged. Each action stays in the records
array with every non-players field intact. Only complete per-seat historical
players facts are interned; no delta inference or current player state is used.
Both directions return detached copies, including between reconstructed rows.
"""
from __future__ import annotations

import copy
import json


FORMAT = 'sakura-agent-action-records-v1'
ENCODING_KEY = 'action_records_encoding'
READING_HINT = (
    '这是Agent专用v1引用格式，不是原始存档格式。action_records仍是完整有序记录数组；'
    '每条players[席位]中的{"$ref":N}表示本board.action_records_encoding.player_facts[N]'
    '（从0开始）的完整历史事实，包含before/after/changed等全部原字段；'
    '同一引用仅表示事实完全相同，不代表省略记录，也不要用当前玩家状态替代。其他字段原样保留。'
)


def encode_board(board):
    """Copy a raw engine board, sharing only exact JSON-identical players facts.

    Boards without action_records (e.g. Gomoku) keep their original schema.
    The reserved metadata key is never silently overwritten or double-encoded.
    """
    result = copy.deepcopy(board)
    if ENCODING_KEY in result:
        raise ValueError('board already has Agent action-record encoding metadata')
    if 'action_records' not in result:
        return result
    table, indexes = [], {}
    for row in result['action_records']:
        if 'players' not in row:
            continue
        refs = {}
        for seat, facts in row['players'].items():
            # JSON identity distinguishes bool/int/float, null and nested values;
            # dictionary insertion order must not prevent factual deduplication.
            key = json.dumps(facts, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False)
            if key not in indexes:
                indexes[key] = len(table)
                table.append(copy.deepcopy(facts))
            refs[seat] = {'$ref': indexes[key]}
        row['players'] = refs
    result[ENCODING_KEY] = {'format': FORMAT, 'player_facts': table, 'readme': READING_HINT}
    return result


def decode_board(board):
    """Reconstruct the raw board after JSON transport, without shared aliases.

    Unencoded boards are copied as-is. Reject unknown versions and malformed
    references rather than interpreting them as missing or current-state facts.
    """
    result = copy.deepcopy(board)
    if ENCODING_KEY not in result:
        return result
    metadata = result.pop(ENCODING_KEY)
    if not isinstance(metadata, dict) or metadata.get('format') != FORMAT:
        raise ValueError('unsupported Agent action-record encoding format')
    table = metadata.get('player_facts')
    records = result.get('action_records')
    if not isinstance(table, list) or not isinstance(records, list):
        raise ValueError('invalid Agent action-record encoding table or records')
    for row in records:
        if 'players' not in row:
            continue
        players = {}
        for seat, ref in row['players'].items():
            if (not isinstance(ref, dict) or set(ref) != {'$ref'}
                    or type(ref['$ref']) is not int or not 0 <= ref['$ref'] < len(table)):
                raise ValueError('invalid Agent historical player-fact reference')
            players[seat] = copy.deepcopy(table[ref['$ref']])
        row['players'] = players
    return result
