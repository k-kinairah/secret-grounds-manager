type PageTitleIconName =
  | "activity"
  | "dashboard"
  | "history"
  | "members"
  | "monitor-cog"
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
  rates: ["M4 7.5V4h3.5L19 15.5 15.5 19 4 7.5z", "M8 8h.01", "M14 10.5c-1-.7-2.5-.2-2.5 1s1.3 1.5 2.5 2 2.5.8 2.5 2-1.5 1.7-2.5 1"],
  receipt: ["M6 3h12v18l-2-1.2-2 1.2-2-1.2-2 1.2-2-1.2L6 21V3z", "M9 7h6", "M9 11h6", "M9 15h3"],
  settings: [
    "M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z",
    "M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2 3.4-.2-.1a1.7 1.7 0 0 0-1.9.1 7.8 7.8 0 0 1-1.6.9 1.7 1.7 0 0 0-1.1 1.6v.1H9v-.1a1.7 1.7 0 0 0-1.1-1.6 7.8 7.8 0 0 1-1.6-.9 1.7 1.7 0 0 0-1.9-.1l-.2.1-2-3.4.1-.1A1.7 1.7 0 0 0 2.6 15a7.7 7.7 0 0 1 0-1.9 1.7 1.7 0 0 0-.3-1.9l-.1-.1 2-3.4.2.1a1.7 1.7 0 0 0 1.9-.1 7.8 7.8 0 0 1 1.6-.9A1.7 1.7 0 0 0 9 5.2V5h4v.2a1.7 1.7 0 0 0 1.1 1.6 7.8 7.8 0 0 1 1.6.9 1.7 1.7 0 0 0 1.9.1l.2-.1 2 3.4-.1.1a1.7 1.7 0 0 0-.3 1.9 7.7 7.7 0 0 1 0 1.9z"
  ],
  shield: ["M12 3 19 6v5c0 4.4-2.8 7.5-7 10-4.2-2.5-7-5.6-7-10V6l7-3z", "m9.5 12 1.8 1.8 3.7-4"],
  terminal: ["M4 5h16v14H4z", "m8 10 2 2-2 2", "M13 15h4"],
  users: ["M16 11a3 3 0 1 0-2.8-4", "M8.5 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z", "M3 20c.7-3 2.6-5 5.5-5s4.8 2 5.5 5", "M14.5 15.3c2.8.2 4.6 1.9 5.5 4.7"]
};

export function PageTitleIcon({ name }: PageTitleIconProps) {
  return (
    <span className="pageTitleIcon" aria-hidden="true">
      <svg className="pageTitleIcon__svg" fill="none" viewBox="0 0 24 24">
        {iconPaths[name].map((path) => (
          <path d={path} key={path} />
        ))}
      </svg>
    </span>
  );
}
