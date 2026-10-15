// Inline SVG icons (stroke style, 24px grid). Decorative by default: give the button around them an aria-label.

const PATHS: Record<string, string[]> = {
  brand: ["M9 3h6", "M10 3v6L4.5 18.5A1.6 1.6 0 0 0 5.9 21h12.2a1.6 1.6 0 0 0 1.4-2.5L14 9V3", "M7 15h10"],
  back: ["m15 18-6-6 6-6"],
  down: ["m7 10 5 5 5-5"],
  right: ["m9 6 6 6-6 6"],
  up: ["m7 14 5-5 5 5"],
  plus: ["M12 5v14", "M5 12h14"],
  check: ["m5 12 5 5L20 7"],
  x: ["M6 6l12 12", "M18 6 6 18"],
  search: ["M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14z", "m20 20-3.5-3.5"],
  lock: ["M7 11h10a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-6a2 2 0 0 1 2-2z", "M8 11V7a4 4 0 0 1 8 0v4"],
  pause: ["M9 5v14", "M15 5v14"],
  play: ["M7 5l12 7-12 7z"],
  stop: ["M7 7h10v10H7z"],
  download: ["M12 4v11", "m7 10 5 5 5-5", "M5 20h14"],
  upload: ["M12 16V4", "m7 9 5-5 5 5", "M5 20h14"],
  replay: ["M4 12a8 8 0 1 0 2.3-5.7", "M4 4v4h4"],
  external: ["M14 4h6v6", "M20 4 11 13", "M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"],
  alert: ["M12 3 2 20h20L12 3z", "M12 10v4", "M12 17h.01"],
  trash: ["M4 7h16", "M10 11v6", "M14 11v6", "M6 7l1 13h10l1-13", "M9 7V4h6v3"],
  note: ["M5 4h14v12l-4 4H5z", "M15 20v-4h4", "M9 9h6", "M9 13h3"],
  shield: ["M12 3 5 6v6c0 4.5 3 7.5 7 9 4-1.5 7-4.5 7-9V6l-7-3z"],
};

export default function Icon({ name, size = 16, className }: { name: keyof typeof PATHS | string; size?: number; className?: string }) {
  return (
    <svg
      className={`icon ${className ?? ""}`}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {(PATHS[name] ?? []).map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  );
}
