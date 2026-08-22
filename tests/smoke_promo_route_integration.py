"""Stage 3B smoke tests: the promo path wired through the REAL
server.main routes and the REAL session_service.start_prepaid_session /
add_prepaid_time -- not the standalone modules directly (those are covered
by tests/smoke_promo_session_integration.py). Proves command/recovery
behavior, expected_promo_id, idempotency, and the new quote route all work
through the actual production call path.
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def create_env(database_path: Path):
    os.environ["SECRET_GROUNDS_DB_PATH"] = str(database_path)
    from server.database import initialize_database

    initialize_database()

    import server.main as main_module
    from server.schemas import PCLayoutUpdate, PCRegistration

    main_module.pc_registry.clear()
    main_module.register_pc(PCRegistration(pc_name="PC-PROMO", ip_address="127.0.0.1"))
    main_module.register_pc(PCRegistration(pc_name="PC-PROMO-VIP", ip_address="127.0.0.1"))
    main_module.pc_layout_repository.upsert_layout(
        "PC-PROMO-VIP",
        PCLayoutUpdate(display_name="VIP", area_label="TEST", layout_order=1, is_vip=True, is_enabled=True),
    )

    main_module.session_service.require_available_client = lambda pc_name: main_module.pc_registry[pc_name]

    async def acknowledge_command(*_: object) -> list[object]:
        return []

    main_module.session_service.send_required_command = acknowledge_command
    return main_module


def make_standard_promo(main_module, **overrides):
    from server.schemas import PromoCreate, PromoStateTransition

    now = datetime.now(timezone.utc)
    fields = {
        "name": "Route Test Standard Promo",
        "audience": "standard",
        "amount_mode": "exact",
        "price": Decimal("20.00"),
        "granted_seconds": 6000,  # normal P20 = 4800s
        "station_scope": "standard",
        "applies_to": "start_session",
        "starts_at": now - timedelta(hours=1),
        "ends_at": now + timedelta(days=7),
    }
    fields.update(overrides)
    promo = main_module.session_service.promo_repository.create(PromoCreate(**fields))
    main_module.session_service.promo_repository.transition_state(
        promo.promo_id, PromoStateTransition(owner_state="enabled")
    )
    return promo


def test_start_prepaid_session_applies_promo_through_real_service(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "start-real.sqlite3")
    from server.schemas import PaymentCreateRequest

    promo = make_standard_promo(main_module)

    response = asyncio.run(
        main_module.session_service.start_prepaid_session(
            "PC-PROMO",
            PaymentCreateRequest(payment_method="cash", amount=Decimal("20.00"), cash_received=Decimal("20.00")),
            amount=Decimal("20.00"),
        )
    )
    assert response.session.total_seconds == 6000, "the promo's granted seconds must win over the normal P20=4800s"
    assert response.command is not None

    from server.database import get_connection

    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM promo_redemptions WHERE session_id = ?", (response.session.session_id,)
        ).fetchone()
    assert row is not None
    assert row["promo_id"] == promo.promo_id


def test_add_prepaid_time_respects_applies_to_through_real_service(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "add-time-real.sqlite3")
    from server.schemas import PaymentCreateRequest

    # start_session-only promo must NOT apply during add-time.
    make_standard_promo(main_module, applies_to="start_session")

    start_response = asyncio.run(
        main_module.session_service.start_prepaid_session(
            "PC-PROMO",
            PaymentCreateRequest(payment_method="cash", amount=Decimal("15.00"), cash_received=Decimal("15.00")),
            amount=Decimal("15.00"),
        )
    )
    assert start_response.session.total_seconds == 3600  # normal P15 = 1hr, no promo (wrong amount)

    add_response = asyncio.run(
        main_module.session_service.add_prepaid_time(
            start_response.session.session_id,
            PaymentCreateRequest(payment_method="cash", amount=Decimal("20.00"), cash_received=Decimal("20.00")),
            amount=Decimal("20.00"),
        )
    )
    assert add_response.session.total_seconds == 3600 + 4800, "start_session-only promo must not apply to add_time"

    from server.database import get_connection

    with get_connection() as connection:
        count = connection.execute("SELECT COUNT(*) FROM promo_redemptions").fetchone()[0]
    assert count == 0


def test_add_time_promo_applies_and_is_atomic_through_real_service(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "add-time-promo-real.sqlite3")
    from server.schemas import PaymentCreateRequest

    make_standard_promo(main_module, applies_to="add_time", granted_seconds=7200)  # beats normal P20=4800s

    start_response = asyncio.run(
        main_module.session_service.start_prepaid_session(
            "PC-PROMO",
            PaymentCreateRequest(payment_method="cash", amount=Decimal("15.00"), cash_received=Decimal("15.00")),
            amount=Decimal("15.00"),
        )
    )
    add_response = asyncio.run(
        main_module.session_service.add_prepaid_time(
            start_response.session.session_id,
            PaymentCreateRequest(payment_method="cash", amount=Decimal("20.00"), cash_received=Decimal("20.00")),
            amount=Decimal("20.00"),
        )
    )
    assert add_response.session.total_seconds == 3600 + 7200
    assert add_response.session.added_seconds == 7200


def test_expected_promo_id_conflict_returns_409_with_no_side_effects(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "expected-conflict.sqlite3")
    from server.schemas import PaymentCreateRequest

    try:
        asyncio.run(
            main_module.session_service.start_prepaid_session(
                "PC-PROMO",
                PaymentCreateRequest(payment_method="cash", amount=Decimal("20.00"), cash_received=Decimal("20.00")),
                amount=Decimal("20.00"),
                expected_promo_id="a-promo-id-that-does-not-exist",
            )
        )
        raise AssertionError("expected 409 for a stale/invalid expected_promo_id")
    except HTTPException as error:
        assert error.status_code == 409

    from server.database import get_connection

    with get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 0


def test_expected_promo_id_matches_current_quote_succeeds(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "expected-match.sqlite3")
    from server.schemas import PaymentCreateRequest

    promo = make_standard_promo(main_module)

    response = asyncio.run(
        main_module.session_service.start_prepaid_session(
            "PC-PROMO",
            PaymentCreateRequest(payment_method="cash", amount=Decimal("20.00"), cash_received=Decimal("20.00")),
            amount=Decimal("20.00"),
            expected_promo_id=promo.promo_id,
        )
    )
    assert response.session.total_seconds == 6000


def test_idempotent_retry_through_real_service_does_not_double_add(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "idempotent-real.sqlite3")
    from server.schemas import PaymentCreateRequest

    make_standard_promo(main_module)
    key = "route-test-idempotency-key-001"

    first = asyncio.run(
        main_module.session_service.start_prepaid_session(
            "PC-PROMO",
            PaymentCreateRequest(
                payment_method="cash", amount=Decimal("20.00"), cash_received=Decimal("20.00"),
                idempotency_key=key,
            ),
            amount=Decimal("20.00"),
        )
    )
    second = asyncio.run(
        main_module.session_service.start_prepaid_session(
            "PC-PROMO",
            PaymentCreateRequest(
                payment_method="cash", amount=Decimal("20.00"), cash_received=Decimal("20.00"),
                idempotency_key=key,
            ),
            amount=Decimal("20.00"),
        )
    )
    assert second.session.session_id == first.session.session_id
    assert second.command is None, "a retried idempotent request sends no new command"

    from server.database import get_connection

    with get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM promo_redemptions").fetchone()[0] == 1


def test_command_failure_still_marks_review_required(temp_directory: str) -> None:
    """Preserves the existing command/recovery behavior for the promo path
    too: a command failure after a successful atomic write marks the
    payment for review rather than silently losing the discrepancy."""
    main_module = create_env(Path(temp_directory) / "command-fails.sqlite3")
    from server.schemas import PaymentCreateRequest

    make_standard_promo(main_module)

    async def failing_command(*_: object) -> list[object]:
        raise RuntimeError("simulated PC command failure")

    main_module.session_service.send_required_command = failing_command

    try:
        asyncio.run(
            main_module.session_service.start_prepaid_session(
                "PC-PROMO",
                PaymentCreateRequest(payment_method="cash", amount=Decimal("20.00"), cash_received=Decimal("20.00")),
                amount=Decimal("20.00"),
            )
        )
        raise AssertionError("expected a 502 for the simulated command failure")
    except HTTPException as error:
        assert error.status_code == 502

    from server.database import get_connection

    with get_connection() as connection:
        payment_row = connection.execute(
            "SELECT review_required FROM payments"
        ).fetchone()
        session_row = connection.execute("SELECT status FROM sessions").fetchone()
    assert payment_row["review_required"] == 1
    assert session_row["status"] == "failed", (
        "the promo path must fall into the exact same command-failure recovery branch "
        "as the existing non-promo start flow: payment/redemption already committed, "
        "session then marked failed for manual review"
    )


def test_promo_quote_route_through_real_route_function(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "quote-route.sqlite3")
    from server.schemas import PromoQuoteRequest

    promo = make_standard_promo(main_module)

    class FakeUser:
        user_id = "owner-1"
        username = "owner"
        role = "owner"

    response = main_module.quote_standard_or_vip_promo(
        PromoQuoteRequest(pc_name="PC-PROMO", action="start_session", amount=Decimal("20.00")),
        FakeUser(),
    )
    assert response.source_type == "promo"
    assert response.promo_id == promo.promo_id
    assert response.final_seconds == 6000
    assert response.base_seconds == 4800
    assert response.benefit_seconds == 1200


def test_promo_quote_route_no_promo_result(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "quote-route-none.sqlite3")
    from server.schemas import PromoQuoteRequest

    class FakeUser:
        user_id = "owner-1"
        username = "owner"
        role = "owner"

    response = main_module.quote_standard_or_vip_promo(
        PromoQuoteRequest(pc_name="PC-PROMO", action="start_session", amount=Decimal("15.00")),
        FakeUser(),
    )
    assert response.source_type == "normal_rate"
    assert response.promo_id is None
    assert response.final_seconds == 3600


def test_get_rates_remains_permanent_rate_only(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "get-rates.sqlite3")

    make_standard_promo(main_module, name="Should Never Appear In GET /rates")

    class FakeUser:
        user_id = "owner-1"
        username = "owner"
        role = "owner"

    rates = main_module.get_rates(FakeUser())
    names = [rate.name for rate in rates]
    assert "Should Never Appear In GET /rates" not in names
    assert all(hasattr(rate, "rate_type") for rate in rates), "GET /rates still only returns permanent rate_profiles rows"


def test_non_promo_amount_start_matches_existing_behavior(temp_directory: str) -> None:
    """No promo configured at all -- start_prepaid_session must behave
    exactly as before Stage 3B."""
    main_module = create_env(Path(temp_directory) / "no-promo-baseline.sqlite3")
    from server.schemas import PaymentCreateRequest

    response = asyncio.run(
        main_module.session_service.start_prepaid_session(
            "PC-PROMO",
            PaymentCreateRequest(payment_method="cash", amount=Decimal("20.00"), cash_received=Decimal("20.00")),
            amount=Decimal("20.00"),
        )
    )
    assert response.session.total_seconds == 4800
    assert response.command is not None

    from server.database import get_connection

    with get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM promo_redemptions").fetchone()[0] == 0


def main() -> None:
    tests = [
        test_start_prepaid_session_applies_promo_through_real_service,
        test_add_prepaid_time_respects_applies_to_through_real_service,
        test_add_time_promo_applies_and_is_atomic_through_real_service,
        test_expected_promo_id_conflict_returns_409_with_no_side_effects,
        test_expected_promo_id_matches_current_quote_succeeds,
        test_idempotent_retry_through_real_service_does_not_double_add,
        test_command_failure_still_marks_review_required,
        test_promo_quote_route_through_real_route_function,
        test_promo_quote_route_no_promo_result,
        test_get_rates_remains_permanent_rate_only,
        test_non_promo_amount_start_matches_existing_behavior,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
            test(temp_directory)
        print(f"{test.__name__} passed.")
    print("Promo Maker Stage 3B (real route/service integration) smoke tests passed.")


if __name__ == "__main__":
    main()
