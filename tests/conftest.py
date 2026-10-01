"""Las pruebas nunca tocan Neon.

Se fuerza una base SQLite temporal antes de importar nada: si DATABASE_URL
apuntara a Neon, una prueba que borra datos los borraría de verdad.

Los paquetes compartidos (neon_storage, user_management_service) se buscan en
los repos hermanos, así que esto funciona igual con los repos clonados uno al
lado del otro que dentro de services/ del gateway.
"""

import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

for repo in [REPO_ROOT, *sorted(REPO_ROOT.parent.glob("*-backend-service"))]:
    if str(repo) not in sys.path:
        sys.path.append(str(repo))

_db_file = Path(tempfile.mkdtemp()) / "code4all-test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file.as_posix()}"
