"""Deterministic proportional Sankey ribbons from explicitly sourced, acyclic flows."""

def sankey_geometry(block):
    levels = {node.id: 0 for node in block.nodes}
    for _ in block.nodes:
        for edge in block.edges:
            levels[edge.target] = max(levels[edge.target], levels[edge.source] + 1)
    max_level = max(1, *levels.values())
    layers = [[node for node in block.nodes if levels[node.id] == level] for level in range(max_level + 1)]
    height = max(320, max(len(layer) for layer in layers) * 90)
    volumes = {node.id: max(sum(float(e.value) for e in block.edges if e.source == node.id),
                            sum(float(e.value) for e in block.edges if e.target == node.id)) for node in block.nodes}
    scale = min((height - 80 - 24 * (len(layer) - 1)) / sum(volumes[node.id] for node in layer)
                for layer in layers if layer and sum(volumes[node.id] for node in layer))
    boxes = {}
    for level, layer in enumerate(layers):
        total = sum(volumes[node.id] * scale for node in layer) + 24 * (len(layer) - 1)
        y = (height - total) / 2
        for node in layer:
            size = volumes[node.id] * scale
            boxes[node.id] = dict(x=80 + level / max_level * 560, y=y, height=size)
            y += size + 24
    incoming, outgoing = dict.fromkeys(boxes, 0), dict.fromkeys(boxes, 0)
    ribbons = []
    for edge in block.edges:
        source, target = boxes[edge.source], boxes[edge.target]
        sx, tx = source['x'] + 14, target['x']
        sy, ty = source['y'] + outgoing[edge.source], target['y'] + incoming[edge.target]
        size = float(edge.value) * scale
        mid = (sx + tx) / 2
        path = (f'M {sx:.3f} {sy:.3f} C {mid:.3f} {sy:.3f}, {mid:.3f} {ty:.3f}, {tx:.3f} {ty:.3f} '
                f'L {tx:.3f} {ty+size:.3f} C {mid:.3f} {ty+size:.3f}, {mid:.3f} {sy+size:.3f}, {sx:.3f} {sy+size:.3f} Z')
        ribbons.append(dict(path=path, width=size, label_x=mid, label_y=(sy+ty+size)/2))
        outgoing[edge.source] += size
        incoming[edge.target] += size
    return dict(height=height, nodes=boxes, ribbons=ribbons)
