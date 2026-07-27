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
MANUAL_END_DEFAULT_BEHAVIOR = os.getenv("SECRET_GROUNDS_MANUAL_END_DEFAULT_BEHAVIOR", "lock_only")
