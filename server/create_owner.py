import getpass

from server.database import initialize_database
from server.schemas import UserCreateRequest
from server.user_repository import UserRepository


def main() -> None:
    initialize_database()
    repository = UserRepository()
    if repository.count_active_owners() > 0:
        print("An active owner already exists. No account was created.")
        return

    username = input("Owner username: ").strip()
    display_name = input("Owner display name: ").strip()
    password = getpass.getpass("Owner password: ")
    confirmation = getpass.getpass("Confirm password: ")

    if password != confirmation:
        raise SystemExit("Passwords do not match.")
    if len(password) < 8:
        raise SystemExit("Password must be at least 8 characters.")

    created = repository.create_user(
        UserCreateRequest(
            username=username,
            display_name=display_name,
            password=password,
            role="owner",
        )
    )
    print(f"Created owner account: {created.username}")


if __name__ == "__main__":
    main()
