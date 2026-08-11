import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(Path(temp_directory) / "rates.sqlite3")

        from server.database import initialize_database
        from server.main import (
            app,
            calculate_time,
            create_rate,
            get_metered_rate,
            get_rates,
            pc_registry,
            register_pc,
            session_repository,
            session_service,
            update_rate,
            update_rate_status,
        )
        from server.schemas import (
            PCRegistration,
            RateProfileCreate,
            RateProfileUpdate,
            RateStatusUpdate,
            CalculateTimeRequest,
            SessionRecord,
        )
        from server.time_utils import current_utc_time

        initialize_database()
        cors_middleware = next(
            middleware
            for middleware in app.user_middleware
            if middleware.cls.__name__ == "CORSMiddleware"
        )
        assert {"GET", "POST", "PUT", "PATCH"}.issubset(
            set(cors_middleware.kwargs["allow_methods"])
        )

        rates = get_rates()
        # A fresh database gets exactly the five owner-approved default
        # packages -- the legacy seed_default_rates() placeholders ("30
        # Minutes"/"1 Hour"/"2 Hours"/"Overnight") have been removed and
        # must never appear here (see deactivate_untouched_legacy_default_rates
        # in server/database.py for how a database that already has one of
        # those rows from before this cleanup is handled instead).
        assert [rate.name for rate in rates if rate.rate_type == "package"] == [
            "Standard Guest - P5 (20 Minutes)",
            "Standard Guest - P10 (40 Minutes)",
            "Standard Guest - P15 (1 Hour)",
            "Standard Midnight Promo",
            "VIP Guest - P30 (1 Hour)",
        ]

        # Metered/Open Time customer operation is permanently retired.
        try:
            get_metered_rate()
        except HTTPException as error:
            assert error.status_code == 410
        else:
            raise AssertionError("GET /rates/metered must be retired")

        try:
            calculate_time(CalculateTimeRequest(amount=Decimal("20.00")))
        except HTTPException as error:
            assert error.status_code == 410
        else:
            raise AssertionError("POST /rates/calculate-time must be retired")

        # The pre-existing legacy metered rate row must still exist (untouched
        # historical data) even though it's no longer reachable for active use.
        legacy_metered = next(
            rate for rate in get_rates(include_inactive=True) if rate.rate_type == "metered"
        )
        assert legacy_metered.name == "Regular Rate"
        assert legacy_metered.price == Decimal("1.00")
        assert legacy_metered.duration_seconds == 300
        assert legacy_metered.is_active is True

        # Creating a brand new metered rate is rejected, active or not.
        for is_active in (True, False):
            try:
                create_rate(
                    RateProfileCreate(
                        name="New Metered",
                        duration_seconds=60,
                        price=Decimal("1.00"),
                        rate_type="metered",
                        sort_order=1,
                        is_active=is_active,
                    )
                )
            except HTTPException as error:
                assert error.status_code == 410
            else:
                raise AssertionError("Creating a new metered rate must be rejected")

        # Editing the existing metered rate (even just its name/price) is
        # rejected -- it is permanently frozen once retired.
        try:
            update_rate(
                legacy_metered.rate_id,
                RateProfileUpdate(
                    name="Regular Rate",
                    duration_seconds=300,
                    price=Decimal("2.00"),
                    rate_type="metered",
                    sort_order=0,
                ),
            )
        except HTTPException as error:
            assert error.status_code == 410
        else:
            raise AssertionError("Editing an existing metered rate must be rejected")

        # Deactivating the legacy metered rate is still allowed...
        deactivated = update_rate_status(legacy_metered.rate_id, RateStatusUpdate(is_active=False))
        assert deactivated.is_active is False

        # ...but reactivating it is rejected.
        try:
            update_rate_status(legacy_metered.rate_id, RateStatusUpdate(is_active=True))
        except HTTPException as error:
            assert error.status_code == 410
        else:
            raise AssertionError("Reactivating an existing metered rate must be rejected")
        still_inactive = next(
            rate for rate in get_rates(include_inactive=True) if rate.rate_id == legacy_metered.rate_id
        )
        assert still_inactive.is_active is False

        # Package rate creation/edit remains fully unaffected.
        one_hour = next(rate for rate in rates if rate.name == "Standard Guest - P15 (1 Hour)")
        updated_one_hour = update_rate(
            one_hour.rate_id,
            RateProfileUpdate(
                name="1 Hour",
                duration_seconds=3600,
                price=Decimal("100.10"),
                rate_type="package",
                sort_order=20,
            ),
        )
        assert updated_one_hour.price == Decimal("100.10")

        pc_registry.clear()
        register_pc(PCRegistration(pc_name="PC-RATE", ip_address="127.0.0.1"))
        session_service.require_available_client = lambda _: pc_registry["PC-RATE"]
        async def acknowledge_command(*_: object) -> list[object]:
            return []

        session_service.send_required_command = acknowledge_command

        import asyncio

        started = asyncio.run(
            session_service.start_session("PC-RATE", rate_id=one_hour.rate_id)
        )
        session = started.session
        assert session.rate_id == one_hour.rate_id
        assert session.rate_name_snapshot == "1 Hour"
        assert session.base_amount == Decimal("100.10")
        assert session.total_amount == Decimal("100.10")

        # Enter Amount (amount-based session start/add-time) is retired too,
        # since it relied on the now-retired active metered rate as a
        # money-to-time conversion table.
        try:
            asyncio.run(session_service.add_time(session.session_id, amount=Decimal("5.00")))
        except HTTPException as error:
            assert error.status_code == 410
        else:
            raise AssertionError("Amount-based add-time must be rejected")

        changed_rate = update_rate(
            one_hour.rate_id,
            RateProfileUpdate(
                name="Edited 1 Hour",
                duration_seconds=3600,
                price=Decimal("200.20"),
                rate_type="package",
                sort_order=20,
            ),
        )
        assert changed_rate.name == "Edited 1 Hour"
        saved_session = session_repository.get_session(session.session_id)
        assert saved_session is not None
        assert saved_session.rate_name_snapshot == "1 Hour"
        assert saved_session.base_amount == Decimal("100.10")

        thirty = next(rate for rate in get_rates() if rate.name == "Standard Guest - P5 (20 Minutes)")
        update_rate(
            thirty.rate_id,
            RateProfileUpdate(
                name="30 Minutes",
                duration_seconds=1800,
                price=Decimal("50.05"),
                rate_type="package",
                sort_order=10,
            ),
        )
        added = asyncio.run(session_service.add_time(session.session_id, rate_id=thirty.rate_id))
        assert added.session.additional_amount == Decimal("50.05")
        assert added.session.total_amount == Decimal("150.15")

        update_rate_status(thirty.rate_id, RateStatusUpdate(is_active=False))
        try:
            asyncio.run(session_service.add_time(session.session_id, rate_id=thirty.rate_id))
        except HTTPException as error:
            assert error.status_code == 409
        else:
            raise AssertionError("Inactive rate should be rejected")

        now = current_utc_time()
        session_repository.create_session(
            SessionRecord(
                session_id="custom-session",
                pc_name="PC-CUSTOM",
                status="active",
                started_at=now,
                scheduled_end_at=now,
                ended_at=None,
                original_seconds=60,
                added_seconds=0,
                total_seconds=60,
                amount_paid=None,
                ended_reason=None,
                created_at=now,
            )
        )
        custom = session_repository.get_session("custom-session")
        assert custom is not None
        assert custom.base_amount == Decimal("0.00")

        created = create_rate(
            RateProfileCreate(
                name="Test Rate",
                duration_seconds=120,
                price=Decimal("12.34"),
                rate_type="package",
                sort_order=99,
            )
        )
        assert created.price == Decimal("12.34")

        # Starting a brand new session via Enter Amount is rejected too.
        pc_registry.clear()
        register_pc(PCRegistration(pc_name="PC-AMOUNT", ip_address="127.0.0.1"))
        session_service.require_available_client = lambda _: pc_registry["PC-AMOUNT"]
        try:
            asyncio.run(session_service.start_session("PC-AMOUNT", amount=Decimal("20.00")))
        except HTTPException as error:
            assert error.status_code == 410
        else:
            raise AssertionError("Amount-based session start must be rejected")
        assert session_repository.get_active_session_for_pc("PC-AMOUNT") is None

        print("Rate profile smoke test passed.")


if __name__ == "__main__":
    main()
