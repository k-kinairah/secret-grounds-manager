"""Stage 6 tests: Income by Group reporting on the Sales report.

Covers: build_sales_report's new income_groups/pending_attribution
fields, built entirely from each payment's immutable
income_group_id_snapshot/income_group_name_snapshot/attribution_status
(never the live pc_layouts assignment), the explicit "Legacy /
Unattributed" bucket for pre-migration historical rows, reconciliation
against the report's authoritative completed revenue, and that existing
report sections (operators/pcs/payment_methods) are unaffected.
"""

import os
import sys
import tempfile
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def create_env(database_path: Path):
    os.environ["SECRET_GROUNDS_DB_PATH"] = str(database_path)
    from server.database import initialize_database

    initialize_database()

    import server.main as main_module

    return main_module


def create_actor(main_module, username: str):
    from server.schemas import UserCreateRequest

    public = main_module.user_repository.create_user(
        UserCreateRequest(username=username, display_name=username, password=f"{username}-pass", role="cashier")
    )
    record = main_module.user_repository.get_user(public.user_id)
    assert record is not None
    return record


def pay_on_pc(main_module, session_id: str, pc_name: str, amount: Decimal, actor):
    from server.schemas import PaymentCreateRequest, SessionRecord
    from server.time_utils import current_utc_time

    now = current_utc_time()
    main_module.session_repository.create_session(
        SessionRecord(
            session_id=session_id,
            pc_name=pc_name,
            status="active",
            started_at=now,
            scheduled_end_at=now,
            original_seconds=3600,
            total_seconds=3600,
            created_at=now,
            base_amount=amount,
            total_amount=amount,
            session_mode="prepaid",
        )
    )
    return main_module.record_payment(
        session_id,
        PaymentCreateRequest(payment_method="cash", amount=amount, cash_received=amount),
        actor,
    )


def group_by_id(report, income_group_id):
    for group in report.income_groups:
        if group.income_group_id == income_group_id:
            return group
    return None


def group_by_name(report, name):
    for group in report.income_groups:
        if group.income_group_name == name:
            return group
    return None


def test_united_gene_mamalala_tyrone_aggregate_correctly(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "four-groups.sqlite3")
    actor = create_actor(main_module, "cashier.four")

    pay_on_pc(main_module, "session-united", "PC-01", Decimal("10.00"), actor)
    pay_on_pc(main_module, "session-gene", "PC-11", Decimal("20.00"), actor)
    pay_on_pc(main_module, "session-mamalala", "PC-13", Decimal("30.00"), actor)
    pay_on_pc(main_module, "session-tyrone", "PC-15", Decimal("40.00"), actor)

    report = main_module.build_sales_report("today", None, None)
    assert group_by_id(report, "united").amount == Decimal("10.00")
    assert group_by_id(report, "gene").amount == Decimal("20.00")
    assert group_by_id(report, "mama-lala").amount == Decimal("30.00")
    assert group_by_id(report, "tyrone").amount == Decimal("40.00")


def test_multiple_payments_in_one_group_sum_correctly(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "sum-one-group.sqlite3")
    actor = create_actor(main_module, "cashier.sum")

    pay_on_pc(main_module, "session-united-1", "PC-01", Decimal("10.00"), actor)
    pay_on_pc(main_module, "session-united-2", "PC-02", Decimal("15.00"), actor)
    pay_on_pc(main_module, "session-united-3", "PC-03", Decimal("5.00"), actor)

    report = main_module.build_sales_report("today", None, None)
    united = group_by_id(report, "united")
    assert united.amount == Decimal("30.00")
    assert united.payment_count == 3


def test_mixed_groups_remain_separate(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "mixed-separate.sqlite3")
    actor = create_actor(main_module, "cashier.mixed")

    pay_on_pc(main_module, "session-mix-united", "PC-01", Decimal("10.00"), actor)
    pay_on_pc(main_module, "session-mix-gene", "PC-11", Decimal("20.00"), actor)

    report = main_module.build_sales_report("today", None, None)
    assert group_by_id(report, "united").amount == Decimal("10.00")
    assert group_by_id(report, "gene").amount == Decimal("20.00")
    assert group_by_id(report, "mama-lala").amount == Decimal("0.00"), "seeded groups appear even at zero"
    assert group_by_id(report, "tyrone").amount == Decimal("0.00")


