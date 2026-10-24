"""Atomic service actions and bounded request replay; no engine/HTTP dependencies.

A transaction owns one in-memory checkpoint and one final atomic save. Nested
service methods join it; a swallowed nested failure marks the whole unit rollback
only. Notifications are an outbox published only after persistence succeeds.
"""
from __future__ import annotations

import copy
import json
from collections import OrderedDict
from collections.abc import Mapping

CACHE_LIMIT = 128
CACHE_BYTES = 2 * 1024 * 1024
MUTATING_METHODS = frozenset({
    'handle_action', 'start', 'roll', 'decide', 'place', 'tower_action',
    'sakura_action', 'auto', 'undo', 'end',
})
SNAPSHOT_FIELDS = (
    '_version', '_game_seq', '_play_paused', '_log', '_lines_written', '_last_event', '_snapshots',
    '_busy_at', '_last_talk', '_opponent', '_persona', '_request_cache',
)
_MISSING = object()


class ProtocolError(ValueError):
    def __init__(self, code, message, status=409):
        super().__init__(message)
        self.code, self.status = code, status


def initialize(service):
    if not hasattr(service, '_request_cache'):
        service._request_cache = OrderedDict()


def protocol_state(service):
    initialize(service)
    return {'version': service._version, 'gameSeq': getattr(service, '_game_seq', 0),
            'requests': copy.deepcopy(list(service._request_cache.items()))}


def restore_protocol(service, data):
    """Only service-authored bounded JSON; malformed optional metadata is ignored."""
    initialize(service)
    if not isinstance(data, dict):
        return
    for key, attr in (('version', '_version'), ('gameSeq', '_game_seq')):
        value = data.get(key)
        if type(value) is int and value >= 0:
            setattr(service, attr, value)
    rows = data.get('requests')
    if not isinstance(rows, list):
        return
    for row in rows[-CACHE_LIMIT:]:
        if (isinstance(row, (list, tuple)) and len(row) == 2 and isinstance(row[0], str)
                and 0 < len(row[0]) <= 128 and isinstance(row[1], dict)
                and isinstance(row[1].get('fingerprint'), str)
                and isinstance(row[1].get('result'), dict)):
            service._request_cache[row[0]] = copy.deepcopy(row[1])
    _trim(service._request_cache)


def _trim(cache):
    while cache and (len(cache) > CACHE_LIMIT or
                     len(json.dumps(list(cache.items()), ensure_ascii=False).encode('utf8')) > CACHE_BYTES):
        cache.popitem(last=False)


def _metadata(metadata):
    if not isinstance(metadata, Mapping) or set(metadata) != {'requestId', 'gameSeq', 'expectedVersion'}:
        raise ProtocolError('INVALID_REQUEST_METADATA', '请求必须包含requestId、gameSeq和expectedVersion。', 400)
    request_id = metadata['requestId']
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
        raise ProtocolError('INVALID_REQUEST_METADATA', 'requestId必须是1至128字符的非空字符串。', 400)
    if any(type(metadata[key]) is not int or metadata[key] < 0 for key in ('gameSeq', 'expectedVersion')):
        raise ProtocolError('INVALID_REQUEST_METADATA', 'gameSeq和expectedVersion必须是非负整数。', 400)
    return dict(metadata)


def _failure(service, error, metadata=None):
    result = {'ok': False, 'error': str(error), 'code': error.code,
              'httpStatus': error.status, 'gameSeq': getattr(service, '_game_seq', 0),
              'version': service._version, 'duplicate': False}
    if isinstance(metadata, Mapping) and isinstance(metadata.get('requestId'), str):
        result['requestId'] = metadata['requestId']
    return result


def _checkpoint(service):
    game = service._game
    return {
        'game': game,
        # Unlike to_dict/from_dict, this preserves transient FX, every RNG counter,
        # tower state and engine-only fields without invoking save migration.
        'game_state': copy.deepcopy(game.__dict__) if game is not None else None,
        'fields': {key: copy.deepcopy(getattr(service, key)) if hasattr(service, key) else _MISSING
                   for key in SNAPSHOT_FIELDS},
        'rng': service._rng.getstate() if hasattr(service, '_rng') else None,
    }


