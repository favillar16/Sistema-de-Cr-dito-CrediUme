"""Bootstrap the first ADMIN user for local testing.

Usage (from repo root, venv active):
    python -m cas_server.scripts.seed_admin <username> <password>
    python -m cas_server.scripts.seed_admin <username> <password> <nombre> <apellido> <C.I.>

Los datos personales (BR-AUTH-006) son opcionales acá, a diferencia de
`CreateUser`, que los exige: este script existe justamente para crear el
primer ADMIN cuando todavía no hay nadie que pueda llamar a esa RPC, y
bloquearlo por falta de datos personales dejaría el sistema sin forma de
arrancar. Sin ellos el usuario funciona igual; solo aparecerá por su
`username` en los documentos que nombran a un responsable, hasta que se le
carguen los datos.
"""

import sys

from cas_server.db.base import SessionLocal
from cas_server.db.models import RoleEnum, User
from cas_server.security.passwords import hash_password


def seed_admin(
    username: str,
    password: str,
    first_name: str | None = None,
    last_name: str | None = None,
    national_id: str | None = None,
) -> None:
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
            first_name=first_name,
            last_name=last_name,
            national_id=national_id,
        )
        session.add(user)
        session.commit()
        print(f"Created ADMIN user '{username}' (id={user.id}).")
        if not (first_name and last_name and national_id):
            print(
                "Aviso: sin nombre/apellido/C.I. este usuario se identificará "
                "solo por su username en el Cronograma y el Comprobante de Pago."
            )


if __name__ == "__main__":
    if len(sys.argv) not in (3, 6):
        print(
            "Usage: python -m cas_server.scripts.seed_admin <username> <password>\n"
            "       python -m cas_server.scripts.seed_admin <username> <password> "
            "<nombre> <apellido> <C.I.>"
        )
        sys.exit(1)
    seed_admin(*sys.argv[1:])
