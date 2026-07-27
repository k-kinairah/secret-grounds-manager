"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { fetchAdminSettings, resetAdminSettings, updateAdminSettings } from "@/lib/api";
import type { AdminSettings, AdminSettingsResponse } from "@/lib/types";
import styles from "@/app/settings/settings.module.css";

type CategoryKey = keyof AdminSettings;

const categories: { key: CategoryKey; label: string; description: string }[] = [
  { key: "general", label: "General", description: "Shop identity and display defaults." },
  { key: "sessions", label: "Sessions", description: "Expiration, reminders, and manual end behavior." },
  { key: "customer_client", label: "Customer Client", description: "Customer-facing overlay and reminder presentation." },
  { key: "wake_power", label: "Wake & Power", description: "Safe power behavior and read-only power safety status." },
  { key: "members", label: "Members", description: "Supported member account defaults." },
  { key: "reports", label: "Reports", description: "Default sales and export display preferences." }
];

function cloneSettings(settings: AdminSettings): AdminSettings {
  return JSON.parse(JSON.stringify(settings)) as AdminSettings;
}

function formatMinutes(seconds: number): string {
  if (!Number.isFinite(seconds)) {
    return "";
  }
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  return remainingSeconds ? `${minutes}m ${remainingSeconds}s` : `${minutes} min`;
}

function parseThresholds(value: string): number[] {
  return value
    .split(",")
    .map((item) => Number(item.trim()))
    .filter((item) => Number.isFinite(item));
}

