from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from server.database import get_connection
from server.schemas import (
    AdminSettings,
    AdminSettingsResponse,
    AdminSettingsResetRequest,
    AdminSettingsUpdateRequest,
    ClientSafeSettings,
)
from server.settings import (
    AUTOMATIC_SHUTDOWN_ON_EXPIRATION,
    CSV_TIMEZONE_DISPLAY,
    CUSTOMER_BRANDING_TEXT,
    CUSTOMER_OVERLAY_ENABLED,
    CUSTOMER_OVERLAY_POSITION,
    DEFAULT_SALES_PERIOD,
    EXPECTED_SHUTDOWN_TIMEOUT_SECONDS,
    EXPIRATION_GRACE_SECONDS,
    EXPIRATION_SOUND_ENABLED,
    MANUAL_END_DEFAULT_BEHAVIOR,
    PC_NAMING_PREFIX,
    REMINDER_SOUNDS_ENABLED,
    REMINDER_THRESHOLDS_SECONDS,
    SHOP_DISPLAY_NAME,
    SHOP_SUBTITLE,
    TEST_MODE,
    TIMEZONE_DISPLAY,
    WAKE_ON_LAN_ENABLED,
)
from server.time_utils import current_utc_time


SETTING_KEY = "admin_settings"


@dataclass(frozen=True)
class SettingsAuditChange:
    category: str
    changed_fields: list[str]
    old_values: dict[str, Any]
    new_values: dict[str, Any]


def _safe_manual_end_behavior(value: str) -> str:
    return value if value in {"lock_only", "lock_and_settle"} else "lock_only"


def _safe_overlay_position(value: str) -> str:
    allowed = {"top-right", "top-left", "bottom-right", "bottom-left"}
    return value if value in allowed else "top-right"


def _safe_sales_period(value: str) -> str:
    allowed = {"today", "yesterday", "this_week", "this_month"}
    return value if value in allowed else "today"


def build_default_settings() -> AdminSettings:
    grace_seconds = min(600, max(30, EXPIRATION_GRACE_SECONDS))
    shutdown_timeout = min(300, max(10, EXPECTED_SHUTDOWN_TIMEOUT_SECONDS))
    thresholds = REMINDER_THRESHOLDS_SECONDS or [900, 300, 60]
    thresholds = sorted({max(1, min(24 * 60 * 60, item)) for item in thresholds}, reverse=True)
    return AdminSettings.model_validate(
        {
            "general": {
                "shop_display_name": SHOP_DISPLAY_NAME,
                "shop_subtitle": SHOP_SUBTITLE or None,
                "pc_naming_prefix": PC_NAMING_PREFIX,
                "timezone_display": TIMEZONE_DISPLAY,
            },
            "sessions": {
                "expiration_grace_seconds": grace_seconds,
                "automatic_shutdown_on_expiration": AUTOMATIC_SHUTDOWN_ON_EXPIRATION,
                "reminder_thresholds_seconds": thresholds,
                "manual_end_default_behavior": _safe_manual_end_behavior(
                    MANUAL_END_DEFAULT_BEHAVIOR
                ),
            },
            "customer_client": {
                "reminder_sounds_enabled": REMINDER_SOUNDS_ENABLED,
                "final_warning_sound_enabled": EXPIRATION_SOUND_ENABLED,
                "active_overlay_enabled": CUSTOMER_OVERLAY_ENABLED,
                "overlay_position": _safe_overlay_position(CUSTOMER_OVERLAY_POSITION),
                "customer_branding_text": CUSTOMER_BRANDING_TEXT,
            },
            "wake_power": {
                "wake_on_lan_enabled": WAKE_ON_LAN_ENABLED,
                "shutdown_on_expiration_enabled": AUTOMATIC_SHUTDOWN_ON_EXPIRATION,
                "expected_shutdown_timeout_seconds": shutdown_timeout,
                "power_command_safety_status": power_command_safety_status(),
            },
            "members": {
                "default_member_pin_policy": "optional",
            },
            "reports": {
                "default_sales_period": _safe_sales_period(DEFAULT_SALES_PERIOD),
                "csv_timezone_display": CSV_TIMEZONE_DISPLAY,
            },
        }
    )


def power_command_safety_status() -> str:
    if TEST_MODE:
        return "Test mode active: real power commands are blocked."
    return "Production mode: power commands still use the fixed server/client allowlist."


