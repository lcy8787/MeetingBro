import type { ReportBlock } from "../types";

export function sankeyGeometry(block: ReportBlock) {
  const nodes = block.nodes ?? [], edges = block.edges ?? [];
  const levels = new Map(nodes.map(n => [n.id, 0]));
  for (let round = 0; round < nodes.length; round++) for (const edge of edges) levels.set(edge.target, Math.max(levels.get(edge.target) ?? 0, (levels.get(edge.source) ?? 0) + 1));
  const maxLevel = Math.max(1, ...levels.values());
  const layers = Array.from({length:maxLevel + 1}, (_, level) => nodes.filter(n => levels.get(n.id) === level));
  const height = Math.max(320, Math.max(...layers.map(layer => layer.length)) * 90);
  const volumes = new Map(nodes.map(n => [n.id, Math.max(edges.filter(e => e.source === n.id).reduce((sum,e) => sum + Number(e.value), 0), edges.filter(e => e.target === n.id).reduce((sum,e) => sum + Number(e.value), 0))]));
  const scale = Math.min(...layers.filter(layer => layer.length && layer.some(n => volumes.get(n.id))).map(layer => (height - 80 - 24 * (layer.length - 1)) / layer.reduce((sum,n) => sum + (volumes.get(n.id) ?? 0), 0)));
  const boxes = new Map<string, {x:number; y:number; height:number}>();
  layers.forEach((layer, level) => {
    const total = layer.reduce((sum,n) => sum + (volumes.get(n.id) ?? 0) * scale, 0) + 24 * (layer.length - 1);
    let y = (height - total) / 2;
    for (const node of layer) {
      const size = (volumes.get(node.id) ?? 0) * scale;
      boxes.set(node.id, {x:80 + level / maxLevel * 560, y, height:size});
      y += size + 24;
    }
  });
  const incoming = new Map(nodes.map(n => [n.id, 0])), outgoing = new Map(incoming);
  const ribbons = edges.map(edge => {
    const source = boxes.get(edge.source)!, target = boxes.get(edge.target)!;
    const sx = source.x + 14, tx = target.x;
    const sy = source.y + (outgoing.get(edge.source) ?? 0), ty = target.y + (incoming.get(edge.target) ?? 0);
    const size = Number(edge.value) * scale, mid = (sx + tx) / 2;
    outgoing.set(edge.source, (outgoing.get(edge.source) ?? 0) + size);
    incoming.set(edge.target, (incoming.get(edge.target) ?? 0) + size);
    return {path:`M ${sx} ${sy} C ${mid} ${sy}, ${mid} ${ty}, ${tx} ${ty} L ${tx} ${ty+size} C ${mid} ${ty+size}, ${mid} ${sy+size}, ${sx} ${sy+size} Z`, width:size, labelX:mid, labelY:(sy+ty+size)/2};
  });
  return {height, nodes:boxes, ribbons};
}
