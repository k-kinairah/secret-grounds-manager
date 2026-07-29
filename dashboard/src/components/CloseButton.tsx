import styles from "@/app/page.module.css";

interface CloseButtonProps {
  label: string;
  className?: string;
  disabled?: boolean;
  onClick: () => void;
  size?: "default" | "small";
}

export function CloseButton({
  label,
  className = "",
  disabled = false,
  onClick,
  size = "default"
}: CloseButtonProps) {
  return (
    <button
      aria-label={label}
      className={`${styles.iconButton} ${size === "small" ? styles.iconButtonSmall : ""} ${className}`}
      disabled={disabled}
      onClick={onClick}
      type="button"
    >
      <svg aria-hidden="true" className={styles.iconButtonSvg} fill="none" viewBox="0 0 24 24">
        <path d="M6.75 6.75l10.5 10.5M17.25 6.75l-10.5 10.5" stroke="currentColor" strokeLinecap="round" strokeWidth="2.25" />
      </svg>
    </button>
  );
}
