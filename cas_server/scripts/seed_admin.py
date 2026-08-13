"""Bootstrap the first ADMIN user for local testing.

Usage (from repo root, venv active):
    python -m cas_server.scripts.seed_admin <username> <password>
"""

import sys

from cas_server.db.base import SessionLocal
from cas_server.db.models import RoleEnum, User
from cas_server.security.passwords import hash_password


def seed_admin(username: str, password: str) -> None:
    with SessionLocal() as session:
        existing = session.query(User).filter_by(username=username).one_or_none()
        if existing is not None:
            print(
                f"User '{username}' already exists (role={existing.role.value}); nothing to do."
            )
            return
        user = User(
            username=username,
            password_hash=hash_password(password),
            role=RoleEnum.ADMIN,
        )
        session.add(user)
        session.commit()
        print(f"Created ADMIN user '{username}' (id={user.id}).")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python -m cas_server.scripts.seed_admin <username> <password>")
        sys.exit(1)
    seed_admin(sys.argv[1], sys.argv[2])
