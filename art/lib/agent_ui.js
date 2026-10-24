/* Agent transport lifecycle only. Game legality and presentation remain external. */
(function(root) {
  'use strict';
  const BUSY = new Set(['queued', 'thinking']);
  const STOP = new Set(['failed', 'expired', 'cancelled']);
  function create(gameSeq = null) {
    return {gameSeq, driver:'agent', decision:null, submitting:false, cancelling:false,
      paused:false, error:'', acknowledged:'', terminalKey:''};
  }
  function busy(ui) { return !!(ui && (ui.submitting || ui.cancelling || BUSY.has(ui.decision?.status))); }
  function terminalKey(d) { return d && STOP.has(d.status) ? d.id + ':' + d.status : ''; }
  function sync(ui, data) {
    const before = JSON.stringify(ui);
    if (data.gameSeq != null && ui.gameSeq !== data.gameSeq) Object.assign(ui, create(data.gameSeq));
    ui.driver = data.decisionDriver === 'algorithm' ? 'algorithm' : 'agent';
    const d = data.agentDecision;
    // Ignore another game's decision, never expose its cancellation control.
    ui.decision = d && (d.gameSeq == null || d.gameSeq === ui.gameSeq) ? {...d} : null;
    const key = terminalKey(ui.decision);
    if (key && key !== ui.acknowledged) {
      ui.paused = true;
      ui.error = ui.decision.error || ({failed:'Agent 决策失败', expired:'Agent 决策已超时', cancelled:'Agent 决策已取消'}[ui.decision.status]);
    }
    ui.terminalKey = key;
    return before !== JSON.stringify(ui);
  }
  function retry(ui) {
    ui.acknowledged = terminalKey(ui.decision);
    ui.paused = false; ui.error = '';
  }
  function reject(ui, error) { ui.paused = true; ui.error = error || 'Agent 请求未完成，请确认状态后手动重试'; }
  function cancelId(ui) { return ui && !ui.cancelling && BUSY.has(ui.decision?.status) ? ui.decision.id : null; }
  function fallbackId(ui) {
    return ui && ui.driver === 'agent' && !busy(ui) && ['expired', 'failed'].includes(ui.decision?.status)
      ? ui.decision.id : null;
  }
  root.AgentUI = Object.freeze({create, busy, sync, retry, reject, cancelId, fallbackId});
})(globalThis);
