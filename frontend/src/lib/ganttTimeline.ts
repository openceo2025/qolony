export type TimelineCell = {
  key: string;
  width: number;
};

function startOfDay(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate(), 0, 0, 0, 0);
}

function startOfWeek(date: Date): Date {
  const day = date.getDay();
  const diffToMonday = day === 0 ? -6 : 1 - day;
  const monday = startOfDay(date);
  monday.setDate(monday.getDate() + diffToMonday);
  return monday;
}

function addDays(date: Date, days: number): Date {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
}

function addMonths(date: Date, months: number): Date {
  const next = new Date(date);
  next.setMonth(next.getMonth() + months);
  return next;
}

function addHours(date: Date, hours: number): Date {
  const next = new Date(date);
  next.setHours(next.getHours() + hours);
  return next;
}

function timelineRangeForDate(viewMode: string, date: Date): { keyPrefix: string; start: Date; end: Date } {
  if (viewMode === "Month") {
    const start = new Date(date.getFullYear(), date.getMonth(), 1, 0, 0, 0, 0);
    return { keyPrefix: `${date.getFullYear()}-${date.getMonth()}`, start, end: addMonths(start, 1) };
  }
  if (viewMode === "Week") {
    const start = startOfWeek(date);
    return { keyPrefix: `${start.getFullYear()}-${start.getMonth()}-${start.getDate()}`, start, end: addDays(start, 7) };
  }
  if (viewMode === "Quarter Day" || viewMode === "Half Day") {
    const hourStep = viewMode === "Quarter Day" ? 6 : 12;
    const startHour = Math.floor(date.getHours() / hourStep) * hourStep;
    const start = new Date(date.getFullYear(), date.getMonth(), date.getDate(), startHour, 0, 0, 0);
    return {
      keyPrefix: `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}-${startHour}`,
      start,
      end: addHours(start, hourStep),
    };
  }
  const start = startOfDay(date);
  return { keyPrefix: `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`, start, end: addDays(start, 1) };
}

export function findTimelineScrollLeftForDate(
  cells: TimelineCell[],
  viewMode: string,
  targetDate: Date,
  viewportWidth: number
): number | null {
  const range = timelineRangeForDate(viewMode, targetDate);
  let offset = 0;
  for (const cell of cells) {
    const isTargetCell = cell.key === range.keyPrefix;
    if (isTargetCell) {
      const spanMs = Math.max(1, range.end.getTime() - range.start.getTime());
      const elapsedMs = Math.max(0, Math.min(spanMs, targetDate.getTime() - range.start.getTime()));
      return Math.max(0, offset + cell.width * (elapsedMs / spanMs) - viewportWidth / 2);
    }
    offset += cell.width;
  }
  return null;
}
