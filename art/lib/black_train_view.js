/* Cosmetic black-train view. Decisions and expedition state remain server-owned. */
(function (root) {
  'use strict';
  const CATEGORIES = Object.freeze({ gold: '金色车票', blue: '蓝色契约', white: '白色B.E.G.', black: '黑色异象' });
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const number = (value, fallback = 0) => Number.isFinite(Number(value)) ? Math.max(0, Number(value)) : fallback;
  function active(game) { return game?.kind === 'monopoly' && game.black_train?.active === true; }
  function pending(game) { const p = game?.pending; return p && (p.kind === 'black_train' || p.type === 'black_train') ? p : null; }
  function model(game, names = {}) {
    const train = game?.black_train || {}, event = pending(game)?.event || train.event || train.current_event || {};
    const used = train.actions_taken || train.used || train.actions_used || {};
    const players = train.players || train.state?.players || game?.state?.players || {};
    return { active: active(game), players,
      used: { user: Math.min(5, number(used.user ?? players.user?.actions_taken)), sakura: Math.min(5, number(used.sakura ?? players.sakura?.actions_taken)) },
      names: { user: names.user || '玩家', sakura: names.sakura || '夜乃樱' },
      event, pending: pending(game), train };
  }
  function summary(game, names) {
    const m = model(game, names);
    return `<details class="bt-details" open><summary>黑列车 · 个人票根 / 收益 <span class="bt-sub">剩余 ${escape(number(m.train.actions_left, 10))} 次行动 · 可折叠</span></summary><div class="bt-detail-body">` +
      ['user', 'sakura'].map(p => `<div class="bt-used"><b>${escape(m.names[p])}</b> · 票根 ${escape(number(m.players[p]?.tickets))} · 收益 ¥${escape(number(m.players[p]?.reward))} · 自定义骰 ${m.players[p]?.custom_die ? '1' : '0'} · 已用 ${m.used[p]} / 5${number(m.players[p]?.lock) ? ` · 契约锁定 ${number(m.players[p]?.lock)} 次` : ''}${number(m.players[p]?.escrow_tickets) ? ` · 封存票 ${number(m.players[p]?.escrow_tickets)}` : ''}${number(m.players[p]?.escrow_reward) ? ` · 封存到期 ¥${number(m.players[p]?.escrow_reward)}` : ''}${m.players[p]?.items?.includes('ward') ? ' · 护符 ×1' : ''}</div>`).join('') +
      `<div class="bt-types">${Object.entries(CATEGORIES).map(([key, label]) => `<span class="bt-type bt-${key}">${label}</span>`).join('')}</div><div class="bt-sub">收益为个人额外报酬，不含固定报酬与票根折现。返程所得＝100＋个人收益＋剩余票根×20。</div></div></details>`;
  }
  function choices(game) {
    const m = model(game), p = m.pending;
    return (p?.choices || m.event.choices || p?.options || []).map((c, i) => typeof c === 'string'
      ? { action: c, label: c, effect_text: '', index: i }
      : { ...c, action: c.action ?? c.id ?? c.value ?? c.label ?? String(i), label: c.label || c.text || c.title || c.action || `选项 ${i + 1}`, effect_text: c.effect_text || c.effect?.text || '', index: i });
  }
  function actions(game, names) {
    const m = model(game, names), p = game?.pending;
    if (!m.active) return '';
    if (p?.player === 'user' && p.type === 'dice') return '<span>自定义骰 · 选择点数：</span>' + (p.options || []).map((value, i) => `<button data-bt-dice="${i}">${escape(value)} 点</button>`).join('');
    if (p?.player === 'user' && p.type === 'route') return '<span class="hint">探索岔路 · 选择前进方向：</span>' + (p.options || []).map((id, i) => {
      const cell = game.cells?.find(c => String(c.id) === String(id)) || {id};
      return `<button class="gold" data-bt-route="${i}">→ 路线 ${i + 1} · ${cellLabel(game, cell)}</button>`;
    }).join('');
    if (m.pending) return '<button class="gold" data-bt-open>查看列车事件</button>';
    if (p?.player === 'sakura' || game.turn === 'sakura') return `<button class="pink" data-bt-partner>请${escape(m.names.sakura)}探索</button>`;
    if (!p && !game.walk && game.turn === 'user') return '<button data-bt-roll>🎲 掷骰探索</button>' + (m.players.user?.custom_die ? `<button data-bt-custom ${number(m.players.user?.lock) > 0 ? 'disabled title="契约期间不能使用自定义骰"' : ''}>掷自定义骰</button>` : '');
    return '';
  }
  function bind(bar, game, send) {
    const options = choices(game);
    const once = (button, action, payload) => { button.onclick = async () => { if (button.disabled || inputLocked(bar.ownerDocument)) return; const buttons = Array.from(bar.querySelectorAll('button')), disabled = buttons.map(b => b.disabled); for (const b of buttons) b.disabled = true; try { await send(action, payload); } finally { buttons.forEach((b, i) => { b.disabled = disabled[i]; }); } }; };
    for (const button of bar.querySelectorAll('[data-bt-choice]')) once(button, 'decide', { action: options[Number(button.dataset.btChoice)]?.action });
    for (const button of bar.querySelectorAll('[data-bt-route]')) once(button, 'decide', { action: String(game.pending.options[Number(button.dataset.btRoute)]) });
    for (const button of bar.querySelectorAll('[data-bt-dice]')) once(button, 'decide', { action: String(game.pending.options[Number(button.dataset.btDice)]) });
    const roll = bar.querySelector('[data-bt-roll]'); if (roll) once(roll, 'roll');
    const custom = bar.querySelector('[data-bt-custom]'); if (custom) once(custom, 'roll', { use_custom_die: true });
    const partner = bar.querySelector('[data-bt-partner]'); if (partner) once(partner, 'act_sakura');
    const start = bar.querySelector('[data-bt-start]'); if (start) once(start, 'black_train_start');
    const open = bar.querySelector('[data-bt-open]'); if (open) open.onclick = () => bar.ownerDocument?.querySelector('#black-train-event-modal')?.classList.add('show');
  }
  function cellLabel(game, cell) {
    const train = game?.black_train || {}, tile = train.tiles?.[cell.id] || train.cells?.[cell.id] || {};
    const color = tile.color || cell.black_train_color || cell.event_color || cell.color;
    return escape(CATEGORIES[color] || tile.category || cell.black_train_category || '列车事件');
  }
  function actualHTML(actual) {
    if (!actual || actual.legacy) return '';
    const signed = v => `${Number(v) >= 0 ? '+' : ''}${Number(v) || 0}`;
    return `<div class="bt-effect">实际结算：收益 ${escape(signed(actual.reward_delta))} · 票根 ${escape(signed(actual.tickets_delta))}` +
      `${actual.prevented ? ' · 护符已化解惩罚' : ''}` +
      `${actual.reward != null ? `<br>个人余额：收益 ¥${escape(number(actual.reward))} · 票根 ${escape(number(actual.tickets))}` : ''}</div>`;
  }
  function eventHTML(game, names, record) {
    const m = model(game, names), e = record || m.event, who = record?.player || m.pending?.player || game.turn;
    const result = !!record;
    const shownChoices = result ? (record.choices || []) : choices(game);
    const choiceList = `<div class="bt-choices">` + shownChoices.map((c, i) => {
      const unavailable = c.disabled || c.available === false || (!result && c.requires_tickets > number(m.players[who]?.tickets));
      return `<button type="button" data-bt-choice="${c.index ?? i}"${result || who === 'sakura' || unavailable ? ' disabled' : ''}><b>${escape(c.label || c.action || c.text)}</b><span>${escape(c.effect_text || '按事件说明结算')}${unavailable ? `（${escape(c.reason || '票根不足，当前不可选')}）` : ''}</span></button>`;
    }).join('') + '</div>';
    return `<div class="from">${escape(m.names[who] || who)} · ${escape(e.category || CATEGORIES[e.color] || '列车事件')}</div>` +
      `<h2 class="title">${escape(e.title || '黑列车事件')}</h2><div class="flavor">${escape(e.story || e.text || e.description || '')}</div>` +
      (result ? choiceList + `<div class="bt-selection">${who === 'sakura' ? '对手自动选择' : '已选择'}：<b>${escape(e.action || e.choice || '完成事件')}</b></div><div class="bt-effect">${escape(e.effect_text || e.result_text || '行动已结算')}</div>` + actualHTML(e.actual) + '<button type="button" data-bt-continue>已了解，继续</button>'
        : `<p class="bt-selection">${who === 'sakura' ? `${escape(m.names.sakura)}将自动选择；选择与结算结果会保留供你查看。` : '请选择行动；每项具体效果如下。'}</p>` + choiceList);
  }
  function settlementHTML(game, names) {
    const m = model(game, names), settlement = m.train.settlement;
    // These are the authoritative ledger fields, not a client-side payout calculation.
    const money = value => value == null ? '未提供' : `¥${escape(value)}`;
    return '<div class="e-card"><div class="from">黑列车 · 安全返程</div><h2 class="title">收益结算</h2><div class="flavor">已返回普通地图，经营状态已恢复。</div>' +
      ['user', 'sakura'].map(p => {
        const row = settlement?.[p];
        if (!row) return `<div class="bt-settlement-row"><b>${escape(m.names[p])}</b>：结算明细未提供</div>`;
        return `<div class="bt-settlement-row"><b>${escape(m.names[p])}</b><div>固定报酬 ${money(row.base)} · 个人收益 ${money(row.reward)}<br>剩余票根 ${escape(row.tickets ?? '未提供')} 张 · 票根折现 ${money(row.ticket_cash)}</div><strong>合计 ${money(row.total)}</strong>` +
          (row.custom_die ? '<div>带回自定义骰 ×1</div>' : '') +
          (row.expired_items?.length ? `<div>到期道具：${row.expired_items.map(escape).join('、')}</div>` : '') + '</div>';
      }).join('') + '<div class="hint">点击画面任意位置关闭</div></div>';
  }
  // Persistent server flags are read by both players; never derive milestones from rolls.
  // Per-document/game tracking avoids replaying the announcement on every poll.
  const schedules = new WeakMap();
  function scheduleNotice(document, game, gameKey) {
    if (game?.kind !== 'monopoly') { schedules.delete(document); return null; }
    const schedule = game.black_train_schedule;
    let seen = schedules.get(document);
    if (!seen || seen.gameKey !== gameKey) {
      seen = { gameKey, announced: false, entered: !!schedule?.entered };
      schedules.set(document, seen);
    }
    if (!schedule) return null;
    if (schedule.entered) {
      seen.announced = true;
      if (seen.entered) return null;
      seen.entered = true;
      return ['黑列车到站', `双方普通行动已完成 ${number(schedule.enter_at, 26)} 次，现已自动进入黑列车探险。双方各行动 5 次后安全返程；普通经营状态将保留。`, false];
    }
    if (schedule.announced && !seen.announced) {
      seen.announced = true;
      return ['黑列车预告', `双方普通行动已完成 ${number(schedule.announce_at, 22)} 次。完成第 ${number(schedule.enter_at, 26)} 次普通行动后，双方将自动进入黑列车；无需手动入场。`, false];
    }
    return null;
  }
  const lifecycles = new WeakMap();
  function presentationActive(document) { return !!lifecycles.get(document)?.presentation; }
  function presentationGame(document, game) {
    const l = lifecycles.get(document);
    // The server has already restored ordinary positions/turn at settlement.
    // Do not let those coordinates move pieces/the follow camera behind the final result.
    if (!(l?.presentation && l.phase === 'active' && !active(game) && l.displayGame)) return game;
    // One atomic opponent turn (or a delayed poll) can include the final landing
    // and settlement together. The last active poll is therefore not authoritative
    // for final train positions; recover each player's last real landing from history.
    const positions = { ...l.displayGame.positions };
    let turn = l.displayGame.turn;
    let changed = false;
    for (const item of game.black_train?.history || []) {
      const position = item.actual?.position ?? item.cell_id;
      if ((item.player === 'user' || item.player === 'sakura') && position != null &&
          game.cells?.some(c => String(c.id) === String(position))) {
        positions[item.player] = position; turn = item.player; changed = true;
      }
    }
    return { ...game, positions: changed ? positions : l.displayGame.positions, turn };
  }
  function inputLocked(document) {
    const l = lifecycles.get(document);
    return !!l && (!!l.queue.length || l.phase === 'waiting-entry' || l.phase === 'returning' || l.phase === 'settlement' ||
      (l.presentation && (!!l.latest?.board3d?.transitioning || l.latest?.mapReady === false)));
  }
  function sync(options) {
    const { game, names, document, board, board3d, send, gameKey, mapReady = true } = options;
    let l = lifecycles.get(document);
    if (!l) { l = { phase: 'idle', presentation: false, serverActive: false, seen: new Set(), queue: [], eventKey: '', autoTimer: null, watchTimer: null, historyLength: 0, gameKey }; lifecycles.set(document, l); }
    const modal = document.querySelector('#black-train-event-modal'), eventCard = document.querySelector('#black-train-event-card');
    const result = document.querySelector('#black-train-result');
    const cancel = key => { if (l[key] !== null) root.clearTimeout(l[key]); l[key] = null; };
    const dismissSettlement = () => { if (result) { result.hidden = true; result.classList.remove('show'); result.innerHTML = ''; result.onclick = null; } };
    const reset = () => {
      cancel('autoTimer'); cancel('watchTimer'); l.phase = 'idle'; l.presentation = false; l.serverActive = false;
      l.seen.clear(); l.queue = []; l.eventKey = ''; l.historyLength = 0; l.terminalAcknowledged = false; l.terminalSeen = false; l.displayGame = null;
      l.generation = (l.generation || 0) + 1;
      modal?.classList.remove('show'); if (eventCard) eventCard.innerHTML = ''; dismissSettlement();
    };
    if (l.gameKey !== gameKey || !game || game.kind !== 'monopoly' || !game.black_train) { reset(); l.gameKey = gameKey; }
    l.latest = options;
    const on = active(game), train = game?.black_train, history = train?.history || [];
    if (on && (!l.serverActive || history.length < l.historyLength)) {
      reset(); l.phase = 'waiting-entry';
    }
    // Server activation is ahead of presentation: the final ordinary roll/landing
    // and every queued ordinary card must finish before changing tiles or camera.
    const entryDeferred = l.phase === 'waiting-entry' &&
      (typeof options.deferEntry === 'function' ? options.deferEntry() : options.deferEntry);
    if (on && l.phase === 'waiting-entry' && !entryDeferred && mapReady) {
      l.presentation = true; l.phase = 'active';
    }
    const participating = l.phase !== 'idle' && l.phase !== 'done';
    if (on) l.displayGame = game;
    let newRecords = 0;
    if (l.phase === 'active') history.forEach((item, index) => {
      const key = index + ':' + JSON.stringify(item);
      if (!l.seen.has(key)) { l.seen.add(key); l.queue.push(item); newRecords++; }
    });
    // A page opened on an already-settled save is deliberately not a participant.
    // Missing terminal history must still require explicit acknowledgement, not silently depart.
    if (participating && !on && train?.outcome && !l.terminalSeen && l.phase === 'active') {
      l.terminalSeen = true;
      if (!newRecords) l.queue.push({ title: '黑列车探索结束', story: '最后一次行动已由服务端结算。确认后开始返程。', effect_text: '完整事件记录未提供，请以对局记录为准。' });
    }
    if (participating && !on && !train?.outcome) reset();
    l.serverActive = on; l.historyLength = history.length;
    const refresh = () => { sync(l.latest); l.latest.onChange?.(); };
    const watch = () => { if (l.watchTimer === null) l.watchTimer = root.setTimeout(() => { l.watchTimer = null; refresh(); }, 50); };
    if (!on && train?.outcome && l.phase === 'active' && !l.queue.length && l.terminalAcknowledged) l.phase = 'returning';
    // Preserve the train presentation until every result has been explicitly confirmed.
    const visible = l.presentation && l.phase !== 'returning';
    if (visible) board3d?.setBlackTrainTiles?.(train?.tiles || {}, game?.cells || []);
    board3d?.setBlackTrainVisible?.(visible);
    if (l.phase === 'returning') {
      const stats = board3d?.getBlackTrainStats?.();
      // board3d finish clears transitioning only after restoring its cinematic camera.
      if (!mapReady || board3d?.transitioning || stats?.transitioning || stats?.active === true) watch();
      else { l.presentation = false; l.phase = 'settlement'; }
    } else if (l.phase === 'waiting-entry' || (l.presentation && board3d?.transitioning)) watch();
    document.body.classList.toggle('black-train-active', l.presentation);
    document.body.classList.toggle('black-train-input-locked', inputLocked(document));
    const card = document.querySelector('#black-train-card');
    if (card) {
      const details = card.querySelector?.('.bt-details');
      if (details && !card.hidden) l.detailsOpen = details.open;
      card.hidden = !l.presentation; card.innerHTML = l.presentation ? summary(game, names) : '';
      if (l.presentation) card.querySelector('.bt-details').open = l.detailsOpen ?? true;
    }
    board?.querySelector('.black-train-marker')?.remove();
    const deferred = typeof options.deferEvents === 'function' ? options.deferEvents() : options.deferEvents;
    const record = l.queue[0], p = on && l.phase === 'active' && pending(game);
    const key = !deferred && !board3d?.transitioning && mapReady ? (record ? 'result:' + JSON.stringify(record) : p ? 'pending:' + JSON.stringify([p, model(game).event]) : '') : '';
    if (modal && eventCard && key !== l.eventKey) {
      const generation = l.generation;
      l.eventKey = key; cancel('autoTimer'); modal.classList.toggle('show', !!key);
      eventCard.innerHTML = key ? eventHTML(game, names, record) : '';
      if (key && record) eventCard.querySelector('[data-bt-continue]').onclick = event => {
        event?.stopPropagation?.();
        // Stale/double click handlers cannot acknowledge a newer queued result.
        if (l.generation !== generation || l.queue[0] !== record) return;
        l.queue.shift(); l.eventKey = null;
        if (!active(l.latest.game) && !l.queue.length) l.terminalAcknowledged = true;
        refresh();
      };
      else if (key && send) {
        bind(eventCard, game, send);
        if (p.player === 'sakura') l.autoTimer = root.setTimeout(async () => {
          l.autoTimer = null;
          if (l.generation !== generation || l.eventKey !== key || inputLocked(document) || !active(l.latest.game)) return;
          try { await send('act_sakura'); }
          catch (error) {
            if (l.generation !== generation || l.eventKey !== key) return;
            eventCard.innerHTML += `<p role="alert">自动选择失败：${escape(error?.message || error)}</p><button type="button" data-bt-retry>重试自动选择</button>`;
            eventCard.querySelector('[data-bt-retry]').onclick = () => { l.eventKey = ''; refresh(); };
          }
        }, 1400);
      }
    }
    if (l.phase === 'settlement' && result) {
      if (result.hidden) {
        const generation = l.generation;
        result.innerHTML = settlementHTML(game, names); result.hidden = false; result.classList.add('show');
        result.onclick = event => { event?.stopPropagation?.(); if (l.generation !== generation || l.phase !== 'settlement') return; l.phase = 'done'; dismissSettlement(); refresh(); };
      }
    } else dismissSettlement();
  }
  root.BlackTrainView = Object.freeze({ CATEGORIES, active, pending, model, summary, choices, actions, bind, cellLabel, eventHTML, settlementHTML, presentationActive, presentationGame, inputLocked, scheduleNotice, sync });
})(globalThis);
