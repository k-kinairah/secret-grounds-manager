"use client";

import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode
} from "react";
import {
  fetchAdminPricing,
  fetchAdminSettings,
  resetAdminSettings,
  updateAdminPricing,
  updateAdminSettings
} from "@/lib/api";
import type {
  AdminPricingSnapshot,
  AdminPricingUpdate,
  AdminSettings,
  AdminSettingsResponse
} from "@/lib/types";
import { formatRateDuration } from "@/lib/time";
import { PricingConfirmDialog } from "./PricingConfirmDialog";
import styles from "@/app/settings/settings.module.css";

type SettingsCategoryKey = keyof AdminSettings;
// "rates_pricing" is a curated editor over fixed rate_profiles /
// membership_products rows (GET/PUT /admin/pricing) -- not a persisted
// AdminSettings category, so it carries its own draft/Save and is excluded
// from the settings toolbar and the Protected Configuration block.
type CategoryKey = SettingsCategoryKey | "rates_pricing";

const categories: { key: CategoryKey; label: string; description: string }[] = [
  { key: "general", label: "General", description: "Shop identity and display defaults." },
  { key: "sessions", label: "Sessions", description: "Expiration, reminders, and manual end behavior." },
  { key: "customer_client", label: "Customer Client", description: "Customer-facing overlay and reminder presentation." },
  { key: "rates_pricing", label: "Rates & Pricing", description: "Edit Standard, VIP, and member session pricing." }
];

function cloneSettings(settings: AdminSettings): AdminSettings {
  return JSON.parse(JSON.stringify(settings)) as AdminSettings;
}

// Curated IANA zones for the General timezone picker. The server validates
// general.timezone_display against the full zoneinfo database and rejects
// anything invalid (422), so this list only needs to cover the common
// cases; a persisted value outside the list is still shown (see the
// timezone render in the General panel). "Asia/Manila" is the shop default.
const CURATED_TIMEZONES: string[] = [
  "Asia/Manila",
  "Asia/Hong_Kong",
  "Asia/Singapore",
  "Asia/Taipei",
  "Asia/Shanghai",
  "Asia/Tokyo",
  "Asia/Seoul",
  "Asia/Bangkok",
  "Asia/Jakarta",
  "Asia/Kuala_Lumpur",
  "Asia/Kolkata",
  "Asia/Dubai",
  "Australia/Perth",
  "Australia/Sydney",
  "Pacific/Guam",
  "Pacific/Auckland",
  "Pacific/Honolulu",
  "America/Los_Angeles",
  "America/Denver",
  "America/Chicago",
  "America/New_York",
  "America/Sao_Paulo",
  "Europe/London",
  "Europe/Paris",
  "Europe/Berlin",
  "Europe/Madrid",
  "UTC"
];

