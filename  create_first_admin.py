
import getpass
from datetime import datetime, timezone

import psycopg
from werkzeug.security import generate_password_hash


def main():
    print("\n--- CREATE FIRST HOLIDAY SCIENCE HUB ADMIN ---\n")

    url = getpass.getpass("New Render External Database URL: ")

    if not url.strip():
        print("Database URL is required.")
        return

    name = input("Administrator full name: ").strip()
    email = input("Administrator email: ").strip().lower()
    phone = input("Phone number (optional): ").strip() or None

    password = getpass.getpass("Choose administrator password (minimum 12 characters): ")
    confirm = getpass.getpass("Confirm administrator password: ")

    if not name or not email:
        print("Name and email are required.")
        return

    if len(password) < 12:
        print("Password must contain at least 12 characters.")
        return

    if password != confirm:
        print("Passwords do not match.")
        return

    password_hash = generate_password_hash(password)
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    try:
        with psycopg.connect(url, connect_timeout=20) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM users")
                count = cur.fetchone()[0]

                if count != 0:
                    print(f"STOPPED: The production users table contains {count} account(s).")
                    print("No administrator was created.")
                    return

                cur.execute(
                    """
                    INSERT INTO users (
                        full_name, email, phone, password_hash,
                        role, is_active, must_change_password,
                        created_at, is_hidden
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, email, role
                    """,
                    (
                        name, email, phone, password_hash,
                        "admin", 1, 0, created_at, 0
                    )
                )

                account = cur.fetchone()

        print("\nSUCCESS: Administrator account created!")
        print("Account ID:", account[0])
        print("Email:", account[1])
        print("Role:", account[2])
        print("\nYou can now sign in to the live website.")

    except Exception as error:
        print("\nCould not create administrator.")
        print("Error:", error)


if __name__ == "__main__":
    main()