class AdminSettingsService:
    """Typed persisted settings with environment defaults and immutable safety flags."""

    def get_settings(self) -> AdminSettings:
        defaults = build_default_settings()
        with get_connection() as connection:
            row = connection.execute(
                "SELECT value_json FROM admin_settings WHERE setting_key = ?",
                (SETTING_KEY,),
            ).fetchone()
        if row is None:
            return defaults

        try:
            stored = json.loads(row["value_json"])
        except json.JSONDecodeError:
            return defaults

        merged = defaults.model_dump(mode="json")
        for category, values in stored.items():
            if isinstance(values, dict) and isinstance(merged.get(category), dict):
                merged[category].update(values)
        merged["wake_power"]["power_command_safety_status"] = power_command_safety_status()
        return AdminSettings.model_validate(merged)

    def get_response(self) -> AdminSettingsResponse:
        updated_at = None
        with get_connection() as connection:
            row = connection.execute(
                "SELECT updated_at FROM admin_settings WHERE setting_key = ?",
                (SETTING_KEY,),
            ).fetchone()
        if row is not None:
            updated_at = row["updated_at"]
        return AdminSettingsResponse.model_validate(
            {
                "settings": self.get_settings(),
                "environment_only": {
                    "test_mode": "Immutable from Settings",
                    "database_path": "Environment only",
                    "authentication_secrets": "Environment only",
                    "power_command_executables": "Fixed allowlist in code",
                },
                "read_only": {
                    "pc_naming_prefix": "Display only for this milestone",
                    "power_command_safety_status": power_command_safety_status(),
                    "default_member_pin_policy": "Optional; member PIN enforcement is handled by member records",
                },
                "restart_required_fields": [],
                "updated_at": updated_at,
            }
        )

    def get_client_settings(self) -> ClientSafeSettings:
        settings = self.get_settings()
        return ClientSafeSettings(
            shop_display_name=settings.general.shop_display_name,
            shop_subtitle=settings.general.shop_subtitle,
            reminder_thresholds_seconds=settings.sessions.reminder_thresholds_seconds,
            reminder_sounds_enabled=settings.customer_client.reminder_sounds_enabled,
            final_warning_sound_enabled=settings.customer_client.final_warning_sound_enabled,
            active_overlay_enabled=settings.customer_client.active_overlay_enabled,
            overlay_position=settings.customer_client.overlay_position,
            customer_branding_text=settings.customer_client.customer_branding_text,
        )

    def update_settings(
        self,
        request: AdminSettingsUpdateRequest,
        actor_user_id: str,
    ) -> AdminSettingsResponse:
        old_settings = self.get_settings()
        normalized = self._normalize_update(request)
        changes = self._diff_settings(old_settings, normalized)
        now = current_utc_time().isoformat()
        with get_connection() as connection:
            connection.execute("BEGIN")
            connection.execute(
                """
                INSERT INTO admin_settings (
                    setting_key,
                    value_json,
                    updated_by_user_id,
                    updated_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(setting_key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_by_user_id = excluded.updated_by_user_id,
                    updated_at = excluded.updated_at
                """,
                (
                    SETTING_KEY,
                    json.dumps(normalized.model_dump(mode="json"), sort_keys=True),
                    actor_user_id,
                    now,
                ),
            )
            for change in changes:
                self._insert_audit_event(
                    connection,
                    "settings_updated",
                    change,
                    actor_user_id,
                    now,
                )
        return self.get_response()

    def reset_settings(
        self,
        request: AdminSettingsResetRequest,
        actor_user_id: str,
    ) -> AdminSettingsResponse:
        current = self.get_settings()
        defaults = build_default_settings()
        current_payload = current.model_dump(mode="json")
        default_payload = defaults.model_dump(mode="json")
        categories = (
            list(default_payload)
            if request.category == "all"
            else [request.category]
        )
        for category in categories:
            current_payload[category] = default_payload[category]
        normalized = AdminSettings.model_validate(current_payload)
        changes = self._diff_settings(current, normalized)
        now = current_utc_time().isoformat()
        with get_connection() as connection:
            connection.execute("BEGIN")
            connection.execute(
                """
                INSERT INTO admin_settings (
                    setting_key,
                    value_json,
                    updated_by_user_id,
                    updated_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(setting_key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_by_user_id = excluded.updated_by_user_id,
                    updated_at = excluded.updated_at
                """,
                (
                    SETTING_KEY,
                    json.dumps(normalized.model_dump(mode="json"), sort_keys=True),
                    actor_user_id,
                    now,
                ),
            )
            for change in changes:
                self._insert_audit_event(
                    connection,
                    "settings_reset",
                    change,
                    actor_user_id,
                    now,
                )
        return self.get_response()

    def _normalize_update(self, request: AdminSettingsUpdateRequest) -> AdminSettings:
        payload = request.model_dump(mode="json")
        payload["general"]["pc_naming_prefix"] = self.get_settings().general.pc_naming_prefix
        payload["wake_power"]["power_command_safety_status"] = power_command_safety_status()
        payload["wake_power"]["shutdown_on_expiration_enabled"] = payload["sessions"][
            "automatic_shutdown_on_expiration"
        ]
        payload["members"]["default_member_pin_policy"] = "optional"
        return AdminSettings.model_validate(payload)

    def _diff_settings(
        self,
        old_settings: AdminSettings,
        new_settings: AdminSettings,
    ) -> list[SettingsAuditChange]:
        old_payload = old_settings.model_dump(mode="json")
        new_payload = new_settings.model_dump(mode="json")
        changes: list[SettingsAuditChange] = []
        for category, old_values in old_payload.items():
            new_values = new_payload[category]
            changed_fields = [
                key
                for key, old_value in old_values.items()
                if new_values.get(key) != old_value
            ]
            if changed_fields:
                changes.append(
                    SettingsAuditChange(
                        category=category,
                        changed_fields=changed_fields,
                        old_values={key: old_values[key] for key in changed_fields},
                        new_values={key: new_values[key] for key in changed_fields},
                    )
                )
        return changes

    def _insert_audit_event(
        self,
        connection: Any,
        event_type: str,
        change: SettingsAuditChange,
        actor_user_id: str,
        created_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO admin_settings_audit_events (
                event_id,
                event_type,
                category,
                changed_fields_json,
                old_values_json,
                new_values_json,
                applied_immediately,
                performed_by_user_id,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                str(uuid4()),
                event_type,
                change.category,
                json.dumps(change.changed_fields, sort_keys=True),
                json.dumps(change.old_values, sort_keys=True),
                json.dumps(change.new_values, sort_keys=True),
                actor_user_id,
                created_at,
            ),
        )


def get_runtime_expiration_grace_seconds() -> int:
    return AdminSettingsService().get_settings().sessions.expiration_grace_seconds
