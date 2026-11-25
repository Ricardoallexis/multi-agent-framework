// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Automatic placement of the office floor plan (GUI4-T07). Pure: a scene (maf.office-scene/1) in, plain geometry out.
// The world is 900 units wide; its height is 600 or more, so a long office never squeezes its positions.
// Areas go in rows above and below the coordinator; the relations are curves between the borders of two boxes.

export const WORLD_WIDTH = 900;
export const WORLD_MIN_HEIGHT = 600;
export const COORDINATOR_SIZE = { w: 164, h: 70 };
export const LAYER_OF_KIND = { assign: "assignment", hands_off: "handoff", reviews: "review", consults: "question" };

const MARGIN = 18;
const GAP_X = 20;
const GAP_Y = 24;
const BAND = 130;               // the coordinator's band between the upper and the lower rows
const ZONE_HEAD = 84;
const ZONE_FOOT = 14;
const ZONE_MIN_HEIGHT = 130;
const ZONE_MAX_WIDTH = 300;
const MEMBER_H = 44;
const MEMBER_GAP = 8;
const MAX_PER_ROW = 3;
const CHAR_WIDTH = 6.6;         // an estimate of a 12 px label, to cut a long name before it leaves its box

const asArray = (value) => (Array.isArray(value) ? value : []);
const round = (value) => Math.round(value * 10) / 10;

// How many areas go in each row: two rows up to six areas, rows of three after that.
export function rowSizes(count) {
  if (count <= 0) return [];
  if (count === 1) return [1];
  if (count <= 6) return [Math.ceil(count / 2), Math.floor(count / 2)];
  const rows = [];
  for (let left = count; left > 0; left -= MAX_PER_ROW) rows.push(Math.min(MAX_PER_ROW, left));
  return rows;
}

export const zoneHeight = (memberCount) =>
  Math.max(ZONE_MIN_HEIGHT, ZONE_HEAD + Math.max(1, memberCount) * (MEMBER_H + MEMBER_GAP) - MEMBER_GAP + ZONE_FOOT);

// The most characters a label of this width shows (the full text stays in the label for screen readers and the tooltip).
export const charsFor = (width) => Math.max(4, Math.floor(width / CHAR_WIDTH));

function centerOf(box) { return { x: box.x + box.w / 2, y: box.y + box.h / 2 }; }

// Where the line from the centre of a box toward a point leaves the box.
export function edgePoint(box, toward) {
  const c = centerOf(box);
  const dx = toward.x - c.x;
  const dy = toward.y - c.y;
  if (dx === 0 && dy === 0) return c;
  const scale = Math.min(dx === 0 ? Infinity : (box.w / 2) / Math.abs(dx), dy === 0 ? Infinity : (box.h / 2) / Math.abs(dy));
  return { x: c.x + dx * scale, y: c.y + dy * scale };
}

// One curve per relation. Several relations between the same two boxes fan out so none hides another.
function placeRelations(relations, boxes) {
  const groups = new Map();
  for (const relation of relations) {
    const pair = [relation.from, relation.to].sort().join("|");
    if (!groups.has(pair)) groups.set(pair, []);
    groups.get(pair).push(relation);
  }
  const placed = [];
  for (const group of groups.values()) {
    group.forEach((relation, index) => {
      const a = boxes[relation.from];
      const b = boxes[relation.to];
      const start = edgePoint(a, centerOf(b));
      const end = edgePoint(b, centerOf(a));
      const [low, high] = [relation.from, relation.to].sort();
      const lowCenter = centerOf(boxes[low]);
      const highCenter = centerOf(boxes[high]);
      const length = Math.hypot(highCenter.x - lowCenter.x, highCenter.y - lowCenter.y) || 1;
      const normal = { x: -(highCenter.y - lowCenter.y) / length, y: (highCenter.x - lowCenter.x) / length };
      const offset = (index - (group.length - 1) / 2) * 44;
      const mid = { x: (start.x + end.x) / 2, y: (start.y + end.y) / 2 };
      const control = { x: mid.x + normal.x * offset, y: mid.y + normal.y * offset };
      const straight = offset === 0;
      placed.push({
        ...relation,
        d: straight ? `M${round(start.x)} ${round(start.y)}L${round(end.x)} ${round(end.y)}`
          : `M${round(start.x)} ${round(start.y)}Q${round(control.x)} ${round(control.y)} ${round(end.x)} ${round(end.y)}`,
        mid: { x: round(0.25 * start.x + 0.5 * control.x + 0.25 * end.x), y: round(0.25 * start.y + 0.5 * control.y + 0.25 * end.y) },
      });
    });
  }
  return placed;
}