def _restore(service, snap):
    game = snap['game']
    service._game = game
    if game is not None:
        game.__dict__.clear()
        game.__dict__.update(snap['game_state'])
    for key, value in snap['fields'].items():
        if value is _MISSING:
            service.__dict__.pop(key, None)
        else:
            setattr(service, key, value)
    if snap['rng'] is not None:
        service._rng.setstate(snap['rng'])


def run(service, invoke, *, metadata=None, action=None, arguments=None):
    """Called under the service RLock. Returns an owned result, never a cached alias."""
    initialize(service)
    active = getattr(service, '_action_transaction', None)
    if active is not None:
        try:
            return invoke()
        except Exception as error:
            active['error'] = error
            raise

    request = None
    fingerprint = None
    if metadata is not None:
        try:
            request = _metadata(metadata)
            fingerprint = json.dumps([action, dict(arguments or {}), request],
                                     ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
            cached = service._request_cache.get(request['requestId'])
            if cached is not None:
                if cached['fingerprint'] != fingerprint:
                    raise ProtocolError('REQUEST_ID_REUSED', '同一requestId不能用于不同请求。')
                result = copy.deepcopy(cached['result'])
                result['duplicate'] = True
                return result
            if request['gameSeq'] != getattr(service, '_game_seq', 0):
                raise ProtocolError('STALE_GAME', '对局已改变，请刷新棋盘后重新操作。')
            if request['expectedVersion'] != service._version:
                raise ProtocolError('STALE_VERSION', '棋盘状态已改变，请刷新后重新操作。')
        except ProtocolError as error:
            return _failure(service, error, metadata)
        except (TypeError, ValueError):
            return _failure(service, ProtocolError('INVALID_REQUEST_METADATA', '请求不是有效JSON。', 400), metadata)

    checkpoint = _checkpoint(service)
    transaction = {'dirty': False, 'delete': False, 'outbox': [], 'error': None}
    service._action_transaction = transaction
    try:
        result = invoke()
        if transaction['error'] is not None:
            raise transaction['error']
        if isinstance(result, dict) and result.get('ok') is False:
            _restore(service, checkpoint)
        else:
            if request is not None:
                result = {**result, 'requestId': request['requestId'],
                          'gameSeq': getattr(service, '_game_seq', 0),
                          'version': service._version, 'duplicate': False}
                service._request_cache[request['requestId']] = {
                    'fingerprint': fingerprint, 'result': copy.deepcopy(result)}
                _trim(service._request_cache)
                # Config is an independent host-owned persistent resource. Pure
                # set_* requests must not acquire a second state.json commit that
                # could fail after config.update already succeeded. Their replay
                # cache is process-local until a later natural game save.
                if not (isinstance(action, str) and action.startswith('set_')):
                    transaction['dirty'] = True
            if transaction['delete']:
                service._delete_save_now()
            elif transaction['dirty']:
                service._save_now()
    except Exception:
        _restore(service, checkpoint)
        raise
    finally:
        service._action_transaction = None

    if isinstance(result, dict) and result.get('ok') is False:
        result = {**result, 'code': result.get('code', 'ACTION_REJECTED'),
                  'httpStatus': result.get('httpStatus', 400)}
        if request is not None:
            result = {**result, 'requestId': request['requestId'],
                      'gameSeq': getattr(service, '_game_seq', 0),
                      'version': service._version, 'duplicate': False}
        return result
    # Publishing is deliberately outside rollback: a failed host notification
    # cannot undo a durable move or turn an accepted request into a retry hazard.
    for summary, force, prompt in transaction['outbox']:
        try:
            service._notify_sakura(summary, force=force, prompt=prompt)
        except Exception as error:
            service._logger.warning('提交后通知失败', fields={
                'reason_code': 'POST_COMMIT_NOTIFY_FAILED', 'error_type': type(error).__name__})
    return result