export function SettingsManager() {
  const [response, setResponse] = useState<AdminSettingsResponse | null>(null);
  const [draft, setDraft] = useState<AdminSettings | null>(null);
  const [activeCategory, setActiveCategory] = useState<CategoryKey>("general");
  const [thresholdText, setThresholdText] = useState("");
  const [message, setMessage] = useState<{ kind: "success" | "error" | "info"; text: string } | null>({
    kind: "info",
    text: "Loading settings..."
  });
  const [isSaving, setIsSaving] = useState(false);
  const [isResetting, setIsResetting] = useState(false);

  const hasUnsavedChanges = useMemo(() => {
    if (response === null || draft === null) {
      return false;
    }
    return JSON.stringify(response.settings) !== JSON.stringify(draft);
  }, [draft, response]);

  useEffect(() => {
    let isMounted = true;
    fetchAdminSettings()
      .then((nextResponse) => {
        if (!isMounted) {
          return;
        }
        setResponse(nextResponse);
        setDraft(cloneSettings(nextResponse.settings));
        setThresholdText(nextResponse.settings.sessions.reminder_thresholds_seconds.join(", "));
        setMessage(null);
      })
      .catch((error) => {
        if (isMounted) {
          setMessage({
            kind: "error",
            text: error instanceof Error ? error.message : "Could not load settings."
          });
        }
      });
    return () => {
      isMounted = false;
    };
  }, []);

  function updateDraft(updater: (settings: AdminSettings) => AdminSettings) {
    setDraft((currentDraft) => (currentDraft === null ? currentDraft : updater(cloneSettings(currentDraft))));
  }

  function resetUnsavedChanges() {
    if (response === null) {
      return;
    }
    setDraft(cloneSettings(response.settings));
    setThresholdText(response.settings.sessions.reminder_thresholds_seconds.join(", "));
    setMessage({ kind: "info", text: "Unsaved changes reset." });
  }

  async function saveSettings() {
    if (draft === null || isSaving) {
      return;
    }
    setIsSaving(true);
    setMessage(null);
    try {
      const normalizedDraft = cloneSettings(draft);
      normalizedDraft.sessions.reminder_thresholds_seconds = parseThresholds(thresholdText);
      normalizedDraft.wake_power.shutdown_on_expiration_enabled =
        normalizedDraft.sessions.automatic_shutdown_on_expiration;
      const nextResponse = await updateAdminSettings(normalizedDraft);
      setResponse(nextResponse);
      setDraft(cloneSettings(nextResponse.settings));
      setThresholdText(nextResponse.settings.sessions.reminder_thresholds_seconds.join(", "));
      setMessage({ kind: "success", text: "Settings saved. Future sessions will use the updated runtime settings." });
    } catch (error) {
      setMessage({ kind: "error", text: error instanceof Error ? error.message : "Could not save settings." });
    } finally {
      setIsSaving(false);
    }
  }

  async function resetCategory(category: CategoryKey | "all") {
    if (isResetting) {
      return;
    }
    if (category === "all" && !window.confirm("Reset all eligible settings to defaults?")) {
      return;
    }
    setIsResetting(true);
    setMessage(null);
    try {
      const nextResponse = await resetAdminSettings(category);
      setResponse(nextResponse);
      setDraft(cloneSettings(nextResponse.settings));
      setThresholdText(nextResponse.settings.sessions.reminder_thresholds_seconds.join(", "));
      setMessage({
        kind: "success",
        text: category === "all" ? "All eligible settings reset." : `${category.replace("_", " ")} settings reset.`
      });
    } catch (error) {
      setMessage({ kind: "error", text: error instanceof Error ? error.message : "Could not reset settings." });
    } finally {
      setIsResetting(false);
    }
  }

  if (draft === null) {
    return (
      <section className={styles.loadingPanel} aria-live="polite">
        {message?.text ?? "Loading settings..."}
      </section>
    );
  }

  return (
    <section className={styles.layout}>
      <aside className={styles.categoryNav} aria-label="Settings categories">
        {categories.map((category) => (
          <button
            className={activeCategory === category.key ? styles.categoryActive : ""}
            key={category.key}
            onClick={() => setActiveCategory(category.key)}
            type="button"
          >
            <strong>{category.label}</strong>
            <span>{category.description}</span>
          </button>
        ))}
        <button className={styles.resetAllButton} disabled={isResetting} onClick={() => void resetCategory("all")} type="button">
          Reset All Defaults
        </button>
      </aside>

      <div className={styles.content}>
        {message ? (
          <div className={`${styles.message} ${styles[`${message.kind}Message`]}`} role={message.kind === "error" ? "alert" : "status"}>
            {message.text}
          </div>
        ) : null}

        <div className={styles.toolbar}>
          <div>
            <strong>{categories.find((category) => category.key === activeCategory)?.label}</strong>
            <span>{hasUnsavedChanges ? "Unsaved changes" : "Saved settings"}</span>
          </div>
          <div className={styles.toolbarActions}>
            <button className={styles.secondaryButton} disabled={!hasUnsavedChanges || isSaving} onClick={resetUnsavedChanges} type="button">
              Reset Unsaved Changes
            </button>
            <button className={styles.secondaryButton} disabled={isResetting} onClick={() => void resetCategory(activeCategory)} type="button">
              Reset Category
            </button>
            <button className={styles.primaryButton} disabled={isSaving} onClick={() => void saveSettings()} type="button">
              {isSaving ? "Saving..." : "Save Changes"}
            </button>
          </div>
        </div>

        {activeCategory === "general" ? (
          <SettingsPanel title="General">
            <TextField
              label="Shop display name"
              onChange={(value) => updateDraft((settings) => ({ ...settings, general: { ...settings.general, shop_display_name: value } }))}
              value={draft.general.shop_display_name}
            />
            <TextField
              label="Optional subtitle"
              onChange={(value) => updateDraft((settings) => ({ ...settings, general: { ...settings.general, shop_subtitle: value || null } }))}
              value={draft.general.shop_subtitle ?? ""}
            />
            <TextField label="PC naming prefix" readOnly value={draft.general.pc_naming_prefix} />
            <TextField
              label="Timezone display"
              onChange={(value) => updateDraft((settings) => ({ ...settings, general: { ...settings.general, timezone_display: value } }))}
              value={draft.general.timezone_display}
            />
          </SettingsPanel>
        ) : null}

        {activeCategory === "sessions" ? (
          <SettingsPanel title="Sessions">
            <NumberField
              helper={`${formatMinutes(draft.sessions.expiration_grace_seconds)}. Applies only to future grace periods.`}
              label="Expiration grace period"
              max={600}
              min={30}
              onChange={(value) => updateDraft((settings) => ({ ...settings, sessions: { ...settings.sessions, expiration_grace_seconds: value } }))}
              value={draft.sessions.expiration_grace_seconds}
            />
            <SwitchField
              checked={draft.sessions.automatic_shutdown_on_expiration}
              label="Automatic shutdown on capped-session expiration"
              onChange={(checked) =>
                updateDraft((settings) => ({
                  ...settings,
                  sessions: { ...settings.sessions, automatic_shutdown_on_expiration: checked },
                  wake_power: { ...settings.wake_power, shutdown_on_expiration_enabled: checked }
                }))
              }
            />
            <TextField
              helper="Comma-separated seconds, descending. Example: 900, 300, 60"
              label="Reminder thresholds"
              onChange={setThresholdText}
              value={thresholdText}
            />
            <SelectField
              label="Manual session-end default behavior"
              onChange={(value) => updateDraft((settings) => ({ ...settings, sessions: { ...settings.sessions, manual_end_default_behavior: value as AdminSettings["sessions"]["manual_end_default_behavior"] } }))}
              options={[
                ["lock_only", "Lock only"],
                ["lock_and_settle", "Lock and settle"]
              ]}
              value={draft.sessions.manual_end_default_behavior}
            />
          </SettingsPanel>
        ) : null}

        {activeCategory === "customer_client" ? (
          <SettingsPanel title="Customer Client">
            <SwitchField
              checked={draft.customer_client.active_overlay_enabled}
              label="Active overlay enabled"
              onChange={(checked) => updateDraft((settings) => ({ ...settings, customer_client: { ...settings.customer_client, active_overlay_enabled: checked } }))}
            />
            <SwitchField
              checked={draft.customer_client.reminder_sounds_enabled}
              label="Reminder sounds enabled"
              onChange={(checked) => updateDraft((settings) => ({ ...settings, customer_client: { ...settings.customer_client, reminder_sounds_enabled: checked } }))}
            />
            <SwitchField
              checked={draft.customer_client.final_warning_sound_enabled}
              label="Final warning sound enabled"
              onChange={(checked) => updateDraft((settings) => ({ ...settings, customer_client: { ...settings.customer_client, final_warning_sound_enabled: checked } }))}
            />
            <SelectField
              label="Overlay position"
              onChange={(value) => updateDraft((settings) => ({ ...settings, customer_client: { ...settings.customer_client, overlay_position: value as AdminSettings["customer_client"]["overlay_position"] } }))}
              options={[
                ["top-right", "Top right"],
                ["top-left", "Top left"],
                ["bottom-right", "Bottom right"],
                ["bottom-left", "Bottom left"]
              ]}
              value={draft.customer_client.overlay_position}
            />
            <TextField
              label="Customer-facing branding text"
              onChange={(value) => updateDraft((settings) => ({ ...settings, customer_client: { ...settings.customer_client, customer_branding_text: value } }))}
              value={draft.customer_client.customer_branding_text}
            />
          </SettingsPanel>
        ) : null}

        {activeCategory === "wake_power" ? (
          <SettingsPanel title="Wake & Power">
            <SwitchField
              checked={draft.wake_power.wake_on_lan_enabled}
              label="Wake-on-LAN enabled"
              onChange={(checked) => updateDraft((settings) => ({ ...settings, wake_power: { ...settings.wake_power, wake_on_lan_enabled: checked } }))}
            />
            <SwitchField checked={draft.wake_power.shutdown_on_expiration_enabled} label="Shutdown-on-expiration enabled" readOnly />
            <NumberField
              helper="Used for expected shutdown/offline diagnostics. Does not change power executables."
              label="Expected shutdown timeout"
              max={300}
              min={10}
              onChange={(value) => updateDraft((settings) => ({ ...settings, wake_power: { ...settings.wake_power, expected_shutdown_timeout_seconds: value } }))}
              value={draft.wake_power.expected_shutdown_timeout_seconds}
            />
            <TextField label="Power command safety status" readOnly value={draft.wake_power.power_command_safety_status} />
          </SettingsPanel>
        ) : null}

        {activeCategory === "members" ? (
          <SettingsPanel title="Members">
            <TextField label="Default member PIN policy" readOnly value={draft.members.default_member_pin_policy} />
            <p className={styles.panelNote}>Member PINs are optional in the current backend. Required-by-default PIN policy is intentionally not enabled in this milestone.</p>
          </SettingsPanel>
        ) : null}

        {activeCategory === "reports" ? (
          <SettingsPanel title="Reports">
            <SelectField
              label="Default Sales date period"
              onChange={(value) => updateDraft((settings) => ({ ...settings, reports: { ...settings.reports, default_sales_period: value as AdminSettings["reports"]["default_sales_period"] } }))}
              options={[
                ["today", "Today"],
                ["yesterday", "Yesterday"],
                ["this_week", "This week"],
                ["this_month", "This month"]
              ]}
              value={draft.reports.default_sales_period}
            />
            <TextField
              label="CSV export timezone/display"
              onChange={(value) => updateDraft((settings) => ({ ...settings, reports: { ...settings.reports, csv_timezone_display: value } }))}
              value={draft.reports.csv_timezone_display}
            />
          </SettingsPanel>
        ) : null}

        <section className={styles.diagnostics}>
          <h2>Protected Configuration</h2>
          <div className={styles.diagnosticGrid}>
            {Object.entries(response?.environment_only ?? {}).map(([key, value]) => (
              <div key={key}>
                <span>{key.replaceAll("_", " ")}</span>
                <strong>{value}</strong>
              </div>
            ))}
            {Object.entries(response?.read_only ?? {}).map(([key, value]) => (
              <div key={key}>
                <span>{key.replaceAll("_", " ")}</span>
                <strong>{value}</strong>
              </div>
            ))}
          </div>
        </section>
      </div>
    </section>
  );
}

