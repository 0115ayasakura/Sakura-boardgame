/* Read-only presentation policy. Server game state and BlackTrainView's lifecycle
 * remain their owners' responsibility; this module never advances either one.
 * board.html supplies a fresh snapshot for every input (including old handlers).
 */
(function (root) {
  'use strict';
  const ADVANCING_ACTIONS = new Set(['roll', 'decide', 'act_sakura', 'agent_fallback', 'place', 'undo', 'start', 'black_train_start']);
  function movementHeld(state) {
    return !!(state.visualMovePending || state.moveAnim || state.pendingMove || state.arrivalHold != null);
  }
  // Modal animation ownership stays with its die node. Cancellation invalidates
  // even callbacks which a host already moved from its timer queue to dispatch.
  function cancelDie(die, timers = root) {
    if (die._spin != null) timers.clearInterval(die._spin);
    if (die._resultTimer != null) timers.clearTimeout(die._resultTimer);
    die._spin = die._resultTimer = null;
    die._modalGeneration = (die._modalGeneration || 0) + 1;
    return die._modalGeneration;
  }
  function snapshot(input = {}) {
    const moving = !!input.moving, modal = !!input.modal, queued = !!input.queued;
    const ordinaryModal = input.ordinaryModal == null ? modal : !!input.ordinaryModal;
    const tower = !!input.tower, towerBusy = !!input.towerBusy;
    const blackActive = !!input.blackActive, blackVisible = !!input.blackVisible, blackLocked = !!input.blackLocked;
    const transitioning = !!input.transitioning, mounting = !!input.mounting;
    const ordinaryPending = moving || ordinaryModal || queued || tower;
    const visualBusy = moving || modal || queued || transitioning || mounting;
    const ordinaryVisualBusy = moving || ordinaryModal || queued || transitioning || mounting;
    const ordinaryLocked = visualBusy || tower || towerBusy || blackActive || blackVisible || blackLocked;
    const phase = blackLocked ? 'black-resolution' : moving ? 'movement' : modal ? 'modal'
      : queued ? 'queued' : transitioning ? 'transition' : mounting ? 'mounting'
      : tower || towerBusy ? 'tower' : blackActive || blackVisible ? 'black-active' : 'idle';
    const reason = blackLocked ? '请先完成列车事件或过渡' : moving ? '等待棋子移动完成'
      : modal ? '请先关闭当前弹窗' : queued ? '请先确认待展示事件'
      : transitioning || mounting ? '等待场景切换完成' : tower || towerBusy ? '请先完成塔内行动'
      : blackActive || blackVisible ? '请先完成黑列车探险' : '';
    function allows(intent, action = '') {
      switch (intent) {
        case 'ordinary': case 'tab': return !ordinaryLocked;
        case 'auto':
          // Only board-owned role rolls may run in an unlocked expedition.
          // Pending choices remain BlackTrainView's job; even its modal blocks
          // this path, unlike that modal's own decision transport below.
          if (action === 'black-roll') return blackActive && !visualBusy && !tower && !towerBusy && !blackLocked;
          return !ordinaryLocked;
        // Waiting-entry itself is a black lock. Never feed that lock back into
        // this predicate or entry would wait for itself forever. mapReady is
        // owned by BlackTrainView, not this ordinary presentation predicate.
        case 'entry': return !ordinaryPending;
        case 'card': return !moving && !modal;
        case 'tower': return tower && !towerBusy && !visualBusy && !blackLocked;
        case 'api':
          if (blackLocked) return false;
          // Tower requests are already serialized by requestTower; towerBusy
          // is true during the request and must not reject its own transport.
          if (action === 'tower_step' || action === 'tower_finish') return !visualBusy && !blackActive;
          // Configuration/piece selection does not advance play. An open piece
          // picker must be able to save its choice; server validation still owns
          // legality. The global black return/settlement lock above wins.
          if (!ADVANCING_ACTIONS.has(action)) return true;
          // Active black decisions/rolls belong to the black FSM, not ordinary
          // tower rules. Its unread-result/return lock remains authoritative.
          if (blackActive && ['roll', 'decide', 'act_sakura', 'agent_fallback'].includes(action)) return !ordinaryVisualBusy;
          return !ordinaryLocked;
        default: return false; // New intents must explicitly choose a policy.
      }
    }
    return Object.freeze({phase, reason, moving, modal, ordinaryModal, queued, tower, towerBusy, blackActive, blackVisible,
      blackLocked, transitioning, mounting, ordinaryPending, ordinaryLocked, allows});
  }
  root.PresentationFlow = Object.freeze({movementHeld, cancelDie, snapshot});
})(typeof globalThis !== 'undefined' ? globalThis : this);
