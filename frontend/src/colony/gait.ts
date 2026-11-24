// 足裏は支持期には地面に残す。遊脚期だけ少し持ち上げ、膝は常に進行方向へ曲げる。
export function legPose(phase: number, rear: boolean, walking: boolean, bob: number) {
  const turn = phase / (2 * Math.PI) + (rear ? 0.5 : 0);
  const u = turn - Math.floor(turn);
  const hip = { x: rear ? -2.7 : 1.2, y: -10.1 - bob };
  let offset = rear ? -1.7 : 1.1,
    lift = 0;
  if (walking) {
    if (u < 0.58) offset = 2.7 * (1 - (2 * u) / 0.58);
    else {
      const t = (u - 0.58) / 0.42;
      offset = 2.7 * (2 * t * t * (3 - 2 * t) - 1);
      lift = Math.sin(Math.PI * t) * 2.05;
    }
  }
  const ankle = { x: hip.x + offset, y: -1 - lift - (rear ? 0.45 : 0) };
  const dx = ankle.x - hip.x,
    dy = ankle.y - hip.y,
    length = Math.hypot(dx, dy);
  const bend = Math.sqrt(Math.max(0, 5.35 ** 2 - (length / 2) ** 2));
  const knee = {
    x: (hip.x + ankle.x) / 2 + (bend * dy) / length,
    y: (hip.y + ankle.y) / 2 - (bend * dx) / length,
  };
  return { hip, knee, ankle };
}
