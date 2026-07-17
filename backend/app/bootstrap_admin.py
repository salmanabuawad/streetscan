"""Create the initial admin user if no users exist yet.

Run with:  python -m app.bootstrap_admin
Prints the generated credentials exactly once — deployment shows them to the
operator, who should change the password or create personal users right away.
Also usable to create extra users:
    python -m app.bootstrap_admin <username> <password> <role> [display name]
"""
import secrets
import sys

from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import Base, SessionLocal, engine
from app.models.entities import User, UserRole


def main():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if len(sys.argv) >= 4:
            username, password, role = sys.argv[1], sys.argv[2], sys.argv[3]
            display = sys.argv[4] if len(sys.argv) > 4 else username
            if db.scalar(select(User).where(User.username == username)):
                print(f"user '{username}' already exists")
                return
            db.add(User(username=username, password_hash=hash_password(password),
                        display_name=display, role=UserRole(role)))
            db.commit()
            print(f"created user '{username}' with role {role}")
            return

        if db.scalar(select(User).limit(1)):
            print("users already exist; nothing to do")
            return
        password = secrets.token_urlsafe(9)
        db.add(User(username="admin", password_hash=hash_password(password),
                    display_name="Administrator", role=UserRole.ADMIN))
        db.commit()
        print("==============================================")
        print("  Initial admin user created")
        print(f"  username: admin")
        print(f"  password: {password}")
        print("  Change it or create personal users promptly.")
        print("==============================================")


if __name__ == "__main__":
    main()
