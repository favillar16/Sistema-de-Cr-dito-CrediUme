"""Bulk-seed random test users for manual QA of the desktop client.

Usage (from repo root, venv active):
    python -m cas_server.scripts.seed_random_users [count]

`count` defaults to 10. Usernames are generated at random and checked
against the DB for uniqueness before insert. Prints a table of the created
username/password/role at the end -- passwords are only ever shown here,
never stored in plaintext (see BR-AUTH-001).
"""

import secrets
import string
import sys

from cas_server.db.base import SessionLocal
from cas_server.db.models import RoleEnum, User
from cas_server.security.passwords import hash_password

_NOMBRES = (
    "Fernando",
    "Maria",
    "Jose",
    "Lucia",
    "Carlos",
    "Ana",
    "Diego",
    "Rosa",
    "Miguel",
    "Laura",
    "Ricardo",
    "Patricia",
    "Javier",
    "Silvia",
    "Ramon",
    "Elena",
    "Hugo",
    "Gloria",
    "Pablo",
    "Carmen",
)
_APELLIDOS = (
    "Gonzalez",
    "Villalba",
    "Benitez",
    "Ferreira",
    "Ovelar",
    "Duarte",
    "Cardozo",
    "Rojas",
    "Insfran",
    "Franco",
    "Aquino",
    "Ayala",
    "Vera",
    "Sosa",
    "Martinez",
    "Ruiz",
    "Diaz",
    "Melgarejo",
    "Cabrera",
    "Rios",
)

# Weighted so the "Estándar" tier (CREDIT_ANALYST) is most common, mirroring
# real usage -- see rbac_ui.py's tier_label() note that CASHIER isn't
# actually assigned to anyone today, but one is still included here so that
# tier stays exercised in manual QA too.
_ROLE_WEIGHTS = (
    (RoleEnum.CREDIT_ANALYST, 5),
    (RoleEnum.MANAGER, 2),
    (RoleEnum.ADMIN, 2),
    (RoleEnum.CASHIER, 1),
)


def _random_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    body = "".join(secrets.choice(alphabet) for _ in range(length - 1))
    return f"{body}!"


def _random_roles(count: int) -> list[RoleEnum]:
    roles: list[RoleEnum] = []
    for role, weight in _ROLE_WEIGHTS:
        roles.extend([role] * weight)
    result = []
    for _ in range(count):
        result.append(secrets.choice(roles))
    return result


def _unique_username(session, used: set[str]) -> str:
    for _ in range(50):
        candidate = (
            f"{secrets.choice(_NOMBRES)}.{secrets.choice(_APELLIDOS)}"
            f"{secrets.randbelow(90) + 10}"
        ).lower()
        if candidate in used:
            continue
        exists = session.query(User).filter_by(username=candidate).one_or_none()
        if exists is None:
            used.add(candidate)
            return candidate
    raise RuntimeError("No se pudo generar un nombre de usuario único.")


def seed_random_users(count: int) -> None:
    created: list[tuple[str, str, str]] = []
    with SessionLocal() as session:
        used: set[str] = set()
        for role in _random_roles(count):
            username = _unique_username(session, used)
            password = _random_password()
            user = User(
                username=username,
                password_hash=hash_password(password),
                role=role,
            )
            session.add(user)
            created.append((username, password, role.value))
        session.commit()

    print(f"Creados {len(created)} usuarios de prueba:\n")
    print(f"{'Usuario':<22} {'Contraseña':<15} Rol")
    print("-" * 50)
    for username, password, role in created:
        print(f"{username:<22} {password:<15} {role}")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    seed_random_users(n)
