"""One-shot, version-bound board decisions executed by the host's real Agent.

Host calls never run under the game lock. Tickets are ephemeral capabilities, not
save data; restarting/unloading the plugin revokes every outstanding ticket.
"""
from __future__ import annotations

import copy
import json
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping

try:
    from .agent_snapshot import encode_board
except ImportError:
    from agent_snapshot import encode_board

ACTIVE = frozenset({'queued', 'thinking'})
TTL_SECONDS = 120

# Model-facing constraints, not a claim that generated prose is infallible.
FACTUAL_NARRATION_HINT = (
    '像一起玩游戏的伙伴那样自然回应：可以开心、吐槽、期待，也可以简短解释自己的选择；不必逐项念状态或每轮重复余额。'
    '把角色感受与游戏事实分开：感受和玩笑可以自由发挥，规则、事件效果、得失及行动归属以本次commit的story/status/pending/cash为准，'
    '不把气氛描写说成额外规则（例如票价随车掌心情改变），也不把旧事件当成本次发生。'
    '行动前state用于决策，播报以行动后的commit结果为准；提交失败就自然说明没办成，不编造成功结果。'
    'dice是可用步数，不是已走距离；path是累计动画轨迹，可能包含不耗步的传送或退回，'
    '不得按len(path)推算实际步数，也不得自行计算实际步数。'
    'steps_left>0时只说已部分行走与剩余步数，不得说已走完dice步或整个回合；'
    'pending.player=sakura时仍由你行动，不得说轮到用户。'
    '路线选择成功不等于购买地产；pending仍为land时只说到达并等待下次决策，'
    '未有本次购买成功事实，不得声称产权归自己或已支付购地款。'
    '余额只照抄本次cash中对应玩家的原始整数，其他金额只照抄本次story/status明确给出的原始整数，'
    '统一使用¥单位，禁止自行计算余额或换算成万、亿及现实货币；'
    '中文、日文和其他语言必须使用完全相同的阿拉伯数字。缺少权威金额就不报金额。'
    '五子棋落点只按本次move的1基行列播报；没有引擎明确结论，不补充上下左右、斜角、封堵或关键手判断。'
    'winner=sakura表示你获胜，winner=user表示用户获胜；没有winner就不得宣告获胜。'
    '本次动作成功后停止调用走棋工具，不让用户代选sakura的后续子动作，等待插件下一次token。'
)


