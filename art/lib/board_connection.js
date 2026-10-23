/* Same-origin transport and polling policy; no game, renderer or DOM dependencies. */
(function (root) {
  'use strict';
  class ConnectionError extends Error {}
  function createClient(options = {}) {
    const fetcher = options.fetch || root.fetch.bind(root);
    const setTimer = options.setTimeout || root.setTimeout.bind(root);
    const clearTimer = options.clearTimeout || root.clearTimeout.bind(root);
    const notify = options.onStatus || (() => {});
    let failures = 0, timer = null, disposed = false, instance;
    let revision = null, actionInFlight = false, needsRecovery = false, recoveryEpoch = 0, requestSeq = 0;
    const requestPrefix = root.crypto?.randomUUID?.() || Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
    const nonnegative = value => Number.isSafeInteger(value) && value >= 0;
    const status = () => ({failures, connected: failures === 0,
      retryMs: Math.min(15000, 1000 * 2 ** Math.min(failures, 4))});
    function failed(error) {
      failures += 1;
      notify({...status(), error: error.message});
    }
    function requireRecovery() { needsRecovery = true; recoveryEpoch += 1; }
    function updateRevision(data, reset = false) {
      if (!nonnegative(data.gameSeq) || !nonnegative(data.version)) return;
      if (reset || !revision || data.gameSeq > revision.gameSeq) revision = {gameSeq: data.gameSeq, version: data.version};
      else if (data.gameSeq === revision.gameSeq) revision.version = Math.max(revision.version, data.version);
    }
    async function request(url, init, timeout, allowRejection = false) {
      if (disposed) throw new ConnectionError('连接已关闭');
      const controller = new AbortController();
      const deadline = setTimer(() => controller.abort(), timeout);
      try {
        const response = await fetcher(url, {...init, signal: controller.signal, cache: 'no-store'});
        // Only explicit JSON business rejections have a known (non-uncertain) outcome.
        const rejection = allowRejection && (response.status === 400 || response.status === 409);
        if (!response.ok && !rejection) throw new ConnectionError('HTTP ' + response.status);
        if (!(response.headers.get('content-type') || '').toLowerCase().startsWith('application/json')) {
          throw new ConnectionError('服务器返回了非 JSON 内容');
        }
        const data = await response.json();
        if (!data || typeof data !== 'object' || Array.isArray(data)) throw new ConnectionError('服务器响应格式无效');
        if (rejection && (data.ok !== false || typeof data.code !== 'string')) throw new ConnectionError('操作拒绝响应格式无效');
        return {data, instance: response.headers.get('x-board-instance'), rejected: rejection};
      } catch (error) {
        throw error instanceof ConnectionError ? error : new ConnectionError(error.name === 'AbortError' ? '连接超时' : '无法连接棋盘服务');
      } finally { clearTimer(deadline); }
    }
    return {
      async state(busy) {
        const startedRecoveryEpoch = recoveryEpoch;
        try {
          const visible = root.document?.visibilityState === 'visible';
          const result = await request('/state?busy=' + (busy ? '1' : '0') + '&visible=' + (visible ? '1' : '0'), {}, options.stateTimeout ?? 6000);
          if (!nonnegative(result.data.version) || !Object.hasOwn(result.data, 'game') ||
              (Object.hasOwn(result.data, 'gameSeq') && !nonnegative(result.data.gameSeq)) ||
              (revision && !Object.hasOwn(result.data, 'gameSeq'))) {
            throw new ConnectionError('棋盘状态格式无效');
          }
          const nextInstance = result.instance || null;
          const changed = instance !== undefined && instance !== nextInstance;
          instance = nextInstance;
          updateRevision(result.data, changed);
          // A state request begun before an uncertain mutation cannot reconcile that mutation.
          if (startedRecoveryEpoch === recoveryEpoch && !actionInFlight) needsRecovery = false;
          failures = 0; notify(status());
          return {...result, changed};
        } catch (error) { failed(error); throw error; }
      },
      async action(action, args = {}) {
        if (actionInFlight) return {ok: false, code: 'ACTION_IN_FLIGHT', error: '上一操作仍在处理中，请勿重复点击。'};
        if (needsRecovery) return {ok: false, code: 'STATE_REQUIRED', error: '请先恢复棋盘状态，再确认是否需要继续操作。'};
        actionInFlight = true;
        let metadata;
        try {
          if (revision) metadata = {requestId: requestPrefix + '-' + (++requestSeq), gameSeq: revision.gameSeq, expectedVersion: revision.version};
          const result = await request('/api/action', {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({action, arguments: args, ...(metadata ? {metadata} : {})})}, options.actionTimeout ?? 20000, true);
          if (typeof result.data.ok !== 'boolean') throw new ConnectionError('操作响应格式无效');
          // Rejections such as INVALID_REQUEST_METADATA may lack a usable requestId;
          // their explicit HTTP/JSON refusal is still definite, not a lost mutation.
          if (metadata && !result.rejected && (result.data.requestId !== metadata.requestId || !nonnegative(result.data.gameSeq) ||
              !nonnegative(result.data.version) || typeof result.data.duplicate !== 'boolean')) {
            throw new ConnectionError('操作确认信息无效');
          }
          // The next state poll still owns instance-change notification/reload decisions.
          if (metadata && (result.instance || null) !== instance) {
            requireRecovery();
            return {ok: false, code: 'STATE_REQUIRED', error: '棋盘服务已切换，请先刷新棋盘状态。'};
          }
          updateRevision(result.data);
          if (result.rejected || ['STALE_GAME', 'STALE_VERSION', 'REQUEST_ID_REUSED', 'INVALID_REQUEST_METADATA'].includes(result.data.code)) requireRecovery();
          failures = 0; notify(status());
          return result.data;
        } catch (error) {
          requireRecovery();
          failed(error);
          // Never retry a mutation: a dropped response does not imply the move was rejected.
          return {ok: false, uncertain: true, error: error.message + '；操作是否生效尚未确认，请恢复连接后查看棋盘，勿重复点击。'};
        } finally { actionInFlight = false; }
      },
      schedule(poll) {
        this.cancel();
        if (!disposed) timer = setTimer(() => {timer = null; poll();}, status().retryMs);
      },
      cancel() { if (timer !== null) clearTimer(timer); timer = null; },
      dispose() { this.cancel(); disposed = true; },
      getStatus: status,
    };
  }
  root.BoardConnection = {createClient, ConnectionError};
})(globalThis);