function SettingsPanel({ children, title }: { children: ReactNode; title: string }) {
  return (
    <section className={styles.panel}>
      <h2>{title}</h2>
      <div className={styles.formGrid}>{children}</div>
    </section>
  );
}

function TextField({
  helper,
  label,
  onChange,
  readOnly = false,
  value
}: {
  helper?: string;
  label: string;
  onChange?: (value: string) => void;
  readOnly?: boolean;
  value: string;
}) {
  return (
    <label className={styles.field}>
      <span>{label}</span>
      <input
        onChange={(event) => onChange?.(event.target.value)}
        readOnly={readOnly}
        value={value}
      />
      {helper ? <small>{helper}</small> : null}
    </label>
  );
}

function NumberField({
  helper,
  label,
  max,
  min,
  onChange,
  value
}: {
  helper?: string;
  label: string;
  max: number;
  min: number;
  onChange: (value: number) => void;
  value: number;
}) {
  return (
    <label className={styles.field}>
      <span>{label}</span>
      <input
        inputMode="numeric"
        max={max}
        min={min}
        onChange={(event) => onChange(Number(event.target.value))}
        type="number"
        value={value}
      />
      {helper ? <small>{helper}</small> : null}
    </label>
  );
}

function SelectField({
  label,
  onChange,
  options,
  value
}: {
  label: string;
  onChange: (value: string) => void;
  options: [string, string][];
  value: string;
}) {
  return (
    <label className={styles.field}>
      <span>{label}</span>
      <select onChange={(event) => onChange(event.target.value)} value={value}>
        {options.map(([optionValue, labelText]) => (
          <option key={optionValue} value={optionValue}>
            {labelText}
          </option>
        ))}
      </select>
    </label>
  );
}

function SwitchField({
  checked,
  label,
  onChange,
  readOnly = false
}: {
  checked: boolean;
  label: string;
  onChange?: (checked: boolean) => void;
  readOnly?: boolean;
}) {
  return (
    <label className={styles.switchField}>
      <span>{label}</span>
      <input
        checked={checked}
        disabled={readOnly}
        onChange={(event) => onChange?.(event.target.checked)}
        type="checkbox"
      />
    </label>
  );
}
