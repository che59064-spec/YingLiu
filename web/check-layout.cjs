// Run with: node web/check-layout.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = {window: {}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(__dirname + '/canvas-layout.js', 'utf8'), context);
const arrange = context.window.CanvasLayout.arrange;
const cards = [
  {id: 'workflow', type: 'workflow', x: NaN, y: Infinity, workflow: {unknown: ['preserve']}, name: '很长的工作流标题'.repeat(30)},
  {id: 'front', type: 'image'}, {id: 'motion', type: 'video'}, {id: 'result', type: 'video'},
  {id: 'join', type: 'join'}, {id: 'joined', type: 'video'}, {id: 'loose-a', type: 'image'}, {id: 'loose-b', type: 'workflow'}
];
const edges = [{from: 'front', to: 'workflow', fieldId: 'identity'}, {from: 'motion', to: 'workflow'},
  {from: 'workflow', to: 'result', result: true}, {from: 'result', to: 'join'}, {from: 'join', to: 'joined'},
  {from: 'missing', to: 'workflow'}, {from: 'front', to: 'workflow'}];
const measurements = Object.fromEntries(cards.map((card, index) => [card.id, {width: 260 + index * 7, height: 190 + index * 29}]));
const before = JSON.stringify({cards, edges, measurements});
const positions = arrange(cards, edges, measurements);
assert.equal(JSON.stringify({cards, edges, measurements}), before, 'layout must preserve workflow, references, edges and measurements');
assert.equal(JSON.stringify(arrange(cards, [...edges].reverse(), measurements)), JSON.stringify(positions), 'edge enumeration must not change layout');
for (const [from, to] of [['front', 'workflow'], ['motion', 'workflow'], ['workflow', 'result'], ['result', 'join'], ['join', 'joined']]) {
  assert.ok(positions[from].x + measurements[from].width < positions[to].x, 'connected stages must progress left to right');
}
function assertNoOverlap(list, placed, sizes) {
  for (let i = 0; i < list.length; i++) {
    const a = list[i], pa = placed[a.id]; assert.ok(Number.isFinite(pa.x) && Number.isFinite(pa.y));
    for (const b of list.slice(i + 1)) {
      const pb = placed[b.id], sa = sizes[a.id], sb = sizes[b.id];
      assert.ok(pa.x + sa.width <= pb.x || pb.x + sb.width <= pa.x || pa.y + sa.height <= pb.y || pb.y + sb.height <= pa.y,
        a.id + ' overlaps ' + b.id);
    }
  }
}
assertNoOverlap(cards, positions, measurements);
const cycleCards = ['a', 'b', 'c', 'd'].map(id => ({id, type: 'workflow'}));
const cycleEdges = [{from: 'a', to: 'b'}, {from: 'b', to: 'a'}, {from: 'b', to: 'c'}, {from: 'c', to: 'd'}];
const cycle = arrange(cycleCards, cycleEdges);
assert.equal(cycle.a.x, cycle.b.x, 'a cycle belongs in one layer');
assert.ok(cycle.b.x < cycle.c.x && cycle.c.x < cycle.d.x, 'downstream nodes must follow a cyclic component');
assertNoOverlap(cycleCards, cycle, Object.fromEntries(cycleCards.map(card => [card.id, {width: 308, height: 300}])));
assert.equal(Object.keys(arrange([], [])).length, 0);
assert.equal(Object.keys(arrange([{id: 'x', type: 'image'}, {id: 'x', type: 'video'}, null], [{from: 'x', to: 'x'}])).length, 1);
assert.ok(Number.isFinite(arrange([{id: '__proto__', type: 'image'}], [], {'__proto__': {width: Infinity, height: -1}}).__proto__.x));
console.log('自动排版检查通过：连接层级、真实尺寸不重叠、独立节点、环路、稳定顺序及原始数据保护。');
