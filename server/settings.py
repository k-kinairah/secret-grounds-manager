import os


def read_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


EXPIRATION_GRACE_SECONDS = int(os.getenv("SECRET_GROUNDS_EXPIRATION_GRACE_SECONDS", "120"))
TEST_MODE = read_bool("SECRET_GROUNDS_TEST_MODE", False)
AUTOMATIC_SHUTDOWN_ON_EXPIRATION = read_bool(
    "SECRET_GROUNDS_AUTOMATIC_SHUTDOWN_ON_EXPIRATION",
    not TEST_MODE,
)
EXPIRATION_SOUND_ENABLED = read_bool("SECRET_GROUNDS_EXPIRATION_SOUND_ENABLED", True)
SHOP_DISPLAY_NAME = os.getenv("SECRET_GROUNDS_SHOP_DISPLAY_NAME", "Secret Grounds")
SHOP_SUBTITLE = os.getenv("SECRET_GROUNDS_SHOP_SUBTITLE", "Computer Shop")
TIMEZONE_DISPLAY = os.getenv("SECRET_GROUNDS_TIMEZONE_DISPLAY", "Asia/Manila")
# Multiplier applied to the shop's single (Standard-scoped) metered rate when
# an arbitrary cash amount is topped up on a VIP PC. 2 => a VIP peso buys half
# the minutes a Standard peso does (e.g. P5 = 20 min becomes P5 = 10 min).
VIP_METERED_MULTIPLIER = os.getenv("SECRET_GROUNDS_VIP_METERED_MULTIPLIER", "2")
REMINDER_THRESHOLDS_SECONDS = [
    int(value.strip())
    for value in os.getenv(
        "SECRET_GROUNDS_REMINDER_THRESHOLDS_SECONDS", "900,600,300,240,180,120,60"
    ).split(",")
    if value.strip()
]
CUSTOMER_OVERLAY_ENABLED = read_bool("SECRET_GROUNDS_CUSTOMER_OVERLAY_ENABLED", True)
CUSTOMER_OVERLAY_POSITION = os.getenv("SECRET_GROUNDS_CUSTOMER_OVERLAY_POSITION", "top-right")
CUSTOMER_BRANDING_TEXT = os.getenv("SECRET_GROUNDS_CUSTOMER_BRANDING_TEXT", "Ask the cashier to add more time to continue.")
REMINDER_SOUNDS_ENABLED = read_bool("SECRET_GROUNDS_REMINDER_SOUNDS_ENABLED", True)

# PC screen monitoring (prototype): in-memory-only latest-frame cache, no
# database/file storage. See server/screen_monitor_service.py.
SCREEN_MONITORING_ENABLED = read_bool("SCREEN_MONITORING_ENABLED", True)
SCREEN_MAX_FRAME_BYTES = int(os.getenv("SCREEN_MAX_FRAME_BYTES", "500000"))
SCREEN_MIN_FRAME_INTERVAL_MS = int(os.getenv("SCREEN_MIN_FRAME_INTERVAL_MS", "500"))
SCREEN_STALE_AFTER_SECONDS = int(os.getenv("SCREEN_STALE_AFTER_SECONDS", "10"))