def test_pending_unknown_pc_payment_appears_in_pending_attribution(temp_directory: str) -> None:
    from server.schemas import MemberCreateRequest, MemberPurchaseRequest

    main_module = create_env(Path(temp_directory) / "pending-unknown-pc.sqlite3")
    actor = create_actor(main_module, "cashier.pendingunknown")
    member = main_module.member_repository.create_member(
        MemberCreateRequest(username="stage6.pending1", display_name="stage6.pending1", pin="1234", confirm_pin="1234"),
        None,
    )
    purchase = main_module.member_repository.purchase(
        member.member_id,
        MemberPurchaseRequest(product_id="member-promo", payment_method="cash", cash_received="1000.00"),
        actor,
    )
    assert purchase.payment.attribution_status == "pending"
    assert purchase.payment.pc_name_snapshot is None

    report = main_module.build_sales_report("today", None, None)
    assert report.pending_attribution_amount == Decimal("50.00")
    assert report.pending_attribution_count == 1
    for group in report.income_groups:
        assert group.income_group_id != "united" or group.amount == Decimal("0.00")


def test_pending_known_pc_unconfigured_group_appears_in_pending_attribution(temp_directory: str) -> None:
    from server.schemas import PaymentCreateRequest, PCLayoutUpdate, SessionRecord
    from server.time_utils import current_utc_time

    main_module = create_env(Path(temp_directory) / "pending-known-pc.sqlite3")
    actor = create_actor(main_module, "cashier.pendingknown")
    main_module.pc_layout_repository.upsert_layout(
        "PC-CUSTOM6",
        PCLayoutUpdate(display_name="PC-CUSTOM6", area_label="UNCONFIGURED", layout_order=999, is_vip=False, is_enabled=True),
    )
    now = current_utc_time()
    main_module.session_repository.create_session(
        SessionRecord(
            session_id="session-pending-known",
            pc_name="PC-CUSTOM6",
            status="active",
            started_at=now,
            scheduled_end_at=now,
            original_seconds=3600,
            total_seconds=3600,
            created_at=now,
            base_amount=Decimal("12.00"),
            total_amount=Decimal("12.00"),
            session_mode="prepaid",
        )
    )
    billing = main_module.record_payment(
        "session-pending-known",
        PaymentCreateRequest(payment_method="cash", amount=Decimal("12.00"), cash_received=Decimal("12.00")),
        actor,
    )
    assert billing.payments[0].attribution_status == "pending"
    assert billing.payments[0].pc_name_snapshot == "PC-CUSTOM6"

    report = main_module.build_sales_report("today", None, None)
    assert report.pending_attribution_amount == Decimal("12.00")
    assert report.pending_attribution_count == 1


def test_voided_pending_payment_does_not_count(temp_directory: str) -> None:
    from server.schemas import MemberCreateRequest, MemberPurchaseRefundRequest, MemberPurchaseRequest

    main_module = create_env(Path(temp_directory) / "voided-pending.sqlite3")
    actor = create_actor(main_module, "cashier.voidedpending")
    member = main_module.member_repository.create_member(
        MemberCreateRequest(username="stage6.voided", display_name="stage6.voided", pin="1234", confirm_pin="1234"),
        None,
    )
    purchase = main_module.member_repository.purchase(
        member.member_id,
        MemberPurchaseRequest(product_id="member-promo", payment_method="cash", cash_received="1000.00"),
        actor,
    )
    main_module.member_repository.refund_purchase(
        purchase.payment.payment_id, MemberPurchaseRefundRequest(reason="Customer cancellation"), actor
    )

    report = main_module.build_sales_report("today", None, None)
    assert report.pending_attribution_amount == Decimal("0.00")
    assert report.pending_attribution_count == 0


def test_assigned_voided_payment_follows_existing_semantics(temp_directory: str) -> None:
    from server.schemas import VoidPaymentRequest

    main_module = create_env(Path(temp_directory) / "voided-assigned.sqlite3")
    actor = create_actor(main_module, "cashier.voidedassigned")
    billing = pay_on_pc(main_module, "session-voided-assigned", "PC-01", Decimal("25.00"), actor)
    payment_id = billing.payments[0].payment_id
    assert billing.payments[0].income_group_id_snapshot == "united"

    main_module.void_payment(payment_id, VoidPaymentRequest(reason="Refund requested"), actor)

    report = main_module.build_sales_report("today", None, None)
    united = group_by_id(report, "united")
    assert united.amount == Decimal("0.00"), "voided payments are excluded from income_groups, matching pc_totals/operator_totals"


