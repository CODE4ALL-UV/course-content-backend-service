"""El nombre y los temas de cada módulo del curso.

Antes esto vivía en archivos JSON dentro de `uploads/modules/`. Dos problemas
acabaron con esa idea:

1. El disco de Render es efímero. Cada despliegue borraba lo que el docente
   hubiera guardado, y sin avisar: el editor decía «Módulo guardado» y al
   siguiente despliegue no quedaba nada.
2. Era un segundo almacén de nombres de módulo, en paralelo al de
   `/api/course/overrides`. Un docente que renombraba un módulo aquí no
   cambiaba nada para las pantallas que leían del otro, ni al revés. Dos
   editores, dos verdades.

Ahora hay un solo sitio: la misma fila de `CourseOverride` con `scope='module'`
que usa el resto del temario. El nombre vive en `payload['title']`, que es
exactamente lo que lee `moduleTitle()` en la aplicación, así que editar por
aquí o por allí da el mismo resultado.

Leer es público, porque el estudiante necesita ver el nombre editado. Escribir
exige ser docente: antes no lo exigía, y cualquiera podía renombrar un módulo
del curso sin haber iniciado sesión siquiera.
"""

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from neon_storage import get_db
from neon_storage.models import CourseOverride

from user_management_service.auth import Caller, require_course_editor

router = APIRouter(prefix="/api/modules", tags=["modules"])

# El mismo `scope` que usa /api/course/overrides. Es lo que hace que los dos
# caminos acaben en la misma fila.
MODULE_SCOPE = "module"

# Un nombre de módulo es una línea, no un documento. El tope existe para que
# un error no convierta la base en un vertedero.
MAX_PAYLOAD_BYTES = 64 * 1024


class ModulePayload(BaseModel):
    module_id: str | None = None
    name: str | None = None
    topics: list[str] | None = None


def _module_target(module_id: str) -> str:
    """Comprueba que el módulo se identifique por su número y lo normaliza.

    La aplicación numera los módulos del 1 al 6 y `moduleTitle(n)` busca por
    ese número. Aceptar cualquier cosa como identificador es lo que llenó el
    almacén antiguo de archivos con UUID que después nadie era capaz de pedir.
    """
    cleaned = (module_id or "").strip()

    if not cleaned.isdigit() or int(cleaned) < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"'{module_id}' no identifica un módulo. Se espera su número, "
                "por ejemplo 1."
            ),
        )

    # '01' y '1' son el mismo módulo: sin esto serían dos filas distintas y el
    # docente vería su cambio desaparecer según por dónde entrara.
    return str(int(cleaned))


def _read_payload(row: CourseOverride) -> dict[str, Any]:
    try:
        content = json.loads(row.payload)
    except (ValueError, TypeError):
        return {}
    return content if isinstance(content, dict) else {}


def _to_out(module_id: str, content: dict[str, Any]) -> dict[str, Any]:
    """La forma que espera la aplicación: `id`, `name` y `topics`.

    Por dentro el nombre se llama `title`, porque así lo guarda el resto del
    temario. La traducción se hace aquí para no tener que tocar el editor.
    """
    topics = content.get("topics")

    return {
        "id": module_id,
        "name": content.get("title") or "",
        "topics": [str(t) for t in topics] if isinstance(topics, list) else [],
    }


def _find(db: Session, target_id: str) -> CourseOverride | None:
    return (
        db.query(CourseOverride)
        .filter(
            CourseOverride.scope == MODULE_SCOPE,
            CourseOverride.target_id == target_id,
        )
        .first()
    )


def _save(
    db: Session, target_id: str, content: dict[str, Any], caller: Caller
) -> dict[str, Any]:
    serialized = json.dumps(content, ensure_ascii=False)
    if len(serialized.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Ese módulo lleva demasiado texto para guardarlo.",
        )

    row = _find(db, target_id)
    now = datetime.now(timezone.utc)

    if row is None:
        row = CourseOverride(
            scope=MODULE_SCOPE,
            target_id=target_id,
            payload=serialized,
            updated_by=caller.email,
            updated_at=now,
        )
        db.add(row)
    else:
        row.payload = serialized
        row.updated_by = caller.email
        row.updated_at = now

    db.commit()
    db.refresh(row)

    return _to_out(target_id, _read_payload(row))


@router.post("/", status_code=201)
def create_module(
    payload: ModulePayload,
    db: Session = Depends(get_db),
    caller: Caller = Depends(require_course_editor),
):
    """Guarda el nombre y los temas de un módulo.

    Se llama «create» por historia, pero guarda encima si ya había algo: el
    editor manda siempre el módulo entero, así que lo que llega es la versión
    buena y completa.
    """
    target_id = _module_target(payload.module_id or "")

    content: dict[str, Any] = {"title": (payload.name or "").strip()}
    if payload.topics is not None:
        content["topics"] = [str(t) for t in payload.topics]

    return _save(db, target_id, content, caller)


@router.get("/{module_id}")
def get_module(module_id: str, db: Session = Depends(get_db)):
    """El módulo tal y como lo dejó el docente.

    Un 404 aquí significa «este módulo no se ha editado nunca», que es el
    estado normal al principio y no un error: la aplicación ya tiene el
    nombre de fábrica y lo usa sin preguntar a nadie.
    """
    target_id = _module_target(module_id)

    row = _find(db, target_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Module not found")

    return _to_out(target_id, _read_payload(row))


@router.patch("/{module_id}")
def update_module(
    module_id: str,
    payload: ModulePayload,
    db: Session = Depends(get_db),
    caller: Caller = Depends(require_course_editor),
):
    """Cambia solo lo que venga: lo que no se manda se queda como estaba."""
    target_id = _module_target(module_id)

    row = _find(db, target_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Module not found")

    content = _read_payload(row)
    if payload.name is not None:
        content["title"] = payload.name.strip()
    if payload.topics is not None:
        content["topics"] = [str(t) for t in payload.topics]

    return _save(db, target_id, content, caller)
