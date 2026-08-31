import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(Path(temp_directory) / "settings.sqlite3")
        os.environ["SECRET_GROUNDS_TEST_MODE"] = "true"
        os.environ["SECRET_GROUNDS_AUTOMATIC_SHUTDOWN_ON_EXPIRATION"] = "false"

        from client.remote_controls import default_power_executor
        from server.admin_settings_service import AdminSettingsService
        from server.auth import authenticate_admin_operator
        from server.database import get_connection, initialize_database
        from server.main import get_admin_settings, require_admin_operator, reset_admin_settings, update_admin_settings
        from server.schemas import AdminSettingsResetRequest, AdminSettingsUpdateRequest, SessionRecord
        from server.session_repository import SessionRepository
        from server.session_service import SessionService
        from server.time_utils import current_utc_time
        from server.auth import user_repository

        initialize_database()
        service = AdminSettingsService()
        defaults = service.get_settings()
        assert defaults.sessions.expiration_grace_seconds == 120
        # power_command_safety_status is server-generated and lives only in the
        # read-only Protected Configuration map (the Wake & Power category was
        # removed); it is always TEST_MODE-derived and never client-writable.
        assert service.get_response().read_only["power_command_safety_status"].startswith("Test mode")

        mama = user_repository.get_user("operator_mama")
        admin = user_repository.get_by_username("admin")
        assert mama is not None and admin is not None
        try:
            require_admin_operator(mama)
        except Exception as error:
            assert getattr(error, "status_code", None) == 403
        else:
            raise AssertionError("Family operator must not pass Settings authorization")
        try:
            authenticate_admin_operator("wrong-password")
        except Exception as error:
            assert getattr(error, "status_code", None) == 401
        else:
            raise AssertionError("Wrong Admin password should be rejected")
        assert authenticate_admin_operator("admin12345").user_id == admin.user_id
        assert get_admin_settings(admin).settings.general.shop_display_name == "Secret Grounds"

        settings = service.get_settings().model_dump(mode="json")
        settings["general"]["shop_display_name"] = "Secret Grounds Test"
        settings["sessions"]["expiration_grace_seconds"] = 180
        settings["sessions"]["reminder_thresholds_seconds"] = [600, 120, 30]
        settings["customer_client"]["customer_branding_text"] = "Ask the cashier for more time."
        updated = update_admin_settings(AdminSettingsUpdateRequest.model_validate(settings), admin)
        updated_settings = updated.settings.model_dump(mode="json")
        assert updated_settings["general"]["shop_display_name"] == "Secret Grounds Test"
        assert updated_settings["sessions"]["expiration_grace_seconds"] == 180
        # The retired Wake & Power category is not persisted, and the
        # server-generated safety status stays TEST_MODE-derived.
        assert "wake_power" not in updated_settings
        assert updated.read_only["power_command_safety_status"].startswith("Test mode")

        invalid_grace = dict(updated_settings)
        invalid_grace["sessions"] = dict(updated_settings["sessions"])
        invalid_grace["sessions"]["expiration_grace_seconds"] = 10
        try:
            AdminSettingsUpdateRequest.model_validate(invalid_grace)
        except Exception:
            pass
        else:
            raise AssertionError("Invalid grace period should be rejected")

        invalid_thresholds = dict(updated_settings)
        invalid_thresholds["sessions"] = dict(updated_settings["sessions"])
        invalid_thresholds["sessions"]["reminder_thresholds_seconds"] = [60, 300]
        try:
            AdminSettingsUpdateRequest.model_validate(invalid_thresholds)
        except Exception:
            pass
        else:
            raise AssertionError("Invalid reminder ordering should be rejected")

        unknown_setting = dict(updated_settings)
        unknown_setting["unsafe_shell_command"] = "shutdown now"
        try:
            AdminSettingsUpdateRequest.model_validate(unknown_setting)
        except Exception:
            pass
        else:
            raise AssertionError("Unknown settings must be rejected")

        # timezone_display must be a real IANA zone -- an invalid one is
        # rejected (422) rather than silently falling back at runtime.
        for bad_timezone in ("Not/AZone", "Manila", "GMT+8", "   "):
            invalid_timezone = dict(updated_settings)
            invalid_timezone["general"] = dict(updated_settings["general"])
            invalid_timezone["general"]["timezone_display"] = bad_timezone
            try:
                AdminSettingsUpdateRequest.model_validate(invalid_timezone)
            except Exception:
                pass
            else:
                raise AssertionError(
                    f"Invalid timezone {bad_timezone!r} should be rejected"
                )

        # A valid, non-default IANA zone is accepted and persisted, and the
        # runtime shop-timezone source reflects it.
        from server.admin_settings_service import get_runtime_shop_timezone_display

        valid_timezone = dict(updated_settings)
        valid_timezone["general"] = dict(updated_settings["general"])
        valid_timezone["general"]["timezone_display"] = "Asia/Tokyo"
        tz_updated = update_admin_settings(
            AdminSettingsUpdateRequest.model_validate(valid_timezone), admin
        )
        assert tz_updated.settings.general.timezone_display == "Asia/Tokyo"
        assert get_runtime_shop_timezone_display() == "Asia/Tokyo"
        # Restore the default so later assertions in this test are unaffected.
        restore_timezone = valid_timezone
        restore_timezone["general"]["timezone_display"] = "Asia/Manila"
        update_admin_settings(
            AdminSettingsUpdateRequest.model_validate(restore_timezone), admin
        )
        assert get_runtime_shop_timezone_display() == "Asia/Manila"

        safe_payload = service.get_client_settings().model_dump(mode="json")
        assert safe_payload["shop_display_name"] == "Secret Grounds Test"
        assert "power_command_safety_status" not in safe_payload
        assert "test_mode" not in safe_payload

        reset = reset_admin_settings(AdminSettingsResetRequest(category="general"), admin)
        assert reset.settings.general.shop_display_name == "Secret Grounds"

        persisted = AdminSettingsService().get_settings()
        assert persisted.sessions.expiration_grace_seconds == 180

        repository = SessionRepository()
        session_service = SessionService(
            repository=repository,
            rate_repository=None,  # type: ignore[arg-type]
            connection_manager=None,  # type: ignore[arg-type]
            pc_registry={},
            command_timeout_seconds=1,
        )
        now = current_utc_time()
        active = SessionRecord(
            session_id="settings-grace-new",
            pc_name="PC-SETTINGS",
            status="active",
            started_at=now - timedelta(minutes=1),
            scheduled_end_at=now,
            original_seconds=60,
            total_seconds=60,
            created_at=now - timedelta(minutes=1),
        )
        repository.create_session(active)
        grace = session_service.begin_expiration_grace(active.session_id, now)
        assert grace is not None and grace.shutdown_at is not None
        assert int((grace.shutdown_at - now).total_seconds()) == 180
        old_shutdown_at = grace.shutdown_at

        current_settings = service.get_settings().model_dump(mode="json")
        current_settings["sessions"]["expiration_grace_seconds"] = 240
        service.update_settings(
            service.get_settings().__class__.model_validate(current_settings),  # type: ignore[arg-type]
            "operator_admin",
        )
        same_grace = session_service.begin_expiration_grace(active.session_id, now + timedelta(seconds=1))
        assert same_grace is not None
        assert same_grace.shutdown_at == old_shutdown_at

        with get_connection() as connection:
            audit_count = connection.execute(
                "SELECT COUNT(*) FROM admin_settings_audit_events WHERE event_type = 'settings_updated'"
            ).fetchone()[0]
            audit_blob = connection.execute(
                "SELECT old_values_json || new_values_json FROM admin_settings_audit_events LIMIT 1"
            ).fetchone()[0]
        assert audit_count >= 1
        assert "password" not in audit_blob.lower()
        assert "password" not in audit_blob.lower()
        assert "token" not in audit_blob.lower()
        assert "cookie" not in audit_blob.lower()

        try:
            default_power_executor(["shutdown", "/s", "/t", "0"])
        except RuntimeError as error:
            assert "blocked" in str(error)
        else:
            raise AssertionError("Settings tests must never reach the real power executor")

        print("Admin settings smoke test passed.")


if __name__ == "__main__":
    main()