// The relations the plan can draw: known kind, both ends are areas of the scene, not the same area.
export function drawableRelations(scene) {
  const ids = new Set(asArray(scene?.areas).map((area) => String(area.id)));
  const relations = [];
  asArray(scene?.relations).forEach((relation, index) => {
    const layer = LAYER_OF_KIND[relation?.kind];
    const from = String(relation?.from);
    const to = String(relation?.to);
    if (!layer || layer === "assignment" || relation.warning || !ids.has(from) || !ids.has(to) || from === to) return;
    relations.push({ key: `relation:${index}`, kind: relation.kind, layer, from, to });
  });
  return relations;
}

export function layoutScene(scene) {
  const areas = asArray(scene?.areas);
  const hasCoordinator = Boolean(scene?.orchestrator?.enabled);
  const rows = rowSizes(areas.length);
  const topRows = hasCoordinator ? Math.ceil(rows.length / 2) : rows.length;

  // First pass in a local space that starts at the top margin; the second centres it in the world.
  let cursor = MARGIN;
  let trail = 0;                                    // the gap after the last row, which is not part of the plan
  let index = 0;
  const bandBox = (top) => ({ x: (WORLD_WIDTH - COORDINATOR_SIZE.w) / 2, y: top + (BAND - COORDINATOR_SIZE.h) / 2, w: COORDINATOR_SIZE.w, h: COORDINATOR_SIZE.h });
  const zones = [];
  let coordinator = null;
  rows.forEach((size, rowNumber) => {
    if (hasCoordinator && rowNumber === topRows) {
      coordinator = bandBox(cursor);
      cursor += BAND;
      trail = 0;
    }
    const rowAreas = areas.slice(index, index + size);
    index += size;
    const height = Math.max(...rowAreas.map((area) => zoneHeight(asArray(area.members).length)));
    const width = Math.min(ZONE_MAX_WIDTH, Math.floor((WORLD_WIDTH - 2 * MARGIN - GAP_X * (size - 1)) / size));
    const startX = (WORLD_WIDTH - (size * width + (size - 1) * GAP_X)) / 2;
    rowAreas.forEach((area, column) => {
      const zone = { id: String(area.id), area, x: round(startX + column * (width + GAP_X)), y: cursor, w: width, h: height, nodes: [] };
      asArray(area.members).forEach((member, position) => {
        zone.nodes.push({
          key: `member:${zone.id}:${member.id ?? position}`, member,
          x: zone.x + 14, y: zone.y + ZONE_HEAD + position * (MEMBER_H + MEMBER_GAP), w: width - 28, h: MEMBER_H,
        });
      });
      zones.push(zone);
    });
    cursor += height + GAP_Y;
    trail = GAP_Y;
  });
  if (hasCoordinator && !coordinator) {             // no lower rows: the coordinator sits below the upper ones
    coordinator = bandBox(cursor);
    cursor += BAND;
    trail = 0;
  }
  const used = cursor - trail + MARGIN;
  const height = Math.max(WORLD_MIN_HEIGHT, Math.ceil(used));
  const shift = (height - used) / 2;
  for (const zone of zones) {
    zone.y = round(zone.y + shift);
    for (const node of zone.nodes) node.y = round(node.y + shift);
  }
  if (coordinator) coordinator.y = round(coordinator.y + shift);
  if (!zones.length && coordinator) coordinator.y = round((height - coordinator.h) / 2);

  const boxes = Object.fromEntries(zones.map((zone) => [zone.id, zone]));
  const relations = drawableRelations(scene);
  if (coordinator) {
    boxes.__coordinator = coordinator;
    for (const zone of zones) relations.push({ key: `assign:${zone.id}`, kind: "assign", layer: "assignment", from: "__coordinator", to: zone.id });
  }
  return {
    bounds: { width: WORLD_WIDTH, height },
    coordinator, zones, relations: placeRelations(relations, boxes),
  };
}
