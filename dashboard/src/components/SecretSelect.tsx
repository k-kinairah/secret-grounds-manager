"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { CSSProperties, KeyboardEvent, ReactNode } from "react";
import styles from "./SecretSelect.module.css";

export interface SecretSelectOption {
  disabled?: boolean;
  label: string;
  value: string;
}

interface SecretSelectProps {
  active?: boolean;
  clearLabel?: string;
  disabled?: boolean;
  error?: string;
  label: string;
  leadingIcon?: ReactNode;
  loading?: boolean;
  onChange: (value: string) => void;
  onClear?: () => void;
  options: SecretSelectOption[];
  placeholder?: string;
  searchable?: boolean;
  searchPlaceholder?: string;
  value: string;
}

export function SecretSelect({
  active = false,
  clearLabel = "Clear selection",
  disabled = false,
  error,
  label,
  leadingIcon,
  loading = false,
  onChange,
  onClear,
  options,
  placeholder = "Select an option",
  searchable = false,
  searchPlaceholder = "Search...",
  value
}: SecretSelectProps) {
  const selectId = useId();
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const [isOpen, setIsOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [searchQuery, setSearchQuery] = useState("");
  const [panelStyle, setPanelStyle] = useState<CSSProperties>({});

  const selectedOption = options.find((option) => option.value === value);
  const filteredOptions = useMemo(() => {
    if (!searchable || searchQuery.trim() === "") {
      return options;
    }
    const query = searchQuery.trim().toLowerCase();
    return options.filter((option) => option.label.toLowerCase().includes(query));
  }, [options, searchQuery, searchable]);
  const activeOption = filteredOptions[activeIndex];
  const canClear = Boolean(onClear && value);

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    const selectedIndex = filteredOptions.findIndex((option) => option.value === value && !option.disabled);
    const firstEnabledIndex = filteredOptions.findIndex((option) => !option.disabled);
    setActiveIndex(selectedIndex >= 0 ? selectedIndex : Math.max(0, firstEnabledIndex));
  }, [filteredOptions, isOpen, value]);

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    const positionPanel = () => {
      const rect = triggerRef.current?.getBoundingClientRect();
      if (!rect) {
        return;
      }
      const viewportPadding = 12;
      const width = Math.min(rect.width, window.innerWidth - viewportPadding * 2);
      const left = Math.min(Math.max(viewportPadding, rect.left), window.innerWidth - width - viewportPadding);
      setPanelStyle({
        left,
        top: rect.bottom + 8,
        width
      });
    };

    positionPanel();
    window.addEventListener("resize", positionPanel);
    window.addEventListener("scroll", positionPanel, true);
    return () => {
      window.removeEventListener("resize", positionPanel);
      window.removeEventListener("scroll", positionPanel, true);
    };
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (triggerRef.current?.contains(target) || listRef.current?.contains(target)) {
        return;
      }
      closeSelect();
    };

    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [isOpen]);

  useEffect(() => {
    if (isOpen && searchable) {
      window.setTimeout(() => searchRef.current?.focus(), 0);
    }
    if (!isOpen) {
      setSearchQuery("");
    }
  }, [isOpen, searchable]);

  function closeSelect() {
    setIsOpen(false);
    window.setTimeout(() => triggerRef.current?.focus(), 0);
  }

  function openSelect() {
    if (disabled || loading) {
      return;
    }
    setIsOpen(true);
  }

  function moveActive(direction: 1 | -1) {
    if (filteredOptions.length === 0) {
      return;
    }
    let nextIndex = activeIndex;
    for (let step = 0; step < filteredOptions.length; step += 1) {
      nextIndex = (nextIndex + direction + filteredOptions.length) % filteredOptions.length;
      if (!filteredOptions[nextIndex]?.disabled) {
        setActiveIndex(nextIndex);
        return;
      }
    }
  }

  function selectOption(option: SecretSelectOption | undefined) {
    if (!option || option.disabled) {
      return;
    }
    onChange(option.value);
    closeSelect();
  }

  function handleTriggerKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (isOpen) {
        moveActive(1);
      } else {
        openSelect();
      }
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      if (isOpen) {
        moveActive(-1);
      } else {
        openSelect();
      }
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (isOpen) {
        selectOption(activeOption);
      } else {
        openSelect();
      }
    } else if (event.key === "Escape" && isOpen) {
      event.preventDefault();
      closeSelect();
    } else if (event.key === "Home" && isOpen) {
      event.preventDefault();
      setActiveIndex(Math.max(0, filteredOptions.findIndex((option) => !option.disabled)));
    } else if (event.key === "End" && isOpen) {
      event.preventDefault();
      const reversedIndex = [...filteredOptions].reverse().findIndex((option) => !option.disabled);
      setActiveIndex(reversedIndex >= 0 ? filteredOptions.length - 1 - reversedIndex : 0);
    }
  }

  function handleListKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      moveActive(1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      moveActive(-1);
    } else if (event.key === "Home") {
      event.preventDefault();
      setActiveIndex(Math.max(0, filteredOptions.findIndex((option) => !option.disabled)));
    } else if (event.key === "End") {
      event.preventDefault();
      const reversedIndex = [...filteredOptions].reverse().findIndex((option) => !option.disabled);
      setActiveIndex(reversedIndex >= 0 ? filteredOptions.length - 1 - reversedIndex : 0);
    } else if (event.key === "Enter") {
      event.preventDefault();
      selectOption(activeOption);
    } else if (event.key === "Escape") {
      event.preventDefault();
      closeSelect();
    }
  }

  const panel = isOpen ? (
    <div
      className={styles.panel}
      id={`${selectId}-panel`}
      onKeyDown={handleListKeyDown}
      ref={listRef}
      style={panelStyle}
    >
      {searchable ? (
        <input
          aria-label={`Search ${label}`}
          className={styles.search}
          onChange={(event) => setSearchQuery(event.target.value)}
          placeholder={searchPlaceholder}
          ref={searchRef}
          value={searchQuery}
        />
      ) : null}
      <div
        aria-activedescendant={activeOption ? `${selectId}-option-${activeOption.value}` : undefined}
        aria-label={label}
        className={styles.optionList}
        role="listbox"
        tabIndex={searchable ? -1 : 0}
      >
        {filteredOptions.length === 0 ? (
          <div className={styles.empty}>No operators found</div>
        ) : (
          filteredOptions.map((option, index) => {
            const isSelected = option.value === value;
            const isActive = index === activeIndex;
            return (
              <button
                aria-disabled={option.disabled || undefined}
                aria-selected={isSelected}
                className={`${styles.option} ${isSelected ? styles.optionSelected : ""} ${isActive ? styles.optionActive : ""}`}
                disabled={option.disabled}
                id={`${selectId}-option-${option.value}`}
                key={option.value}
                onClick={() => selectOption(option)}
                onMouseEnter={() => setActiveIndex(index)}
                role="option"
                type="button"
              >
                <span>{option.label}</span>
                {isSelected ? <SelectGlyph name="check" /> : null}
              </button>
            );
          })
        )}
      </div>
    </div>
  ) : null;

  return (
    <div className={`${styles.root} ${active ? styles.rootActive : ""} ${error ? styles.rootError : ""}`}>
      <label className={styles.label} id={`${selectId}-label`}>
        {label}
      </label>
      <div className={styles.triggerRow}>
        <button
          aria-controls={isOpen ? `${selectId}-panel` : undefined}
          aria-expanded={isOpen}
          aria-haspopup="listbox"
          aria-labelledby={`${selectId}-label ${selectId}-value`}
          className={styles.trigger}
          disabled={disabled || loading}
          onClick={() => (isOpen ? closeSelect() : openSelect())}
          onKeyDown={handleTriggerKeyDown}
          ref={triggerRef}
          type="button"
        >
          {leadingIcon ? <span className={styles.leadingIcon}>{leadingIcon}</span> : null}
          <span className={selectedOption ? styles.value : styles.placeholder} id={`${selectId}-value`}>
            {loading ? "Loading..." : selectedOption?.label ?? placeholder}
          </span>
          <span className={styles.chevron}>
            <SelectGlyph name="chevron" />
          </span>
        </button>
        {canClear ? (
          <button aria-label={clearLabel} className={styles.clearButton} onClick={onClear} type="button">
            <SelectGlyph name="x" />
          </button>
        ) : null}
      </div>
      {error ? <p className={styles.error}>{error}</p> : null}
      {typeof document === "undefined" ? panel : createPortal(panel, document.body)}
    </div>
  );
}

export function SelectGlyph({ name }: { name: "badge" | "check" | "chevron" | "credit-card" | "list-filter" | "users" | "x" }) {
  const paths = {
    badge: ["M8 4h8l4 4v8l-4 4H8l-4-4V8z", "m8.5 12 2.2 2.2 4.8-5"],
    check: ["m5 12 4 4L19 6"],
    chevron: ["m6 9 6 6 6-6"],
    "credit-card": ["M3 6h18v12H3z", "M3 10h18", "M7 15h4"],
    "list-filter": ["M4 6h16", "M7 12h10", "M10 18h4"],
    users: ["M16 11a3 3 0 1 0-2.8-4", "M8.5 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z", "M3 20c.7-3 2.6-5 5.5-5s4.8 2 5.5 5", "M14.5 15.3c2.8.2 4.6 1.9 5.5 4.7"],
    x: ["M6 6l12 12", "M18 6 6 18"]
  };

  return (
    <svg aria-hidden="true" className={styles.icon} fill="none" viewBox="0 0 24 24">
      {paths[name].map((path) => (
        <path d={path} key={path} />
      ))}
    </svg>
  );
}
