const lines = {
  plus: "M12 5v14M5 12h14",
  minus: "M5 12h14",
  close: "m6 6 12 12M18 6 6 18",
  down: "m6 9 6 6 6-6",
  list: "M9 6h11M9 12h11M9 18h11M4 6h.1M4 12h.1M4 18h.1",
  home: "m3 11 9-7 9 7M6 10v10h12V10m-8 10v-6h4v6",
  layers: "m3 8 9-5 9 5-9 5zM3 12l9 5 9-5M3 16l9 5 9-5",
  cursor: "m5 3 13 10-7 1-3 7z",
  arrow: "M5 12h14m-6-6 6 6-6 6",
  split: "M12 20v-5m0 1L5 8V4m7 12 7-8V4M2 7l3-3 3 3m8 0 3-3 3 3",
  check: "m4 13 5 5L20 6",
  flag: "M5 21V4m0 1q4-4 8 0t7 0v8q-3 4-7 0t-8 0",
  clock: "M12 3a9 9 0 1 0 .01 0M12 7v5l3 2",
  target: "M3 12h4m10 0h4M12 3v4m0 10v4M12 6a6 6 0 1 0 .01 0",
  hand: "m7 12-2-3q-2-2-3 0l4 8q2 4 6 4h2q5-1 5-6V8q-1-3-3 0V5q-1-3-3 0v3-5q-2-2-3 0v6-5q-2-2-3 0z",
  help: "M4 20v-6h4l3 2h5q3 0 3-2m-12 5 7 2 7-6q2-3-1-3l-4 3M12 12l-5-5c-3-4 2-8 5-4 3-4 8 0 5 4z",
  book: "M3 5h6l3 2 3-2h6v14h-6l-3 2-3-2H3zM12 7v14",
  zone: "m3 6 9-3 8 5-2 10-10 3-5-7z",
  line: "m3 18 6-9 6 4 6-9",
  user: "M12 3a4 4 0 1 0 .01 0M4 21v-2a8 8 0 0 1 16 0v2",
  pin: "M12 21s7-8 7-13a7 7 0 0 0-14 0c0 5 7 13 7 13zM12 6a2 2 0 1 0 .01 0",
  pause: "M8 5v14m8-14v14",
  work: "M3 8h18v12H3zM8 8V5h8v3M3 12q8 4 18 0",
} as const;
export type IconName = keyof typeof lines;
export function Icon({ name, size = 17 }: { name: IconName; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={lines[name]} />
    </svg>
  );
}
export function AntMark() {
  return (
    <svg viewBox="0 0 44 31" width="38" height="29" fill="currentColor" aria-hidden="true">
      <ellipse cx="10" cy="19" rx="9" ry="6.5" transform="rotate(-15 10 19)" />
      <circle cx="22" cy="17" r="5.3" />
      <circle cx="31" cy="12.4" r="7.6" />
      <path
        d="M28 7q-4-9-8-3m12 2q2-8 7-5M18 21l-4 6m11-8 1 7"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.3"
        strokeLinecap="round"
      />
      <circle cx="34" cy="10.7" r="1.3" fill="#fff4d6" />
    </svg>
  );
}
