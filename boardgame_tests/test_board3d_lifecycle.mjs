/* Executes the real renderer with real Three maths and a deterministic DOM/GPU boundary.
 * Run: node boardgame_tests/test_board3d_lifecycle.mjs
 */
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import * as Three from '../sakura_boardgame/art/lib/three_module.js';
const mergeGeometries = () => { throw new Error('empty-scene fixture unexpectedly requested geometry merging'); };

const filename = new URL('../sakura_boardgame/art/lib/board3d.js', import.meta.url);
const source = (await readFile(filename, 'utf8'))
  .replace(/^import .*;$/gm, '')
  .replace('export const LIGHT_PRESETS', 'const LIGHT_PRESETS')
  .replace('export async function createBoard3D', 'async function createBoard3D')
  .replace('export default {createBoard3D,LIGHT_PRESETS};', 'return createBoard3D;');

function fixture({failTextures = false} = {}) {
  let now = 100;
  let nextId = 0;
  const raf = new Map(), timers = new Map(), warnings = [];
  const canvas = {style: {}, isConnected: true, addEventListener() {}, removeEventListener() {},
    remove() { this.isConnected = false; }, setPointerCapture() {}};
  const container = {clientWidth: 800, clientHeight: 600, appendChild() {}};
  class Renderer {
    constructor() {this.domElement = canvas; this.shadowMap = {}; this.capabilities = {getMaxAnisotropy: () => 1}; this.info = {render: {calls: 0, triangles: 0}};}
    setPixelRatio() {} setSize() {} render() {} dispose() {}
  }
  class TextureLoader {
    load(url, success, progress, fail) {
      queueMicrotask(() => failTextures ? fail(new Error(`missing ${url}`)) : success(new Three.Texture({width: 32, height: 32})));
    }
  }
  class Loader {async loadAsync() {return {scene: new Three.Group()};}}
  const document = {hidden: false, addEventListener() {}, removeEventListener() {}};
  const create = new Function('T', 'GLTFLoader', 'mergeGeometries', 'devicePixelRatio', 'document', 'window', 'ResizeObserver', 'performance', 'requestAnimationFrame', 'cancelAnimationFrame', 'setTimeout', 'clearTimeout', 'console', source)(
    {...Three, WebGLRenderer: Renderer, TextureLoader}, Loader, mergeGeometries, 1, document, {dispatchEvent() {}},
    class {observe() {} disconnect() {}}, {now: () => now},
    fn => {const id = ++nextId; raf.set(id, fn); return id;}, id => raf.delete(id),
    fn => {const id = ++nextId; timers.set(id, fn); return id;}, id => timers.delete(id),
    {warn: (...args) => warnings.push(args), error: (...args) => warnings.push(args)});
  return {warnings, create: () => create(container, {ambient: false, boardView: {orthoScale: 20, target: [8.5, 10, 2], right: [1, 0, 0], up: [0, .8, .6]}}),
    frame() {now += 40; const callbacks = [...raf.values()]; raf.clear(); for (const fn of callbacks) fn();},
    pending: () => raf.size + timers.size};
}

async function test_overlay_returns_at_unchanged_position() {
  const f = fixture(), board = await f.create();
  let point = {u: 8, v: 10, h: 2};
  const el = {isConnected: true, style: {}};
  board.addOverlay(el, () => point);
  const original = el.style.transform;
  assert.equal(el.style.display, '');
  point = null; board.setPieces([]); f.frame();
  assert.equal(el.style.display, 'none');
  point = {u: 8, v: 10, h: 2}; board.setPieces([]); f.frame();
  assert.equal(el.style.transform, original);
  assert.equal(el.style.display, '', 'same-position overlay must become visible again');
  board.dispose(); assert.equal(f.pending(), 0);
}

async function test_missing_badge_texture_is_handled() {
  const unhandled = [];
  const onUnhandled = reason => unhandled.push(reason);
  process.on('unhandledRejection', onUnhandled);
  const f = fixture({failTextures: true}), board = await f.create();
  try {
    board.setBadges([{id: 1, owner: 'user', x: 8, y: 10}], null);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(unhandled.length, 0, 'missing ownership icon must not reject without a handler');
    assert.equal(f.warnings.length, 1, 'a missing texture must leave a diagnostic');
    f.frame(); board.setBadges([], null); f.frame();
  } finally {board.dispose(); process.removeListener('unhandledRejection', onUnhandled);}
}

let failures = 0;
for (const test of [test_overlay_returns_at_unchanged_position, test_missing_badge_texture_is_handled]) {
  try {await test(); console.log(`PASS ${test.name}`);}
  catch (error) {failures++; console.error(`FAIL ${test.name}`, error);}
}
if (failures) process.exitCode = 1;
