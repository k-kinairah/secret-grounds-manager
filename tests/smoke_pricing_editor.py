"""Phase 1 curated pricing editor -- isolated smoke test.

Runs entirely against a throwaway temp-directory SQLite database
(SECRET_GROUNDS_DB_PATH). It never opens, reads, or writes the real
data/secret_grounds.db.
"""

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
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(Path(temp_directory) / "pricing.sqlite3")

        from server.database import get_connection, initialize_database
        from server import pricing_service
        from server.schemas import PricingMoneyTime, PricingUpdateRequest

        initialize_database()

        # --- baseline snapshot reflects the seeded owner pricing -----------
        snap = pricing_service.get_editable_pricing()
        assert snap.standard_guest.price == Decimal("5.00"), snap.standard_guest
        assert snap.standard_guest.seconds == 1200
        assert snap.standard_member_new_account.price == Decimal("100.00")
        assert snap.standard_member_new_account.seconds == 6 * 3600
        assert snap.standard_member_topup_small.price == Decimal("50.00")
        assert snap.standard_member_topup_small.seconds == 4 * 3600
        assert snap.standard_member_topup_large.price == Decimal("100.00")
        assert snap.standard_member_topup_large.seconds == 8 * 3600
        assert snap.vip_guest_hourly.price == Decimal("30.00")
        assert snap.vip_guest_hourly.seconds == 3600
        assert snap.promo.price == Decimal("250.00")
        assert snap.promo.seconds == 10 * 3600
        assert [t.multiple for t in snap.vip_guest_tiers] == [2, 3, 4]
        assert [(t.price, t.seconds) for t in snap.vip_guest_tiers] == [
            (Decimal("60.00"), 7200),
            (Decimal("90.00"), 10800),
            (Decimal("120.00"), 14400),
        ]
        assert snap.vip_member_conversion_ratio == 2

        with get_connection() as connection:
            audit_before = connection.execute("SELECT COUNT(*) FROM rate_audit_events").fetchone()[0]

        # --- apply an edit ------------------------------------------------
        request = PricingUpdateRequest(
            standard_guest=PricingMoneyTime(price=Decimal("6.00"), seconds=1200),
            standard_member_new_account=PricingMoneyTime(price=Decimal("120.00"), seconds=7 * 3600),
            standard_member_topup_small=PricingMoneyTime(price=Decimal("60.00"), seconds=5 * 3600),
            standard_member_topup_large=PricingMoneyTime(price=Decimal("110.00"), seconds=9 * 3600),
            vip_guest_hourly=PricingMoneyTime(price=Decimal("35.00"), seconds=3600),
            promo=PricingMoneyTime(price=Decimal("240.00"), seconds=10 * 3600),
        )
        updated = pricing_service.apply_pricing_update(request, actor_user_id="tester")

        assert updated.standard_guest.price == Decimal("6.00")
        assert updated.standard_member_new_account.price == Decimal("120.00")
        assert updated.standard_member_new_account.seconds == 7 * 3600
        assert updated.standard_member_topup_small.price == Decimal("60.00")
        assert updated.standard_member_topup_large.seconds == 9 * 3600
        assert updated.vip_guest_hourly.price == Decimal("35.00")
        assert updated.promo.price == Decimal("240.00")

        # VIP tiers regenerated as exact x2 / x3 / x4 of the new base.
        assert [(t.price, t.seconds) for t in updated.vip_guest_tiers] == [
            (Decimal("70.00"), 7200),
            (Decimal("105.00"), 10800),
            (Decimal("140.00"), 14400),
        ]

        # Promo mirrored: guest package row and member product carry the same
        # (price, seconds).
        with get_connection() as connection:
            promo_pkg = connection.execute(
                "SELECT price, duration_seconds FROM rate_profiles WHERE rate_id = 'rate-vip-promo-10hr'"
            ).fetchone()
            promo_product = connection.execute(
                "SELECT price, time_seconds FROM membership_products WHERE product_id = 'vip-promo-250'"
            ).fetchone()
            assert Decimal(promo_pkg["price"]) == Decimal("240.00")
            assert int(promo_pkg["duration_seconds"]) == 10 * 3600
            assert Decimal(promo_product["price"]) == Decimal("240.00")
            assert int(promo_product["time_seconds"]) == 10 * 3600

            # Every changed rate_profiles row logged an audit event
            # (metered + vip base + 3 tiers + promo package = 6).
            audit_after = connection.execute("SELECT COUNT(*) FROM rate_audit_events").fetchone()[0]
            assert audit_after - audit_before == 6, (audit_before, audit_after)

            # Legacy Standard package rows are untouched by this editor.
            legacy = connection.execute(
                "SELECT rate_id, price, duration_seconds, is_active FROM rate_profiles "
                "WHERE rate_id IN ('rate-standard-guest-5','rate-standard-guest-10','rate-standard-guest-15') "
                "ORDER BY rate_id"
            ).fetchall()
            assert [(r["rate_id"], Decimal(r["price"]), int(r["duration_seconds"]), int(r["is_active"])) for r in legacy] == [
                ("rate-standard-guest-10", Decimal("10.00"), 2400, 1),
                ("rate-standard-guest-15", Decimal("15.00"), 3600, 1),
                ("rate-standard-guest-5", Decimal("5.00"), 1200, 1),
            ]

        # --- rollback: an invalid request must leave everything untouched --
        with get_connection() as connection:
            before_rows = connection.execute(
                "SELECT rate_id, price, duration_seconds FROM rate_profiles ORDER BY rate_id"
            ).fetchall()
            before = [(r["rate_id"], r["price"], r["duration_seconds"]) for r in before_rows]
            audit_pre_fail = connection.execute("SELECT COUNT(*) FROM rate_audit_events").fetchone()[0]

        # Point the LAST write's fixed id at a row that does not exist ->
        # _product_row raises 404 after every other row has already been
        # written on the connection, so the whole transaction must roll back.
        original_last_id = pricing_service.MEMBER_TOPUP_LARGE_PRODUCT_ID
        pricing_service.MEMBER_TOPUP_LARGE_PRODUCT_ID = "product-does-not-exist"
        try:
            raised = False
            try:
                pricing_service.apply_pricing_update(
                    PricingUpdateRequest(
                        standard_guest=PricingMoneyTime(price=Decimal("9.99"), seconds=1200),
                        standard_member_new_account=PricingMoneyTime(price=Decimal("1.00"), seconds=3600),
                        standard_member_topup_small=PricingMoneyTime(price=Decimal("1.00"), seconds=3600),
                        standard_member_topup_large=PricingMoneyTime(price=Decimal("1.00"), seconds=3600),
                        vip_guest_hourly=PricingMoneyTime(price=Decimal("1.00"), seconds=3600),
                        promo=PricingMoneyTime(price=Decimal("1.00"), seconds=3600),
                    ),
                    actor_user_id="tester",
                )
            except HTTPException as error:
                raised = True
                assert error.status_code == 404
            assert raised, "expected a 404 for the missing allowlisted row"
        finally:
            pricing_service.MEMBER_TOPUP_LARGE_PRODUCT_ID = original_last_id

        with get_connection() as connection:
            after_rows = connection.execute(
                "SELECT rate_id, price, duration_seconds FROM rate_profiles ORDER BY rate_id"
            ).fetchall()
            after = [(r["rate_id"], r["price"], r["duration_seconds"]) for r in after_rows]
            audit_post_fail = connection.execute("SELECT COUNT(*) FROM rate_audit_events").fetchone()[0]
        assert after == before, "a failed pricing update must not change any row"
        assert audit_post_fail == audit_pre_fail, "a failed pricing update must not write audit rows"

        # --- invalid / zero / negative pricing input is rejected -----------
        for bad_price in (Decimal("0.00"), Decimal("-0.01"), Decimal("-5.00")):
            rejected = False
            try:
                PricingMoneyTime(price=bad_price, seconds=1200)
            except Exception:
                rejected = True
            assert rejected, f"price {bad_price} must be rejected"
        for bad_seconds in (0, -1, -3600):
            rejected = False
            try:
                PricingMoneyTime(price=Decimal("5.00"), seconds=bad_seconds)
            except Exception:
                rejected = True
            assert rejected, f"seconds {bad_seconds} must be rejected"

        with get_connection() as connection:
            state_pre_bad = [
                (r["rate_id"], r["price"], r["duration_seconds"])
                for r in connection.execute(
                    "SELECT rate_id, price, duration_seconds FROM rate_profiles ORDER BY rate_id"
                ).fetchall()
            ]
            audit_pre_bad = connection.execute("SELECT COUNT(*) FROM rate_audit_events").fetchone()[0]
        rejected = False
        try:
            PricingUpdateRequest(
                standard_guest=PricingMoneyTime(price=Decimal("5.00"), seconds=1200),
                standard_member_new_account=PricingMoneyTime(price=Decimal("100.00"), seconds=3600),
                standard_member_topup_small=PricingMoneyTime(price=Decimal("50.00"), seconds=3600),
                standard_member_topup_large=PricingMoneyTime(price=Decimal("100.00"), seconds=3600),
                vip_guest_hourly=PricingMoneyTime(price=Decimal("-5.00"), seconds=3600),
                promo=PricingMoneyTime(price=Decimal("250.00"), seconds=3600),
            )
        except Exception:
            rejected = True
        assert rejected, "a request carrying a negative price must not construct"
        with get_connection() as connection:
            state_post_bad = [
                (r["rate_id"], r["price"], r["duration_seconds"])
                for r in connection.execute(
                    "SELECT rate_id, price, duration_seconds FROM rate_profiles ORDER BY rate_id"
                ).fetchall()
            ]
            audit_post_bad = connection.execute("SELECT COUNT(*) FROM rate_audit_events").fetchone()[0]
        assert state_post_bad == state_pre_bad and audit_post_bad == audit_pre_bad

        # --- saving the CURRENT values is a full no-op --------------------
        # No audit events, no rate_profiles change, AND no membership_products
        # change -- including updated_at, which the _update_product no-op
        # guard must leave alone (mirrors _update_rate).
        _RATE_SNAPSHOT_SQL = (
            "SELECT rate_id, price, duration_seconds, updated_at FROM rate_profiles ORDER BY rate_id"
        )
        _PRODUCT_SNAPSHOT_SQL = (
            "SELECT product_id, price, time_seconds, updated_at FROM membership_products ORDER BY product_id"
        )

        def _rows(connection, sql):
            return [tuple(row) for row in connection.execute(sql).fetchall()]

        current = pricing_service.get_editable_pricing()
        noop_request = PricingUpdateRequest(
            standard_guest=current.standard_guest,
            standard_member_new_account=current.standard_member_new_account,
            standard_member_topup_small=current.standard_member_topup_small,
            standard_member_topup_large=current.standard_member_topup_large,
            vip_guest_hourly=current.vip_guest_hourly,
            promo=current.promo,
        )
        with get_connection() as connection:
            audit_before_noop = connection.execute("SELECT COUNT(*) FROM rate_audit_events").fetchone()[0]
            rate_rows_before_noop = _rows(connection, _RATE_SNAPSHOT_SQL)
            product_rows_before_noop = _rows(connection, _PRODUCT_SNAPSHOT_SQL)
        pricing_service.apply_pricing_update(noop_request, actor_user_id="tester")
        with get_connection() as connection:
            audit_after_noop = connection.execute("SELECT COUNT(*) FROM rate_audit_events").fetchone()[0]
            rate_rows_after_noop = _rows(connection, _RATE_SNAPSHOT_SQL)
            product_rows_after_noop = _rows(connection, _PRODUCT_SNAPSHOT_SQL)
        assert audit_after_noop == audit_before_noop, "saving unchanged values must write no audit events"
        assert rate_rows_after_noop == rate_rows_before_noop, (
            "an unchanged save must not touch any rate_profiles row (incl. updated_at)"
        )
        assert product_rows_after_noop == product_rows_before_noop, (
            "an unchanged save must not touch any membership_products row (incl. updated_at)"
        )

        # --- a partial edit audits only the rate rows that actually change -
        base = pricing_service.get_editable_pricing()
        partial_request = PricingUpdateRequest(
            standard_guest=PricingMoneyTime(price=Decimal("7.00"), seconds=base.standard_guest.seconds),
            standard_member_new_account=base.standard_member_new_account,
            standard_member_topup_small=base.standard_member_topup_small,
            standard_member_topup_large=base.standard_member_topup_large,
            vip_guest_hourly=base.vip_guest_hourly,
            promo=base.promo,
        )
        with get_connection() as connection:
            audit_before_partial = connection.execute("SELECT COUNT(*) FROM rate_audit_events").fetchone()[0]
        pricing_service.apply_pricing_update(partial_request, actor_user_id="tester")
        with get_connection() as connection:
            audit_after_partial = connection.execute("SELECT COUNT(*) FROM rate_audit_events").fetchone()[0]
        assert audit_after_partial - audit_before_partial == 1, "only the one changed rate row should be audited"

    print("smoke_pricing_editor OK")


if __name__ == "__main__":
    main()
