import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import styles from "@/app/page.module.css";

export interface FancySelectOption<T extends string> {
  value: T;
  label: string;
  hint?: string;
  /** Optional leading status dot colour (any CSS colour). */
  dotColor?: string;
}

interface FancySelectProps<T extends string> {
  value: T;
  options: FancySelectOption<T>[];
  onChange: (next: T) => void;
  ariaLabel: string;
  disabled?: boolean;
}

/**
 * A small themeable listbox that replaces a native <select> so the open
 * menu can be fully styled (native option popups are drawn by the OS and
 * ignore CSS). Implements the WAI-ARIA listbox keyboard pattern:
 * arrows / Home / End move the active option, Enter or Space commits,
 * Escape closes, and a click outside dismisses.
 */
export function FancySelect<T extends string>({
  value,
  options,
  onChange,
  ariaLabel,
  disabled = false
}: FancySelectProps<T>) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(() =>
    Math.max(0, options.findIndex((option) => option.value === value))
  );
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const selected = options.find((option) => option.value === value) ?? options[0];

  useEffect(() => {
    if (!open) {
      return;
    }
    setActiveIndex(Math.max(0, options.findIndex((option) => option.value === value)));

    function onPointerDown(event: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open, value, options]);

  function commit(next: T) {
    onChange(next);
    setOpen(false);
    triggerRef.current?.focus();
  }

  function onTriggerKeyDown(event: ReactKeyboardEvent<HTMLButtonElement>) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp" || event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setOpen(true);
    }
  }

  function onMenuKeyDown(event: ReactKeyboardEvent<HTMLUListElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
      triggerRef.current?.focus();
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((index) => (index + 1) % options.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((index) => (index - 1 + options.length) % options.length);
    } else if (event.key === "Home") {
      event.preventDefault();
      setActiveIndex(0);
    } else if (event.key === "End") {
      event.preventDefault();
      setActiveIndex(options.length - 1);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      commit(options[activeIndex].value);
    }
  }

  return (
    <div className={styles.fancySelect} ref={rootRef}>
      <button
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-label={ariaLabel}
        className={styles.fancySelectTrigger}
        disabled={disabled}
        onClick={() => setOpen((prev) => !prev)}
        onKeyDown={onTriggerKeyDown}
        ref={triggerRef}
        type="button"
      >
        {selected?.dotColor ? (
          <span
            className={styles.fancySelectDot}
            style={{ background: selected.dotColor, color: selected.dotColor }}
            aria-hidden="true"
          />
        ) : null}
        <span className={styles.fancySelectValue}>{selected?.label ?? ""}</span>
        <svg aria-hidden="true" className={styles.fancySelectCaret} data-open={open} viewBox="0 0 12 8">
          <path d="M1 1l5 5 5-5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" />
        </svg>
      </button>
      {open ? (
        // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions -- listbox key handling per WAI-ARIA pattern
        <ul
          className={styles.fancySelectMenu}
          onKeyDown={onMenuKeyDown}
          ref={(node) => node?.focus()}
          role="listbox"
          tabIndex={-1}
        >
          {options.map((option, index) => (
            <li
              aria-selected={option.value === value}
              className={styles.fancySelectOption}
              data-active={index === activeIndex}
              key={option.value}
              onClick={() => commit(option.value)}
              onMouseEnter={() => setActiveIndex(index)}
              role="option"
            >
              {option.dotColor ? (
                <span
                  className={styles.fancySelectDot}
                  style={{ background: option.dotColor, color: option.dotColor }}
                  aria-hidden="true"
                />
              ) : null}
              <span className={styles.fancySelectOptionLabel}>{option.label}</span>
              {option.hint ? <span className={styles.fancySelectHint}>{option.hint}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