def test_pc_reassignment_does_not_rewrite_history_new_payment_uses_new_group(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "reassign-history.sqlite3")
    actor = create_actor(main_module, "cashier.reassign")

    pay_on_pc(main_module, "session-reassign-old", "PC-11", Decimal("15.00"), actor)

    with main_module.get_connection() as connection:
        connection.execute("UPDATE pc_layouts SET income_group_id = 'united' WHERE pc_name = 'PC-11'")

    pay_on_pc(main_module, "session-reassign-new", "PC-11", Decimal("20.00"), actor)

    report = main_module.build_sales_report("today", None, None)
    assert group_by_id(report, "gene").amount == Decimal("15.00"), "the pre-reassignment payment stays Gene"
    assert group_by_id(report, "united").amount == Decimal("20.00"), "the post-reassignment payment is United"


def test_custom_income_group_snapshot_is_not_dropped(temp_directory: str) -> None:
    from server.schemas import PCLayoutUpdate
    from server.time_utils import current_utc_time

    main_module = create_env(Path(temp_directory) / "custom-group.sqlite3")
    actor = create_actor(main_module, "cashier.custom")

    now = current_utc_time().isoformat()
    with main_module.get_connection() as connection:
        connection.execute(
            "INSERT INTO income_groups (income_group_id, name, is_active, created_at, updated_at) VALUES (?, ?, 1, ?, ?)",
            ("custom-owner", "Custom Owner", now, now),
        )
    main_module.pc_layout_repository.upsert_layout(
        "PC-CUSTOMOWNER",
        PCLayoutUpdate(
            display_name="PC-CUSTOMOWNER", area_label="CUSTOM", layout_order=998, is_vip=False, is_enabled=True, income_group_id="custom-owner"
        ),
    )
    pay_on_pc(main_module, "session-custom", "PC-CUSTOMOWNER", Decimal("33.00"), actor)

    report = main_module.build_sales_report("today", None, None)
    custom = group_by_id(report, "custom-owner")
    assert custom is not None, "a legitimate custom income group must never be dropped"
    assert custom.income_group_name == "Custom Owner"
    assert custom.amount == Decimal("33.00")


def test_historical_assigned_null_group_payment_is_represented_explicitly(temp_directory: str) -> None:
    from server.time_utils import current_utc_time

    main_module = create_env(Path(temp_directory) / "legacy-bucket.sqlite3")
    now = current_utc_time().isoformat()
    with main_module.get_connection() as connection:
        connection.execute(
            """
            INSERT INTO payments (
                payment_id, session_id, amount, payment_method, status, created_at,
                attribution_status, income_group_id_snapshot, income_group_name_snapshot
            )
            VALUES ('legacy-payment-1', NULL, '77.00', 'cash', 'completed', ?, 'assigned', NULL, NULL)
            """,
            (now,),
        )

    report = main_module.build_sales_report("today", None, None)
    legacy = group_by_name(report, "Legacy / Unattributed")
    assert legacy is not None, "a pre-migration assigned/null-group payment must never silently disappear"
    assert legacy.income_group_id is None
    assert legacy.amount == Decimal("77.00")


def test_income_group_buckets_reconcile_to_authoritative_report_revenue(temp_directory: str) -> None:
    from server.schemas import MemberCreateRequest, MemberPurchaseRequest
    from server.time_utils import current_utc_time

    main_module = create_env(Path(temp_directory) / "reconcile.sqlite3")
    actor = create_actor(main_module, "cashier.reconcile")

    pay_on_pc(main_module, "session-recon-united", "PC-01", Decimal("10.00"), actor)
    pay_on_pc(main_module, "session-recon-gene", "PC-11", Decimal("20.00"), actor)

    member = main_module.member_repository.create_member(
        MemberCreateRequest(username="stage6.reconcile", display_name="stage6.reconcile", pin="1234", confirm_pin="1234"),
        None,
    )
    main_module.member_repository.purchase(
        member.member_id,
        MemberPurchaseRequest(product_id="member-promo", payment_method="cash", cash_received="1000.00"),
        actor,
    )  # pending, no active session

    now = current_utc_time().isoformat()
    with main_module.get_connection() as connection:
        connection.execute(
            """
            INSERT INTO payments (
                payment_id, session_id, amount, payment_method, status, created_at,
                attribution_status, income_group_id_snapshot, income_group_name_snapshot
            )
            VALUES ('legacy-payment-recon', NULL, '5.00', 'cash', 'completed', ?, 'assigned', NULL, NULL)
            """,
            (now,),
        )

    report = main_module.build_sales_report("today", None, None)
    bucket_total = sum((group.amount for group in report.income_groups), Decimal("0.00"))
    reconciled = bucket_total + report.pending_attribution_amount
    assert reconciled == report.summary.total_sales
    assert reconciled == Decimal("10.00") + Decimal("20.00") + Decimal("50.00") + Decimal("5.00")


