/* Deterministic canvas arrangement. Only positions are returned; project data stays untouched. */
(function (root) {
  'use strict';
  const COLUMN_GAP = 96, ROW_GAP = 40, MARGIN = 40;

  function arrange(cards, edges, measurements = {}) {
    const nodes = new Map();
    for (const card of Array.isArray(cards) ? cards : []) {
      if (!card || card.id == null || String(card.id) === '' || nodes.has(String(card.id))) continue;
      const id = String(card.id), media = card.type === 'image' || card.type === 'video';
      const measured = measurements?.[id];
      const dimension = (value, fallback) => Number.isFinite(Number(value)) && Number(value) > 0 ? Number(value) : fallback;
      nodes.set(id, {id, order: nodes.size, width: dimension(measured?.width, media ? 284 : 308),
        height: dimension(measured?.height, media ? 330 : 300), next: new Set(), previous: new Set()});
    }
    const positions = new Map();
    if (!nodes.size) return {};
    for (const edge of Array.isArray(edges) ? edges : []) {
      if (!edge) continue;
      const from = nodes.get(String(edge.from)), to = nodes.get(String(edge.to));
      if (!from || !to || from === to) continue;
      from.next.add(to.id); to.previous.add(from.id);
    }
    const connected = [...nodes.values()].filter(node => node.next.size || node.previous.size);
    const isolated = [...nodes.values()].filter(node => !node.next.size && !node.previous.size);
    const byOrder = (a, b) => nodes.get(a).order - nodes.get(b).order;
    for (const node of connected) {
      node.next = [...node.next].sort(byOrder);
      node.previous = [...node.previous].sort(byOrder);
    }

    // Iterative strongly connected components keep cycles together without recursive depth limits.
    const visited = new Set(), finish = [];
    for (const node of connected) {
      if (visited.has(node.id)) continue;
      visited.add(node.id);
      const stack = [{id: node.id, index: 0}];
      while (stack.length) {
        const current = stack[stack.length - 1], next = nodes.get(current.id).next;
        if (current.index < next.length) {
          const id = next[current.index++];
          if (!visited.has(id)) { visited.add(id); stack.push({id, index: 0}); }
        } else { finish.push(current.id); stack.pop(); }
      }
    }
    const components = [], owner = new Map();
    for (const id of finish.reverse()) {
      if (owner.has(id)) continue;
      const component = {members: [], next: new Set(), previous: new Set(), rank: 0, incoming: 0};
      const index = components.length, stack = [id];
      owner.set(id, index);
      while (stack.length) {
        const member = stack.pop(); component.members.push(member);
        for (const parent of nodes.get(member).previous) {
          if (!owner.has(parent)) { owner.set(parent, index); stack.push(parent); }
        }
      }
      component.members.sort(byOrder);
      component.order = nodes.get(component.members[0]).order;
      component.width = Math.max(...component.members.map(member => nodes.get(member).width));
      component.height = component.members.reduce((sum, member) => sum + nodes.get(member).height, 0) + ROW_GAP * (component.members.length - 1);
      components.push(component);
    }
    for (const node of connected) {
      for (const to of node.next) {
        const fromIndex = owner.get(node.id), toIndex = owner.get(to);
        if (fromIndex === toIndex) continue;
        components[fromIndex].next.add(toIndex); components[toIndex].previous.add(fromIndex);
      }
    }
    for (const component of components) component.incoming = component.previous.size;
    const pending = components.map((_, index) => index).filter(index => !components[index].incoming)
      .sort((a, b) => components[a].order - components[b].order);
    for (let cursor = 0; cursor < pending.length; cursor++) {
      const component = components[pending[cursor]];
      for (const index of component.next) {
        const target = components[index]; target.rank = Math.max(target.rank, component.rank + 1);
        if (--target.incoming === 0) pending.push(index);
      }
    }

    const columns = new Map();
    for (const component of components) {
      if (!columns.has(component.rank)) columns.set(component.rank, []);
      columns.get(component.rank).push(component);
    }
    let x = MARGIN, bottom = MARGIN;
    for (const rank of [...columns.keys()].sort((a, b) => a - b)) {
      const column = columns.get(rank);
      const desiredCenter = component => component.previous.size
        ? [...component.previous].reduce((sum, index) => sum + components[index].center, 0) / component.previous.size
        : MARGIN + component.height / 2;
      column.sort((a, b) => desiredCenter(a) - desiredCenter(b) || a.order - b.order);
      let y = MARGIN;
      for (const component of column) {
        const top = Math.max(y, desiredCenter(component) - component.height / 2);
        component.center = top + component.height / 2;
        y = top;
        for (const id of component.members) {
          const node = nodes.get(id);
          positions.set(id, {x: Math.round(x), y: Math.round(y)});
          y += node.height + ROW_GAP;
        }
        bottom = Math.max(bottom, y - ROW_GAP);
      }
      x += Math.max(...column.map(component => component.width)) + COLUMN_GAP;
    }

    // Disconnected cards form a compact shelf below the connected workflow.
    if (isolated.length) {
      const count = Math.min(3, Math.ceil(Math.sqrt(isolated.length)));
      const widths = Array.from({length: count}, (_, column) => Math.max(...isolated.filter((_, index) => index % count === column).map(node => node.width)));
      let y = connected.length ? bottom + ROW_GAP * 2 : MARGIN;
      for (let start = 0; start < isolated.length; start += count) {
        const row = isolated.slice(start, start + count); let x = MARGIN;
        row.forEach((node, column) => { positions.set(node.id, {x: Math.round(x), y: Math.round(y)}); x += widths[column] + COLUMN_GAP; });
        y += Math.max(...row.map(node => node.height)) + ROW_GAP;
      }
    }
    return Object.fromEntries([...nodes.keys()].map(id => [id, positions.get(id)]));
  }

  root.CanvasLayout = {arrange};
})(typeof window === 'undefined' ? globalThis : window);
