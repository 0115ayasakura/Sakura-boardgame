"""Offline-only lossless size experiment. NOT an Agent protocol or production codec.

Intern complete per-seat ledger facts, not current player state. No fields are
removed from each reconstructed record. JSON round-tripping is required by tests.
"""
import copy
import json


def pack(records):
    table, indexes, rows = [], {}, []
    for record in records:
        row = copy.deepcopy(record)
        refs = {}
        for seat, facts in row.pop('players').items():
            # Exact serialized identity avoids Python's True == 1 conflation.
            key = json.dumps(facts, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False)
            if key not in indexes:
                indexes[key] = len(table)
                table.append(copy.deepcopy(facts))
            refs[seat] = indexes[key]
        rows.append({'record': row, 'players_ref': refs})
    return {'format': 'offline-ledger-reference-v1', 'player_facts': table, 'rows': rows}


def unpack(packed):
    if packed['format'] != 'offline-ledger-reference-v1':
        raise ValueError('unsupported experimental format')
    records = []
    for row in packed['rows']:
        record = copy.deepcopy(row['record'])
        record['players'] = {
            seat: copy.deepcopy(packed['player_facts'][index])
            for seat, index in row['players_ref'].items()
        }
        records.append(record)
    return records
