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
        assert [rate.name for rate in rates if rate.rate_type == "package"] == [
            "30 Minutes",
            "1 Hour",
            "2 Hours",
            "Overnight",
        ]
        metered = get_metered_rate()
        assert metered.name == "Regular Rate"
        assert metered.price == Decimal("1.00")
        assert metered.duration_seconds == 300

        calculated = calculate_time(CalculateTimeRequest(amount=Decimal("20.00")))
        assert calculated.duration_seconds == 6000
        assert calculated.rate_name == "Regular Rate"

        fractional_rate = update_rate(
            metered.rate_id,
            RateProfileUpdate(
                name="Regular Rate",
                duration_seconds=300,
                price=Decimal("2.00"),
                rate_type="metered",
                sort_order=0,
            ),
        )
        assert fractional_rate.rate_type == "metered"
        fractional = calculate_time(CalculateTimeRequest(amount=Decimal("0.01")))
        assert fractional.duration_seconds == 1

        update_rate(
            metered.rate_id,
            RateProfileUpdate(
                name="Regular Rate",
                duration_seconds=300,
                price=Decimal("1.00"),
                rate_type="metered",
                sort_order=0,
            ),
        )

        one_hour = next(rate for rate in rates if rate.name == "1 Hour")
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

        added_metered = asyncio.run(
            session_service.add_time(session.session_id, amount=Decimal("5.00"))
        )
        assert added_metered.session.additional_amount == Decimal("5.00")
        assert added_metered.session.total_amount == Decimal("105.10")
        assert added_metered.session.added_seconds == 1500

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

        thirty = next(rate for rate in get_rates() if rate.name == "30 Minutes")
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
        assert added.session.additional_amount == Decimal("55.05")
        assert added.session.total_amount == Decimal("155.15")

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

        second_metered = create_rate(
            RateProfileCreate(
                name="Second Metered",
                duration_seconds=60,
                price=Decimal("1.00"),
                rate_type="metered",
                sort_order=1,
            )
        )
        assert second_metered.is_active is True
        assert get_metered_rate().rate_id == second_metered.rate_id
        original_metered = next(
            rate
            for rate in get_rates(include_inactive=True)
            if rate.rate_id == metered.rate_id
        )
        assert original_metered.is_active is False

        pc_registry.clear()
        register_pc(PCRegistration(pc_name="PC-AMOUNT", ip_address="127.0.0.1"))
        session_service.require_available_client = lambda _: pc_registry["PC-AMOUNT"]
        amount_started = asyncio.run(
            session_service.start_session("PC-AMOUNT", amount=Decimal("20.00"))
        )
        assert amount_started.session.base_amount == Decimal("20.00")
        assert amount_started.session.original_seconds == 1200

        print("Rate profile smoke test passed.")


if __name__ == "__main__":
    main()
