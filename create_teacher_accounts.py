
import getpass
import secrets
import string
from datetime import datetime, timezone

import psycopg
from werkzeug.security import generate_password_hash


TEACHERS = [
    ("Bismark", "bismark@gmail.com"),
    ("Niwagaba", "niwagaba@gmail.com"),
    ("Bimark", "bimark@gmail.com"),
    ("Aijuka", "aijuka@gmail.com"),
]


def make_password():
    chars = string.ascii_letters + string.digits + "!@#$%?"
    while True:
        password = "".join(secrets.choice(chars) for _ in range(20))
        if (
            any(c.islower() for c in password)
            and any(c.isupper() for c in password)
            and any(c.isdigit() for c in password)
            and any(c in "!@#$%?" for c in password)
        ):
            return password


def main():
    print("\n--- CREATE FOUR TEACHER ACCOUNTS ---")
    print("Connecting to the LIVE Render database.\n")

    url = getpass.getpass("New Render External Database URL: ")

    if not url.strip():
        print("Database URL is required.")
        return

    created = []

    try:
        with psycopg.connect(url, connect_timeout=20) as conn:
            with conn.cursor() as cur:

                for name, email in TEACHERS:
                    cur.execute(
                        "SELECT id, role FROM users WHERE LOWER(email) = %s",
                        (email.lower(),)
                    )
                    existing = cur.fetchone()

                    if existing:
                        print(
                            f"SKIPPED: {email} already exists "
                            f"(role: {existing[1]})."
                        )
                        continue

                    password = make_password()
                    password_hash = generate_password_hash(password)
                    created_at = datetime.now(
                        timezone.utc
                    ).isoformat(timespec="seconds")

                    cur.execute(
                        """
                        INSERT INTO users (
                            full_name, email, phone, password_hash,
                            role, is_active, must_change_password,
                            created_at, is_hidden
                        )
                        VALUES (%s, %s, NULL, %s, 'teacher', 1, 1, %s, 0)
                        RETURNING id
                        """,
                        (name, email, password_hash, created_at)
                    )

                    account_id = cur.fetchone()[0]
                    created.append((name, email, password, account_id))

        print("\n--- NEW TEACHER ACCOUNTS ---")

        if not created:
            print("No new accounts were created.")
        else:
            for name, email, password, account_id in created:
                print(f"\nName: {name}")
                print(f"Email: {email}")
                print(f"Temporary password: {password}")
                print(f"Account ID: {account_id}")

            print("\nSave these passwords securely.")
            print("They will not be displayed again.")

        print("\nTeacher account creation process completed.")

    except Exception as error:
        print("\nERROR: No successful completion.")
        print(error)


if __name__ == "__main__":
    main()