def test_date_filters_affect_income_group_totals_identically(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "date-filters.sqlite3")
    actor = create_actor(main_module, "cashier.datefilter")

    billing_today = pay_on_pc(main_module, "session-datefilter-today", "PC-01", Decimal("10.00"), actor)
    billing_old = pay_on_pc(main_module, "session-datefilter-old", "PC-11", Decimal("20.00"), actor)

    from server.time_utils import current_utc_time

    old_created_at = (current_utc_time() - timedelta(days=5)).isoformat()
    with main_module.get_connection() as connection:
        connection.execute(
            "UPDATE payments SET created_at = ? WHERE payment_id = ?",
            (old_created_at, billing_old.payments[0].payment_id),
        )

    today_report = main_module.build_sales_report("today", None, None)
    assert group_by_id(today_report, "united").amount == Decimal("10.00")
    assert group_by_id(today_report, "gene").amount == Decimal("0.00"), "the backdated payment must be excluded from 'today'"

    from datetime import timedelta as _timedelta

    # Custom-range dates are shop-local calendar days -- reuse the report's own
    # resolved local end date so this stays correct regardless of the UTC offset.
    wide_end = today_report.end_date
    wide_start = wide_end - _timedelta(days=10)
    wide_report = main_module.build_sales_report("custom", wide_start, wide_end)
    assert group_by_id(wide_report, "united").amount == Decimal("10.00")
    assert group_by_id(wide_report, "gene").amount == Decimal("20.00"), "a wider range must include the backdated payment"
    assert billing_today.payments[0].income_group_id_snapshot == "united"


def test_existing_operator_pc_and_method_totals_unaffected(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "existing-sections.sqlite3")
    actor = create_actor(main_module, "cashier.existing")

    pay_on_pc(main_module, "session-existing-1", "PC-01", Decimal("10.00"), actor)
    pay_on_pc(main_module, "session-existing-2", "PC-11", Decimal("20.00"), actor)

    report = main_module.build_sales_report("today", None, None)
    assert len(report.operators) == 1
    assert report.operators[0].total_amount == Decimal("30.00")
    pc_names = {pc.pc_name: pc.total_amount for pc in report.pcs}
    assert pc_names == {"PC-01": Decimal("10.00"), "PC-11": Decimal("20.00")}
    cash_method = next(m for m in report.payment_methods if m.payment_method == "cash")
    assert cash_method.amount == Decimal("30.00")
    assert report.summary.total_sales == Decimal("30.00")


def main() -> None:
    tests = [
        test_united_gene_mamalala_tyrone_aggregate_correctly,
        test_multiple_payments_in_one_group_sum_correctly,
        test_mixed_groups_remain_separate,
        test_pending_unknown_pc_payment_appears_in_pending_attribution,
        test_pending_known_pc_unconfigured_group_appears_in_pending_attribution,
        test_voided_pending_payment_does_not_count,
        test_assigned_voided_payment_follows_existing_semantics,
        test_pc_reassignment_does_not_rewrite_history_new_payment_uses_new_group,
        test_custom_income_group_snapshot_is_not_dropped,
        test_historical_assigned_null_group_payment_is_represented_explicitly,
        test_income_group_buckets_reconcile_to_authoritative_report_revenue,
        test_date_filters_affect_income_group_totals_identically,
        test_existing_operator_pc_and_method_totals_unaffected,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
            test(temp_directory)
    print("Stage 6 income-by-group sales report tests passed.")


if __name__ == "__main__":
    main()
