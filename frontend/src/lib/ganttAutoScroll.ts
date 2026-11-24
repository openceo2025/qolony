const DEFAULT_EDGE_SIZE = 64;
const DEFAULT_MAX_SPEED = 26;

export function calculateGanttAutoScrollDelta(
  pointerX: number,
  viewportLeft: number,
  viewportRight: number,
  edgeSize = DEFAULT_EDGE_SIZE,
  maxSpeed = DEFAULT_MAX_SPEED
): number {
  if (
    !Number.isFinite(pointerX) ||
    !Number.isFinite(viewportLeft) ||
    !Number.isFinite(viewportRight) ||
    viewportRight <= viewportLeft ||
    edgeSize <= 0 ||
    maxSpeed <= 0
  ) {
    return 0;
  }

  const effectiveEdgeSize = Math.min(edgeSize, (viewportRight - viewportLeft) / 2);
  const leftEdge = viewportLeft + effectiveEdgeSize;
  const rightEdge = viewportRight - effectiveEdgeSize;

  if (pointerX < leftEdge) {
    const proximity = Math.min(1, (leftEdge - pointerX) / effectiveEdgeSize);
    return -Math.max(1, Math.ceil(maxSpeed * proximity));
  }
  if (pointerX > rightEdge) {
    const proximity = Math.min(1, (pointerX - rightEdge) / effectiveEdgeSize);
    return Math.max(1, Math.ceil(maxSpeed * proximity));
  }
  return 0;
}