// Options for the timezone <select>: the curated list, plus the currently
// persisted value if it is something exotic set outside this UI.
function timezoneOptions(current: string): [string, string][] {
  const values = CURATED_TIMEZONES.includes(current)
    ? CURATED_TIMEZONES
    : [current, ...CURATED_TIMEZONES];
  return values.map((zone) => [zone, zone]);
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

  const activeMeta = categories.find((category) => category.key === activeCategory);

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

  async function resetCategory(category: SettingsCategoryKey | "all") {
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

        {activeCategory !== "rates_pricing" ? (
          <div className={styles.toolbar}>
            <div className={styles.toolbarStatus}>
              <strong>{activeMeta?.label}</strong>
              <span className={`${styles.statusPill} ${hasUnsavedChanges ? styles.statusPillDirty : styles.statusPillClean}`}>
                {hasUnsavedChanges ? "Unsaved changes" : "All changes saved"}
              </span>
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
        ) : null}

        {activeCategory === "general" ? (
          <CategoryPanel description="Shop identity and how it is presented across the dashboard and customer screens." title="General">
            <FieldGroup title="Shop identity">
              <TextField
                label="Shop display name"
                onChange={(value) => updateDraft((settings) => ({ ...settings, general: { ...settings.general, shop_display_name: value } }))}
                value={draft.general.shop_display_name}
              />
              <TextField
                helper="Shown under the shop name where the layout allows it."
                label="Optional subtitle"
                onChange={(value) => updateDraft((settings) => ({ ...settings, general: { ...settings.general, shop_subtitle: value || null } }))}
                value={draft.general.shop_subtitle ?? ""}
              />
              <SelectField
                helper="Used for scheduled rate and promo windows. Defaults to Asia/Manila."
                label="Timezone"
                onChange={(value) => updateDraft((settings) => ({ ...settings, general: { ...settings.general, timezone_display: value } }))}
                options={timezoneOptions(draft.general.timezone_display)}
                value={draft.general.timezone_display}
              />
            </FieldGroup>
          </CategoryPanel>
        ) : null}

        {activeCategory === "sessions" ? (
          <CategoryPanel description="How running sessions expire, warn the customer, and end." title="Sessions">
            <FieldGroup title="Expiration">
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
                helper="Powers off the PC once a capped session's grace period runs out."
                label="Automatic shutdown on capped-session expiration"
                onChange={(checked) =>
                  updateDraft((settings) => ({
                    ...settings,
                    sessions: { ...settings.sessions, automatic_shutdown_on_expiration: checked }
                  }))
                }
              />
            </FieldGroup>
            <FieldGroup title="Reminders">
              <TextField
                helper="Comma-separated seconds, descending. Example: 900, 300, 60"
                label="Reminder thresholds"
                onChange={setThresholdText}
                value={thresholdText}
              />
            </FieldGroup>
          </CategoryPanel>
        ) : null}

        {activeCategory === "customer_client" ? (
          <CategoryPanel description="What the customer sees and hears on their screen during a session." title="Customer Client">
            <FieldGroup title="Overlay">
              <SwitchField
                checked={draft.customer_client.active_overlay_enabled}
                helper="Shows the live time-remaining overlay on the customer screen."
                label="Active overlay enabled"
                onChange={(checked) => updateDraft((settings) => ({ ...settings, customer_client: { ...settings.customer_client, active_overlay_enabled: checked } }))}
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
            </FieldGroup>
            <FieldGroup title="Sounds">
              <SwitchField
                checked={draft.customer_client.reminder_sounds_enabled}
                helper="Plays a chime at each reminder threshold."
                label="Reminder sounds enabled"
                onChange={(checked) => updateDraft((settings) => ({ ...settings, customer_client: { ...settings.customer_client, reminder_sounds_enabled: checked } }))}
              />
              <SwitchField
                checked={draft.customer_client.final_warning_sound_enabled}
                helper="Plays a distinct alert as the session reaches its final warning."
                label="Final warning sound enabled"
                onChange={(checked) => updateDraft((settings) => ({ ...settings, customer_client: { ...settings.customer_client, final_warning_sound_enabled: checked } }))}
              />
            </FieldGroup>
          </CategoryPanel>
        ) : null}

        {activeCategory === "rates_pricing" ? <RatesPricingPanel /> : null}

        {activeCategory !== "rates_pricing" ? (
          <section className={styles.diagnostics}>
            <div className={styles.diagnosticsHead}>
              <h2>Protected Configuration</h2>
              <p className={styles.diagnosticsLead}>
                Fixed in code or the deployment environment. Listed for reference — not editable from Settings.
              </p>
            </div>
            <dl className={styles.diagnosticList}>
              {Object.entries(response?.environment_only ?? {}).map(([key, value]) => (
                <div className={styles.diagnosticItem} key={key}>
                  <dt>{key.replaceAll("_", " ")}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
              {Object.entries(response?.read_only ?? {}).map(([key, value]) => (
                <div className={styles.diagnosticItem} key={key}>
                  <dt>{key.replaceAll("_", " ")}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
          </section>
        ) : null}
      </div>
    </section>
  );
}

const PRICING_DISCLAIMER =
  "Changes apply to new sessions and new purchases only. Running sessions, existing balances, and past transactions are unaffected.";
const SECONDS_PER_MINUTE = 60;
const SECONDS_PER_HOUR = 3600;

type KnobKey = "standardGuest" | "newAccount" | "topupSmall" | "topupLarge" | "promo";

interface KnobDraft {
  price: string;
  amount: string;
}

interface PricingDraft {
  standardGuest: KnobDraft;
  newAccount: KnobDraft;
  topupSmall: KnobDraft;
  topupLarge: KnobDraft;
  vipHourlyPrice: string;
  promo: KnobDraft;
}

function trimZeros(value: string | number): string {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? String(numeric) : String(value);
}

function pesoText(value: number): string {
  return `₱${trimZeros(value.toFixed(2))}`;
}

function snapshotToDraft(snapshot: AdminPricingSnapshot): PricingDraft {
  return {
    standardGuest: {
      price: trimZeros(snapshot.standard_guest.price),
      amount: trimZeros(snapshot.standard_guest.seconds / SECONDS_PER_MINUTE)
    },
    newAccount: {
      price: trimZeros(snapshot.standard_member_new_account.price),
      amount: trimZeros(snapshot.standard_member_new_account.seconds / SECONDS_PER_HOUR)
    },
    topupSmall: {
      price: trimZeros(snapshot.standard_member_topup_small.price),
      amount: trimZeros(snapshot.standard_member_topup_small.seconds / SECONDS_PER_HOUR)
    },
    topupLarge: {
      price: trimZeros(snapshot.standard_member_topup_large.price),
      amount: trimZeros(snapshot.standard_member_topup_large.seconds / SECONDS_PER_HOUR)
    },
    vipHourlyPrice: trimZeros(snapshot.vip_guest_hourly.price),
    promo: {
      price: trimZeros(snapshot.promo.price),
      amount: trimZeros(snapshot.promo.seconds / SECONDS_PER_HOUR)
    }
  };
}

function positive(value: string): number | null {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? numeric : null;
}

function draftToUpdate(draft: PricingDraft): { update: AdminPricingUpdate } | { error: string } {
  const price = (value: string) => {
    const numeric = positive(value);
    return numeric === null ? null : numeric.toFixed(2);
  };
  const seconds = (value: string, perUnit: number) => {
    const numeric = positive(value);
    return numeric === null ? null : Math.round(numeric * perUnit);
  };

  const parts = {
    standard_guest: { price: price(draft.standardGuest.price), seconds: seconds(draft.standardGuest.amount, SECONDS_PER_MINUTE) },
    standard_member_new_account: { price: price(draft.newAccount.price), seconds: seconds(draft.newAccount.amount, SECONDS_PER_HOUR) },
    standard_member_topup_small: { price: price(draft.topupSmall.price), seconds: seconds(draft.topupSmall.amount, SECONDS_PER_HOUR) },
    standard_member_topup_large: { price: price(draft.topupLarge.price), seconds: seconds(draft.topupLarge.amount, SECONDS_PER_HOUR) },
    // VIP base duration is fixed at exactly 1 hour by design -- the 2h/3h/4h
    // rows are regenerated server-side as x2/x3/x4 of this pair.
    vip_guest_hourly: { price: price(draft.vipHourlyPrice), seconds: SECONDS_PER_HOUR },
    promo: { price: price(draft.promo.price), seconds: seconds(draft.promo.amount, SECONDS_PER_HOUR) }
  };

  if (Object.values(parts).some((part) => part.price === null || part.seconds === null)) {
    return { error: "Enter a price above ₱0 and a duration above 0 for every field." };
  }

  return {
    update: {
      standard_guest: { price: parts.standard_guest.price!, seconds: parts.standard_guest.seconds! },
      standard_member_new_account: {
        price: parts.standard_member_new_account.price!,
        seconds: parts.standard_member_new_account.seconds!
      },
      standard_member_topup_small: {
        price: parts.standard_member_topup_small.price!,
        seconds: parts.standard_member_topup_small.seconds!
      },
      standard_member_topup_large: {
        price: parts.standard_member_topup_large.price!,
        seconds: parts.standard_member_topup_large.seconds!
      },
      vip_guest_hourly: { price: parts.vip_guest_hourly.price!, seconds: parts.vip_guest_hourly.seconds! },
      promo: { price: parts.promo.price!, seconds: parts.promo.seconds! }
    }
  };
}

// One row of the "Apply pricing" before/after confirmation. `beforeText` /
// `afterText` are already formatted (₱ + duration) so the dialog stays dumb.
export interface PricingChangeRow {
  key: string;
  label: string;
  sublabel?: string;
  beforeText: string;
  afterText: string;
}

function moneyTimeText(entry: { price: string; seconds: number }): string {
  return `${pesoText(Number(entry.price))} · ${formatRateDuration(entry.seconds)}`;
}

function moneyTimeEqual(
  a: { price: string; seconds: number },
  b: { price: string; seconds: number }
): boolean {
  return Number(a.price) === Number(b.price) && a.seconds === b.seconds;
}

// Pure. Returns ONLY the rows whose (price, seconds) actually differ between
// the last-saved snapshot and the exact AdminPricingUpdate that will be PUT.
// The VIP 2h/3h/4h tiers are derived exactly as
// server/pricing_service.apply_pricing_update does -- hourly x multiple, with
// the durations staying 3600 / 7200 / 10800 / 14400 -- and only appear when
// the VIP hourly price changes. The promo is one logical row: the server
// writes the same (price, seconds) to both the VIP guest promo package and
// the VIP member promo product in one transaction.
export function pricingChanges(
  snapshot: AdminPricingSnapshot,
  update: AdminPricingUpdate
): PricingChangeRow[] {
  const rows: PricingChangeRow[] = [];
  const add = (
    key: string,
    label: string,
    before: { price: string; seconds: number },
    after: { price: string; seconds: number },
    sublabel?: string
  ) => {
    if (!moneyTimeEqual(before, after)) {
      rows.push({
        key,
        label,
        sublabel,
        beforeText: moneyTimeText(before),
        afterText: moneyTimeText(after)
      });
    }
  };

  add("standard_guest", "Standard Guest", snapshot.standard_guest, update.standard_guest);
  add(
    "standard_member_new_account",
    "Member — New Account",
    snapshot.standard_member_new_account,
    update.standard_member_new_account
  );
  add(
    "standard_member_topup_small",
    "Member — Top-up (small)",
    snapshot.standard_member_topup_small,
    update.standard_member_topup_small
  );
  add(
    "standard_member_topup_large",
    "Member — Top-up (large)",
    snapshot.standard_member_topup_large,
    update.standard_member_topup_large
  );

  if (!moneyTimeEqual(snapshot.vip_guest_hourly, update.vip_guest_hourly)) {
    add("vip_guest_1hr", "VIP Guest — 1 hr", snapshot.vip_guest_hourly, update.vip_guest_hourly);
    const hourlyPrice = Number(update.vip_guest_hourly.price);
    for (const multiple of [2, 3, 4] as const) {
      const tier = snapshot.vip_guest_tiers.find((entry) => entry.multiple === multiple);
      const before = tier
        ? { price: tier.price, seconds: tier.seconds }
        : {
            price: (Number(snapshot.vip_guest_hourly.price) * multiple).toFixed(2),
            seconds: SECONDS_PER_HOUR * multiple
          };
      add(
        `vip_guest_${multiple}hr`,
        `VIP Guest — ${multiple} hr`,
        before,
        { price: (hourlyPrice * multiple).toFixed(2), seconds: SECONDS_PER_HOUR * multiple },
        "Regenerated automatically from the hourly rate"
      );
    }
  }

  add(
    "promo",
    "VIP Promo",
    snapshot.promo,
    update.promo,
    "Applies to the VIP guest promo package and the VIP member promo product together"
  );

  return rows;
}

// Mirrors server/rate_calculator.calculate_metered_time: floor(amount / price * duration).
function meteredExample(amount: number, price: number, durationSeconds: number): string {
  if (!(price > 0) || !(durationSeconds > 0)) {
    return "—";
  }
  const result = Math.floor((amount / price) * durationSeconds);
  return result >= 1 ? formatRateDuration(result) : "under 1 sec";
}

function PricingField({
  label,
  unit,
  price,
  amount,
  onPrice,
  onAmount
}: {
  label?: string;
  unit: "min" | "hr";
  price: string;
  amount: string;
  onPrice: (value: string) => void;
  onAmount?: (value: string) => void;
}) {
  const locked = onAmount === undefined;
  return (
    <div className={styles.pricingField}>
      {label ? <span className={styles.pricingFieldLabel}>{label}</span> : null}
      <div className={styles.pricingFieldInputs}>
        <label className={styles.pricingInput}>
          <span className={styles.pricingInputCaption}>Price</span>
          <span className={styles.pricingInputBox}>
            <span className={styles.pricingInputAffix}>₱</span>
            <input
              inputMode="decimal"
              onChange={(event) => onPrice(event.target.value)}
              placeholder="0.00"
              value={price}
            />
          </span>
        </label>
        <label className={`${styles.pricingInput} ${locked ? styles.pricingInputLocked : ""}`}>
          <span className={styles.pricingInputCaption}>Duration</span>
          <span className={styles.pricingInputBox}>
            <input
              inputMode="decimal"
              onChange={(event) => onAmount?.(event.target.value)}
              placeholder="0"
              readOnly={locked}
              value={amount}
            />
            <span className={styles.pricingInputAffix}>{unit === "min" ? "min" : "hr"}</span>
          </span>
        </label>
      </div>
    </div>
  );
}

function PricingPreview({
  caption,
  rows
}: {
  caption: string;
  rows: { key: string; label: string; value: string }[];
}) {
  return (
    <div className={styles.pricingPreview}>
      <span className={styles.pricingPreviewCaption}>{caption}</span>
      <div className={styles.pricingPreviewRows}>
        {rows.map((row) => (
          <div className={styles.pricingPreviewRow} key={row.key}>
            <span>{row.label}</span>
            <strong>{row.value}</strong>
          </div>
        ))}
      </div>
    </div>
  );
}

// Curated editor over fixed rate_profiles / membership_products rows via
// GET/PUT /admin/pricing. It is not a generic rate CRUD: the six knobs below
// are the only editable values, and linked rows (VIP 2h/3h/4h tiers, the
// VIP promo guest package + member product) are kept consistent by the
// server in one transaction. Nothing here changes session/purchase logic.
function RatesPricingPanel() {
  const [snapshot, setSnapshot] = useState<AdminPricingSnapshot | null>(null);
  const [draft, setDraft] = useState<PricingDraft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  // The exact AdminPricingUpdate awaiting confirmation. While non-null the
  // "Apply pricing" dialog is open; the PUT has NOT been sent yet.
  const [pendingUpdate, setPendingUpdate] = useState<AdminPricingUpdate | null>(null);

  useEffect(() => {
    let isMounted = true;
    fetchAdminPricing()
      .then((next) => {
        if (isMounted) {
          setSnapshot(next);
          setDraft(snapshotToDraft(next));
          setError(null);
        }
      })
      .catch((loadError) => {
        if (isMounted) {
          setError(loadError instanceof Error ? loadError.message : "Could not load pricing.");
        }
      });
    return () => {
      isMounted = false;
    };
  }, []);

  const pristineDraft = useMemo(
    () => (snapshot === null ? null : snapshotToDraft(snapshot)),
    [snapshot]
  );
  const hasChanges = useMemo(
    () => draft !== null && pristineDraft !== null && JSON.stringify(draft) !== JSON.stringify(pristineDraft),
    [draft, pristineDraft]
  );

  function setKnob(key: KnobKey, part: keyof KnobDraft, value: string) {
    setDraft((current) => (current === null ? current : { ...current, [key]: { ...current[key], [part]: value } }));
    setMessage(null);
  }

  // "Save pricing" only validates + builds the update and opens the
  // confirmation dialog. It never calls the API or mutates saved pricing.
  function save() {
    if (draft === null || isSaving || pendingUpdate !== null) {
      return;
    }
    const built = draftToUpdate(draft);
    if ("error" in built) {
      setError(built.error);
      setMessage(null);
      return;
    }
    setError(null);
    setMessage(null);
    setPendingUpdate(built.update);
  }

  // The only path that calls PUT /admin/pricing. Invoked by "Apply pricing".
  async function commit(update: AdminPricingUpdate) {
    if (isSaving) {
      return;
    }
    setIsSaving(true);
    setError(null);
    setMessage(null);
    try {
      const next = await updateAdminPricing(update);
      setSnapshot(next);
      setDraft(snapshotToDraft(next));
      setMessage("Pricing saved. New sessions and purchases will use the updated values.");
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Could not save pricing.");
    } finally {
      setIsSaving(false);
      setPendingUpdate(null);
    }
  }

  const standardGuestPrice = draft ? Number(draft.standardGuest.price) : 0;
  const standardGuestSeconds = draft ? Number(draft.standardGuest.amount) * SECONDS_PER_MINUTE : 0;
  const vipHourly = draft ? Number(draft.vipHourlyPrice) : 0;

  return (
    <section className={`${styles.panel} ${styles.pricingPanel}`}>
      <header className={styles.pricingHeader}>
        <div className={styles.pricingHeaderMain}>
          <h2>Rates &amp; Pricing</h2>
          <p className={styles.pricingLead}>Session and membership pricing used across the shop.</p>
        </div>
        <div className={styles.pricingHeaderAside}>
          {draft !== null && hasChanges ? <span className={styles.pricingDirty}>Unsaved changes</span> : null}
          <div className={styles.pricingDisclaimer}>
            <svg className={styles.pricingInfoIcon} viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <circle cx="12" cy="12" r="9" />
              <path d="M12 11v5" />
              <path d="M12 7.4h.01" />
            </svg>
            <span>{PRICING_DISCLAIMER}</span>
          </div>
        </div>
      </header>

      {error ? (
        <div className={`${styles.message} ${styles.errorMessage}`} role="alert">
          {error}
        </div>
      ) : null}
      {message ? (
        <div className={`${styles.message} ${styles.successMessage}`} role="status">
          {message}
        </div>
      ) : null}

      {draft === null ? (
        <p className={styles.pricingLoading}>{error ? null : "Loading pricing…"}</p>
      ) : (
        <>
          <div className={styles.pricingGroups}>
            <article className={styles.ratesGroup}>
              <div className={styles.pricingGroupHead}>
                <h3 className={styles.ratesGroupTitle}>Standard</h3>
              </div>

              <div className={styles.ratesSubgroup}>
                <div className={styles.pricingSubHead}>
                  <h4 className={styles.ratesSubgroupTitle}>Guest</h4>
                  <p className={styles.pricingSubNote}>
                    Linear metered rate — a cash amount converts straight to time.
                  </p>
                </div>
                <PricingField
                  label="Metered rate"
                  unit="min"
                  price={draft.standardGuest.price}
                  amount={draft.standardGuest.amount}
                  onPrice={(value) => setKnob("standardGuest", "price", value)}
                  onAmount={(value) => setKnob("standardGuest", "amount", value)}
                />
                <PricingPreview
                  caption="Examples"
                  rows={[5, 10, 15].map((amount) => ({
                    key: String(amount),
                    label: pesoText(amount),
                    value: meteredExample(amount, standardGuestPrice, standardGuestSeconds)
                  }))}
                />
              </div>

              <div className={styles.ratesSubgroup}>
                <div className={styles.pricingSubHead}>
                  <h4 className={styles.ratesSubgroupTitle}>Member</h4>
                </div>
                <PricingField
                  label="New member account"
                  unit="hr"
                  price={draft.newAccount.price}
                  amount={draft.newAccount.amount}
                  onPrice={(value) => setKnob("newAccount", "price", value)}
                  onAmount={(value) => setKnob("newAccount", "amount", value)}
                />
                <div className={styles.pricingStack}>
                  <span className={styles.pricingStackLabel}>Existing member</span>
                  <PricingField
                    unit="hr"
                    price={draft.topupSmall.price}
                    amount={draft.topupSmall.amount}
                    onPrice={(value) => setKnob("topupSmall", "price", value)}
                    onAmount={(value) => setKnob("topupSmall", "amount", value)}
                  />
                  <PricingField
                    unit="hr"
                    price={draft.topupLarge.price}
                    amount={draft.topupLarge.amount}
                    onPrice={(value) => setKnob("topupLarge", "price", value)}
                    onAmount={(value) => setKnob("topupLarge", "amount", value)}
                  />
                </div>
              </div>
            </article>

            <article className={`${styles.ratesGroup} ${styles.pricingGroupVip}`}>
              <div className={styles.pricingGroupHead}>
                <h3 className={styles.ratesGroupTitle}>VIP</h3>
              </div>

              <div className={styles.ratesSubgroup}>
                <div className={styles.pricingSubHead}>
                  <h4 className={styles.ratesSubgroupTitle}>Guest</h4>
                  <p className={styles.pricingSubNote}>No separate VIP account is required.</p>
                </div>
                <PricingField
                  label="Hourly rate (per 1 hour)"
                  unit="hr"
                  price={draft.vipHourlyPrice}
                  amount="1"
                  onPrice={(value) => {
                    setDraft((current) => (current === null ? current : { ...current, vipHourlyPrice: value }));
                    setMessage(null);
                  }}
                />
                <PricingPreview
                  caption="Regenerated tiers"
                  rows={[2, 3, 4].map((multiple) => ({
                    key: String(multiple),
                    label: Number.isFinite(vipHourly) ? pesoText(vipHourly * multiple) : "—",
                    value: formatRateDuration(multiple * SECONDS_PER_HOUR)
                  }))}
                />
                <p className={styles.pricingSubNote}>
                  The 2-hour, 3-hour, and 4-hour VIP rows are regenerated automatically from the hourly
                  rate and stay available for the cashier session dialog.
                </p>
              </div>

              <div className={styles.ratesSubgroup}>
                <div className={styles.pricingSubHead}>
                  <h4 className={styles.ratesSubgroupTitle}>Member</h4>
                </div>
                <ul className={styles.pricingFacts}>
                  <li>Existing members can use their member account.</li>
                  <li>
                    VIP conversion: {snapshot?.vip_member_conversion_ratio ?? 2} member hours = 1 VIP hour.
                  </li>
                </ul>
              </div>
            </article>

            <article className={`${styles.ratesGroup} ${styles.ratesGroupPromo}`}>
              <div className={styles.pricingGroupHead}>
                <h3 className={styles.ratesGroupTitle}>Promo</h3>
              </div>
              <PricingField
                label="VIP promo"
                unit="hr"
                price={draft.promo.price}
                amount={draft.promo.amount}
                onPrice={(value) => setKnob("promo", "price", value)}
                onAmount={(value) => setKnob("promo", "amount", value)}
              />
              <p className={styles.pricingSubNote}>
                Saved to the VIP guest promo package and the VIP member promo product together. Still
                part of pricing configuration, not the Promotions system.
              </p>
            </article>
          </div>

          <div className={styles.pricingActions}>
            <span className={styles.pricingActionsHint}>
              {hasChanges ? "You have unsaved pricing changes." : "All pricing changes are saved."}
            </span>
            <div className={styles.pricingActionsButtons}>
              <button
                className={styles.secondaryButton}
                disabled={!hasChanges || isSaving}
                onClick={() => {
                  if (pristineDraft) {
                    setDraft(pristineDraft);
                    setError(null);
                    setMessage(null);
                  }
                }}
                type="button"
              >
                Discard changes
              </button>
              <button
                className={styles.primaryButton}
                disabled={!hasChanges || isSaving || pendingUpdate !== null}
                onClick={save}
                type="button"
              >
                {isSaving ? "Saving…" : "Save pricing"}
              </button>
            </div>
          </div>
        </>
      )}

      {pendingUpdate !== null && snapshot !== null ? (
        <PricingConfirmDialog
          changes={pricingChanges(snapshot, pendingUpdate)}
          isLoading={isSaving}
          onCancel={() => setPendingUpdate(null)}
          onConfirm={() => void commit(pendingUpdate)}
        />
      ) : null}
    </section>
  );
}

function CategoryPanel({
  children,
  description,
  title
}: {
  children: ReactNode;
  description?: string;
  title: string;
}) {
  return (
    <section className={styles.panel}>
      <div className={styles.panelHead}>
        <h2>{title}</h2>
        {description ? <p className={styles.panelLead}>{description}</p> : null}
      </div>
      {children}
    </section>
  );
}

function FieldGroup({
  children,
  note,
  title
}: {
  children: ReactNode;
  note?: string;
  title: string;
}) {
  return (
    <div className={styles.group}>
      <div className={styles.groupHead}>
        <h3 className={styles.groupTitle}>{title}</h3>
        {note ? <p className={styles.groupNote}>{note}</p> : null}
      </div>
      <div className={styles.rows}>{children}</div>
    </div>
  );
}

// One setting = one row: label (+ optional hint) on the left, the control on
// the right. Wrapping the whole row in <label> keeps the click/focus target
// association without per-field ids.
function Row({
  children,
  hint,
  label
}: {
  children: ReactNode;
  hint?: string;
  label: string;
}) {
  return (
    <label className={styles.row}>
      <span className={styles.rowLabel}>
        <span className={styles.rowLabelText}>{label}</span>
        {hint ? <span className={styles.rowHint}>{hint}</span> : null}
      </span>
      <span className={styles.rowControl}>{children}</span>
    </label>
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
    <Row hint={helper} label={label}>
      <input
        className={styles.control}
        onChange={(event) => onChange?.(event.target.value)}
        readOnly={readOnly}
        value={value}
      />
    </Row>
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
    <Row hint={helper} label={label}>
      <input
        className={styles.control}
        inputMode="numeric"
        max={max}
        min={min}
        onChange={(event) => onChange(Number(event.target.value))}
        type="number"
        value={value}
      />
    </Row>
  );
}

// Custom listbox. A native <select>'s open popup is drawn by the OS and can't
// be themed, so this is a button + role="listbox" following the WAI-ARIA
// listbox pattern (roving aria-activedescendant, type-agnostic key handling,
// click-outside + Escape to close, focus returns to the trigger).
function SelectField({
  helper,
  label,
  onChange,
  options,
  value
}: {
  helper?: string;
  label: string;
  onChange: (value: string) => void;
  options: [string, string][];
  value: string;
}) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const wrapRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const baseId = useId();

  const selectedIndex = options.findIndex(([optionValue]) => optionValue === value);
  const selectedLabel = selectedIndex >= 0 ? options[selectedIndex][1] : value;

  function openList() {
    setActiveIndex(selectedIndex >= 0 ? selectedIndex : 0);
    setOpen(true);
  }

  function closeList(returnFocus: boolean) {
    setOpen(false);
    if (returnFocus) {
      triggerRef.current?.focus();
    }
  }

  function commit(index: number) {
    const option = options[index];
    if (option) {
      onChange(option[0]);
    }
    closeList(true);
  }

  // While open: keep the active option scrolled into view, and close on any
  // pointer press outside the widget.
  useEffect(() => {
    if (!open) {
      return;
    }
    listRef.current?.focus();
    function onPointerDown(event: PointerEvent) {
      if (wrapRef.current && !wrapRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  useEffect(() => {
    if (open) {
      (listRef.current?.children[activeIndex] as HTMLElement | undefined)?.scrollIntoView({ block: "nearest" });
    }
  }, [open, activeIndex]);

  function onTriggerKeyDown(event: ReactKeyboardEvent<HTMLButtonElement>) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp" || event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openList();
    }
  }

  function onListKeyDown(event: ReactKeyboardEvent<HTMLUListElement>) {
    if (event.key === "Escape" || event.key === "Tab") {
      closeList(event.key === "Escape");
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((index) => Math.min(options.length - 1, index + 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((index) => Math.max(0, index - 1));
    } else if (event.key === "Home") {
      event.preventDefault();
      setActiveIndex(0);
    } else if (event.key === "End") {
      event.preventDefault();
      setActiveIndex(options.length - 1);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      commit(activeIndex);
    }
  }

  return (
    <Row hint={helper} label={label}>
      {/* Stop clicks reaching the wrapping <label>, which would re-dispatch
          them to the trigger and reopen the list right after a selection. */}
      <div className={styles.selectWrap} onClick={(event) => event.stopPropagation()} ref={wrapRef}>
        <button
          aria-controls={`${baseId}-list`}
          aria-expanded={open}
          aria-haspopup="listbox"
          className={styles.selectTrigger}
          onClick={() => (open ? closeList(false) : openList())}
          onKeyDown={onTriggerKeyDown}
          ref={triggerRef}
          type="button"
        >
          <span>{selectedLabel}</span>
          <svg aria-hidden="true" className={styles.selectChevron} viewBox="0 0 24 24">
            <path d="M6 9l6 6 6-6" />
          </svg>
        </button>
        {open ? (
          <ul
            aria-activedescendant={`${baseId}-opt-${activeIndex}`}
            aria-label={label}
            className={styles.selectPopup}
            id={`${baseId}-list`}
            onKeyDown={onListKeyDown}
            ref={listRef}
            role="listbox"
            tabIndex={-1}
          >
            {options.map(([optionValue, optionLabel], index) => (
              <li
                aria-selected={optionValue === value}
                className={`${styles.selectOption} ${index === activeIndex ? styles.selectOptionActive : ""}`}
                id={`${baseId}-opt-${index}`}
                key={optionValue}
                onClick={() => commit(index)}
                onMouseEnter={() => setActiveIndex(index)}
                role="option"
              >
                {optionLabel}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </Row>
  );
}

function SwitchField({
  checked,
  helper,
  label,
  onChange,
  readOnly = false
}: {
  checked: boolean;
  helper?: string;
  label: string;
  onChange?: (checked: boolean) => void;
  readOnly?: boolean;
}) {
  return (
    <Row hint={helper} label={label}>
      <input
        checked={checked}
        className={styles.toggle}
        disabled={readOnly}
        onChange={(event) => onChange?.(event.target.checked)}
        role="switch"
        type="checkbox"
      />
    </Row>
  );
}
