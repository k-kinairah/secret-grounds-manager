type PageTitleIconName =
  | "activity"
  | "dashboard"
  | "history"
  | "members"
  | "monitor-cog"
  | "promotions"
  | "rates"
  | "receipt"
  | "settings"
  | "shield"
  | "terminal"
  | "users";

interface PageTitleIconProps {
  name: PageTitleIconName;
}

const iconPaths: Record<PageTitleIconName, Array<string>> = {
  activity: ["M4 13h4l3-8 4 14 3-6h2"],
  dashboard: ["M4 4h7v7H4z", "M13 4h7v7h-7z", "M4 13h7v7H4z", "M13 13h7v7h-7z"],
  history: ["M4 12a8 8 0 1 0 2.3-5.7", "M4 5v5h5", "M12 8v5l3 2"],
  members: [
    "M16 11a3 3 0 1 0-2.8-4",
    "M8.5 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z",
    "M3 20c.7-3 2.6-5 5.5-5s4.8 2 5.5 5",
    "M14.5 15.3c2.8.2 4.6 1.9 5.5 4.7"
  ],
  "monitor-cog": [
    "M4 5h16v10H4z",
    "M8 19h8",
    "M12 15v4",
    "M17.5 10.5a2 2 0 1 0 0-4 2 2 0 0 0 0 4z",
    "M17.5 4.5v1",
    "M17.5 11.5v1",
    "M14.9 5.6l.7.7",
    "M19.4 10.7l.7.7",
    "M14.9 11.4l.7-.7",
    "M19.4 6.3l.7-.7"
  ],
  promotions: [
    "M6 18 18 6",
    "M7.5 10a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z",
    "M16.5 19a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z"
  ],
  rates: ["M4 7.5V4h3.5L19 15.5 15.5 19 4 7.5z", "M8 8h.01", "M14 10.5c-1-.7-2.5-.2-2.5 1s1.3 1.5 2.5 2 2.5.8 2.5 2-1.5 1.7-2.5 1"],
  receipt: ["M6 3h12v18l-2-1.2-2 1.2-2-1.2-2 1.2-2-1.2L6 21V3z", "M9 7h6", "M9 11h6", "M9 15h3"],
  settings: [
    "M4 6h16",
    "M9 8.1a2.1 2.1 0 1 0 0-4.2 2.1 2.1 0 0 0 0 4.2z",
    "M4 12h16",
    "M16 14.1a2.1 2.1 0 1 0 0-4.2 2.1 2.1 0 0 0 0 4.2z",
    "M4 18h16",
    "M12 20.1a2.1 2.1 0 1 0 0-4.2 2.1 2.1 0 0 0 0 4.2z"
  ],
  shield: ["M12 3 19 6v5c0 4.4-2.8 7.5-7 10-4.2-2.5-7-5.6-7-10V6l7-3z", "m9.5 12 1.8 1.8 3.7-4"],
  terminal: ["M4 5h16v14H4z", "m8 10 2 2-2 2", "M13 15h4"],
  users: ["M16 11a3 3 0 1 0-2.8-4", "M8.5 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z", "M3 20c.7-3 2.6-5 5.5-5s4.8 2 5.5 5", "M14.5 15.3c2.8.2 4.6 1.9 5.5 4.7"]
};

// Sparse outline-only glyphs (e.g. shield) cover far less of the 24x24 box
// than a denser icon like the rates tag, so at the same stroke color they
// read as faint/grayish even though the color value is identical. These
// render as a solid badge instead: the outer shape filled with the accent
// color, remaining paths cut through it in the dialog's dark background
// color so they read as a light notch rather than disappearing into the fill.
const SOLID_BADGE_ICONS = new Set<PageTitleIconName>(["shield"]);

export function PageTitleIcon({ name }: PageTitleIconProps) {
  const paths = iconPaths[name];
  if (SOLID_BADGE_ICONS.has(name)) {
    const [outline, ...rest] = paths;
    return (
      <span className="pageTitleIcon" aria-hidden="true">
        <svg className="pageTitleIcon__svg" fill="none" viewBox="0 0 24 24">
          <path d={outline} fill="#22d3ee" stroke="none" />
          {rest.map((path) => (
            <path d={path} key={path} stroke="#04121a" strokeWidth={2.2} />
          ))}
        </svg>
      </span>
    );
  }
  return (
    <span className="pageTitleIcon" aria-hidden="true">
      <svg className="pageTitleIcon__svg" fill="none" viewBox="0 0 24 24">
        {paths.map((path) => (
          <path d={path} key={path} />
        ))}
      </svg>
    </span>
  );
}