class AgentTurns:
    def __init__(self, service, *, clock=time.time, launch=None, wait=None):
        self.service = service
        self.clock = clock
        self.launch = launch or self._launch
        self.wait = wait or time.sleep
        self.ticket = None
        self.receipts = OrderedDict()
        self.closed = False
        self.epoch = secrets.token_urlsafe(18)

    @staticmethod
    def _launch(callback):
        threading.Thread(target=callback, name='boardgame-agent', daemon=True).start()

    def checkpoint(self):
        return copy.deepcopy((self.ticket, self.receipts))

    def restore(self, checkpoint):
        self.ticket, self.receipts = checkpoint

    def _error(self, message):
        # Import through the service's engine instance, preserving embedded imports.
        return self.service.agent_error(message)

    def _chat(self):
        context = self.service._context
        chat = context.get('sakura.host.chat') if context is not None else None
        if chat is None:
            raise self._error('真实Agent聊天服务不可用，请更新宿主；不会切换为算法。')
        return chat

    def identity(self):
        """Call only outside the game lock, including tool callbacks."""
        current = self._chat().current()
        if (not isinstance(current, Mapping) or not current.get('sessionId')
                or not current.get('characterId')):
            raise self._error('当前聊天会话不可用。')
        return dict(current)

    def legal(self):
        game = self.service._game
        if not game or game.winner or getattr(game, 'draw', False):
            raise self._error('当前没有可行动的对局。')
        pending = getattr(game, 'pending', None)
        actor = pending.get('player') if pending else game.turn
        if actor != 'sakura':
            raise self._error('现在需要玩家行动，不能让Agent代替玩家。')
        if game.kind == 'gomoku':
            return {'action': 'place', 'parameters': {'row': 'integer 1..15', 'col': 'integer 1..15'}}
        if pending:
            if pending.get('type') == 'tower':
                tower = game._tower
                action = 'tower_finish' if 'tower_finish' in pending['options'] else 'tower_step'
                return {'action': action, 'arguments': {'seq': tower['seq'], 'floor': tower['at']}}
            legal = {'action': 'decide', 'options': [str(x) for x in pending.get('options', [])],
                     'pendingType': pending.get('type')}
            if pending.get('type') == 'shop':
                items = self.service.agent_shop_items()
                legal['prices'] = {option: int(items[option[4:]]['price'])
                                   for option in legal['options'] if option.startswith('buy_')}
            elif pending.get('type') == 'land':
                cell = game.cells[pending['cell']]
                price = game.current_price(cell)
                legal['prices'] = {key: value for key, value in
                                   {'buy': price, 'upgrade': game.upgrade_cost('sakura', cell),
                                    'redeem': price * 3 // 5}.items() if key in legal['options']}
            return legal
        if getattr(game, '_walk', None):
            raise self._error('棋子仍在行走，暂时不能派发决策。')
        return {'action': 'roll', 'parameters': {'use_custom_die': 'optional boolean'}}

    @staticmethod
    def _set_status(ticket, status, **details):
        # A wait reason describes only the current queue observation.
        ticket.pop('waitReason', None)
        ticket.update(status=status, **details)

    def _refresh(self):
        ticket = self.ticket
        if not ticket or ticket['status'] not in ACTIVE:
            return
        if self.clock() * 1000 >= ticket['expiresAt']:
            error = ('等待宿主空闲超时，Agent决策尚未派发，请手动重试。'
                     if ticket['status'] == 'queued' else
                     'Agent决策派发后未在时限内提交动作，请手动重试。')
            self._set_status(ticket, 'expired', error=error)
        elif (self.closed or self.service.decision_driver() != 'agent'
              or ticket['gameSeq'] != self.service._game_seq
              or ticket['version'] != self.service._version):
            self._set_status(ticket, 'cancelled', error='棋盘或控制方式已改变，旧决策已失效。')

    def public(self):
        self._refresh()
        if self.ticket is None:
            return None
        return {key: copy.deepcopy(self.ticket[key]) for key in
                ('id', 'status', 'gameSeq', 'version', 'action', 'expiresAt', 'error', 'waitReason')
                if key in self.ticket}

    def request(self):
        """Called in the outer action transaction; merely stage, never submit."""
        self._refresh()
        if self.closed:
            raise self._error('插件已停止。')
        if self.ticket and self.ticket['status'] in ACTIVE:
            return {'ok': True, 'status': '正在等待真实Agent决定。', 'agentDecision': self.public()}
        legal = self.legal()
        previous = self.ticket
        receipt = self.receipts.get(previous['token']) if previous else None
        continuation = bool(previous and previous['status'] == 'committed' and receipt
                            and previous['gameSeq'] == self.service._game_seq
                            and receipt['result']['version'] == self.service._version)
        self.ticket = {'id': secrets.token_urlsafe(12), 'token': secrets.token_urlsafe(32),
                       'status': 'queued', 'gameSeq': self.service._game_seq,
                       'version': self.service._version, 'action': legal['action'], 'legal': legal,
                       'expiresAt': int((self.clock() + TTL_SECONDS) * 1000),
                       'epoch': self.epoch, 'scheduled': False, 'turn': self.service._game.turn,
                       'characterId': (previous['characterId'] if continuation else
                                       self.service._opponent.get('id') or None),
                       'sessionId': previous['sessionId'] if continuation else None,
                       }
        return {'ok': True, 'status': '已请求真实Agent决定下一步（尚未走棋）。',
                'agentDecision': self.public()}

    def cancel(self, identifier):
        self._refresh()
        if not self.ticket or identifier != self.ticket['id']:
            raise self._error('等待任务已改变，请刷新后再取消。')
        if self.ticket['status'] not in ACTIVE:
            raise self._error('该决策已结束；取消不会撤销已执行的棋步。')
        self._set_status(self.ticket, 'cancelled', error='已取消棋盘等待；宿主回复或语音可能继续。')
        return {'ok': True, 'status': self.ticket['error'], 'agentDecision': self.public()}

    def dispatch_pending(self):
        """Invoked only after the outer transaction succeeds AND releases its lock."""
        with self.service._action_lock:
            self._refresh()
            ticket = self.ticket
            if not ticket or ticket['status'] != 'queued' or ticket['scheduled']:
                return
            ticket['scheduled'] = True
            token = ticket['token']
        try:
            self.launch(lambda: self._dispatch(token))
        except Exception:
            self._fail(token, '无法启动Agent任务，请重试。')

    def _fail(self, token, message):
        with self.service._action_lock:
            if self.ticket and self.ticket['token'] == token and self.ticket['status'] in ACTIVE:
                self._set_status(self.ticket, 'failed', error=message)

    def _queued(self, token):
        """Under the lock: revalidate before/after every idle-poll RPC."""
        self._refresh()
        ticket = self.ticket
        if not ticket or ticket['token'] != token or ticket['status'] != 'queued':
            return None
        if (ticket['epoch'] != self.epoch or self.service._game.turn != ticket['turn']
                or self.legal() != ticket['legal']):
            self._set_status(ticket, 'cancelled', error='合法动作已改变，旧决策已撤销。')
            return None
        return ticket

    def _dispatch(self, token):
        try:
            while True:
                with self.service._action_lock:
                    if self._queued(token) is None:
                        return
                identity = self.identity()  # No game lock during any host RPC.
                with self.service._action_lock:
                    ticket = self._queued(token)
                    if ticket is None:
                        return
                    if ((ticket['characterId'] and ticket['characterId'] != identity['characterId'])
                            or (ticket['sessionId'] and ticket['sessionId'] != identity['sessionId'])):
                        self._set_status(ticket, 'cancelled', error='聊天会话或角色已改变，旧决策已撤销。')
                        return
                    # Bind on the first identity, even while waiting. Never move
                    # a queued intention to a different session later.
                    ticket.update(characterId=identity['characterId'], sessionId=identity['sessionId'])
                    ticket.pop('waitReason', None)
                    if identity.get('idle') is False:
                        ticket['waitReason'] = 'host_busy'
                    if identity.get('idle') is True:
                        # This marks dispatch, not evidence that submit was accepted.
                        self._set_status(ticket, 'thinking')
                        # Full board/history is available only through the tool.
                        request = {'token': ticket['token'], 'gameSeq': ticket['gameSeq'],
                                   'version': ticket['version'], 'player': 'sakura',
                                   'legalAction': copy.deepcopy(ticket['legal'])}
                        break
                # Every legal intention may wait in its bound session, including
                # the next turn after a user move. Waiting does not submit or
                # interrupt chat. One worker, original TTL, no host cancellation.
                self.wait(0.25)
            message = ('【棋盘插件主动决策请求，不是用户消息】你现在控制sakura棋子。只决定并提交一次动作；'
                       '必须调用boardgame_agent_state取得权威状态，再调用boardgame_agent_commit。'
                       '不能使用旧boardgame_roll/decide/place/auto工具代算或代替玩家。'
                       '不要仅口头声称走棋；引擎工具成功才算走棋。token只对本次动作有效。'
                       '本次成功后不要让用户代选后续路线等子动作；插件将在宿主空闲后再次唤醒你。'
                       '如工具拒绝或状态过期，请停止，不要重试旧选择。' +
                       FACTUAL_NARRATION_HINT + '\n' + json.dumps(request, ensure_ascii=False))
            # Explicit session identity: host rejects busy/switched sessions rather
            # than queueing an intent against a future session. This plugin does
            # not invoke host cancellation; it only revokes the board capability.
            accepted = self._chat().submit({'sessionId': identity['sessionId'],
                                            'message': message, 'resources': []})
            if not isinstance(accepted, Mapping) or accepted.get('accepted') is not True:
                reason = accepted.get('reasonCode', 'CHAT_REJECTED') if isinstance(accepted, Mapping) else 'CHAT_REJECTED'
                self._fail(token, f'宿主未受理Agent决策（{reason}），请手动重试。')
        except Exception:
            self._fail(token, '真实Agent调用失败，请检查宿主；不会切换为算法。')

    def _validate(self, token, identity):
        self._refresh()
        ticket = self.ticket
        if not isinstance(token, str) or not ticket or ticket['token'] != token:
            raise self._error('Agent token无效或已被替换。')
        if ticket['status'] != 'thinking' or ticket['epoch'] != self.epoch:
            raise self._error('Agent决策已取消、超时或消费。')
        if (ticket['sessionId'] != identity.get('sessionId') or
                ticket['characterId'] != identity.get('characterId')):
            self._set_status(ticket, 'cancelled', error='聊天会话或角色已改变。')
            raise self._error('聊天会话或角色已改变，拒绝迟到动作。')
        if self.service._game.turn != ticket['turn'] or self.legal() != ticket['legal']:
            raise self._error('合法动作已改变，请重新请求决策。')
        return ticket

    def _snapshot(self, ticket):
        game = self.service._game
        state = self.service.state({})
        return {'token': ticket['token'], 'gameSeq': ticket['gameSeq'], 'version': ticket['version'],
                'expiresAt': ticket['expiresAt'], 'player': 'sakura', 'turn': game.turn, 'game': game.kind,
                'legalAction': copy.deepcopy(ticket['legal']), 'state': copy.deepcopy(state),
                'board': encode_board(game.to_dict()), 'shopItems': self.service.agent_shop_items()}

    def state(self, arguments):
        identity = self.identity()
        with self.service._action_lock:
            return self._snapshot(self._validate(arguments.get('token'), identity))

    def commit(self, arguments):
        # Resolve host identity before acquiring the transaction lock. Revocation
        # on session mismatch is independent of a failed engine transaction.
        identity = self.identity()
        with self.service._action_lock:
            if self.closed:
                raise self._error('插件已停止。')
            token = arguments.get('token')
            if not isinstance(token, str) or token not in self.receipts:
                self._validate(token, identity)
        return self.service._commit_agent(arguments, identity=identity)

    def apply(self, arguments, identity):
        """In the ordinary service transaction; failures restore ticket + engine."""
        token, action = arguments.get('token'), arguments.get('action')
        params = arguments.get('arguments', {})
        if not isinstance(token, str) or not isinstance(action, str) or not isinstance(params, Mapping):
            raise self._error('Agent提交参数无效。')
        fingerprint = json.dumps([action, dict(params)], sort_keys=True, ensure_ascii=False, allow_nan=False)
        cached = self.receipts.get(token)
        if cached:
            if (cached['sessionId'] != identity.get('sessionId') or
                    cached['characterId'] != identity.get('characterId') or self.closed):
                raise self._error('聊天会话或角色已改变，旧token不可复用。')
            if cached['fingerprint'] != fingerprint:
                raise self._error('已消费token不能提交不同动作。')
            return {**copy.deepcopy(cached['result']), 'duplicate': True}
        ticket = self._validate(token, identity)
        legal = ticket['legal']
        if action != legal['action']:
            raise self._error('动作不在本次允许范围内。')
        if action == 'roll':
            if set(params) - {'use_custom_die'} or ('use_custom_die' in params and type(params['use_custom_die']) is not bool):
                raise self._error('掷骰只能指定use_custom_die布尔值。')
        elif action == 'decide':
            if set(params) != {'action'} or not isinstance(params['action'], str) or params['action'] not in legal['options']:
                raise self._error('决策必须是当前合法选项。')
        elif action == 'place':
            if set(params) != {'row', 'col'} or any(type(params[k]) is not int or not 1 <= params[k] <= 15 for k in ('row', 'col')):
                raise self._error('真实Agent落子必须明确给出1至15的row/col。')
        elif (dict(params) != legal['arguments'] or
              any(type(params.get(key)) is not int for key in ('seq', 'floor'))):
            raise self._error('塔内参数必须与权威快照一致。')
        self.service._agent_applying = True
        try:
            result = self.service._execute_agent_action(action, dict(params))
        finally:
            self.service._agent_applying = False
        self._set_status(ticket, 'committed')
        # Every Monopoly action supplies post-action balances, not only purchases.
        # Otherwise a roll narrator must infer balances from rendered board text,
        # contradicting the cash-only contract above. Copy inside the transaction
        # so retries return the original receipt rather than a later balance.
        if self.service._game.kind == 'monopoly':
            result = {**result, 'cash': copy.deepcopy(self.service._game.cash)}
        # Replace legacy next-tool instructions only in this Agent result.
        result = {**result, 'hint': FACTUAL_NARRATION_HINT,
                  'ok': True, 'requestId': token, 'duplicate': False,
                  'gameSeq': self.service._game_seq, 'version': self.service._version,
                  'agentDecision': self.public()}
        self.receipts[token] = {'fingerprint': fingerprint, 'result': copy.deepcopy(result),
                                'sessionId': ticket['sessionId'], 'characterId': ticket['characterId']}
        while len(self.receipts) > 128:
            self.receipts.popitem(last=False)
        return result

    def close(self):
        with self.service._action_lock:
            self.closed = True
            self.epoch = secrets.token_urlsafe(18)
            if self.ticket and self.ticket['status'] in ACTIVE:
                self._set_status(self.ticket, 'cancelled', error='插件已停止，旧决策已撤销。')
