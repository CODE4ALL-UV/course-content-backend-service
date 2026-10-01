"""El temario que cada docente ha editado en sus cursos.

Aquí se guarda **solo lo que el docente cambia**, no el curso entero. El
temario de fábrica sigue viviendo dentro de la aplicación, así que si este
servidor se cae el estudiante no se queda sin curso: ve la versión original en
lugar de la editada.

Ese reparto tiene una consecuencia práctica que conviene tener presente: una
sección que el docente no haya tocado nunca no aparece aquí, y eso es lo
normal, no un error.

Cada edición es de un curso. Hay dos caminos:

- `/api/courses/{id}/overrides/...`: el de ahora. Lee quien puede ver el curso
  y escribe solo su docente (ver `course_content_service.access`).
- `/api/course/overrides/...`: el de siempre, sin curso. Trabaja sobre el
  Curso general, para que una versión vieja de la aplicación siga viendo el
  curso igual que antes. Escribir ahí ya es solo de la coordinación.
"""

import json
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from neon_storage import get_db
from neon_storage.courses import general_course
from neon_storage.models import Course, CourseOverride

from course_content_service.access import (
    load_course,
    require_edit,
    require_view,
)
from user_management_service.auth import Caller, current_caller, require_course_editor

router = APIRouter(prefix="/api/course", tags=["Contenido del curso"])
course_router = APIRouter(prefix="/api/courses", tags=["Contenido del curso"])

# Qué se puede editar.
SECTION = "section"
MODULE = "module"
SCOPES = {SECTION, MODULE}

# Tope por sección. Una sección con lecturas, quiz y ejercicios ronda unas
# pocas decenas de kilobytes; medio mega deja margen de sobra y a la vez
# impide que un error convierta la base en un vertedero.
MAX_PAYLOAD_BYTES = 512 * 1024


class OverridePayload(BaseModel):
    """Lo editado, tal cual lo entiende la aplicación."""

    content: dict[str, Any] = Field(..., description="Contenido editado")


class OverrideOut(BaseModel):
    scope: str
    target_id: str
    content: dict[str, Any]
    course_id: Optional[int] = None
    updated_by: Optional[str] = None
    updated_at: Optional[str] = None


def _check_scope(scope: str) -> str:
    if scope not in SCOPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No se puede editar '{scope}'. Solo: {', '.join(sorted(SCOPES))}.",
        )
    return scope


def _to_out(row: CourseOverride) -> OverrideOut:
    try:
        content = json.loads(row.payload)
    except (ValueError, TypeError):
        # Un cambio ilegible no debe tumbar la lista entera: se devuelve vacío
        # y la aplicación mostrará el original de esa sección.
        content = {}

    return OverrideOut(
        scope=row.scope,
        target_id=row.target_id,
        content=content if isinstance(content, dict) else {},
        course_id=row.course_id,
        updated_by=row.updated_by,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )


def _find(db: Session, course: Course, scope: str, target_id: str) -> Optional[CourseOverride]:
    return (
        db.query(CourseOverride)
        .filter(
            CourseOverride.course_id == course.id,
            CourseOverride.scope == scope,
            CourseOverride.target_id == target_id,
        )
        .first()
    )


# --- lo común a los dos caminos ---------------------------------------------


def list_overrides_of(db: Session, course: Course, response: Response) -> dict:
    """Todo lo que se ha cambiado en un curso.

    La aplicación lo pide una vez y lo va combinando con su temario de fábrica.
    Una lista vacía significa que nadie ha editado nada todavía, que es el
    estado normal al principio.
    """
    rows = db.query(CourseOverride).filter(CourseOverride.course_id == course.id).all()
    items = [_to_out(row) for row in rows]

    # `version` cambia solo cuando cambia algo, así la aplicación sabe si le
    # merece la pena volver a pedirlo.
    latest = max(
        (row.updated_at for row in rows if row.updated_at),
        default=None,
    )
    version = latest.isoformat() if latest else ""

    # Sin caché: el sentido de todo esto es que el cambio se vea enseguida.
    response.headers["Cache-Control"] = "no-store"

    return {"course_id": course.id, "version": version, "count": len(items), "items": items}


def get_override_of(db: Session, course: Course, scope: str, target_id: str) -> OverrideOut:
    _check_scope(scope)

    row = _find(db, course, scope, target_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Esa parte del curso no se ha editado todavía.",
        )

    return _to_out(row)


def save_override_of(
    db: Session,
    course: Course,
    scope: str,
    target_id: str,
    content: dict[str, Any],
    caller: Caller,
    max_bytes: int = MAX_PAYLOAD_BYTES,
) -> OverrideOut:
    """Guarda lo editado.

    Se guarda entero, no por trozos: es lo que permite que el docente vea en
    su pantalla exactamente lo que va a ver el estudiante.
    """
    _check_scope(scope)

    if not target_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Falta decir qué sección o módulo se está editando.",
        )

    serialized = json.dumps(content, ensure_ascii=False)
    if len(serialized.encode("utf-8")) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="El contenido de esta sección es demasiado grande para guardarlo.",
        )

    row = _find(db, course, scope, target_id)

    now = datetime.now(timezone.utc)
    if row is None:
        row = CourseOverride(
            course_id=course.id,
            scope=scope,
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

    return _to_out(row)


def revert_override_of(db: Session, course: Course, scope: str, target_id: str) -> dict:
    """Devuelve una sección a su versión original.

    No borra contenido del curso: borra la edición, con lo que vuelve a verse
    el material de fábrica. Por eso se puede deshacer un cambio sin miedo.
    """
    _check_scope(scope)

    row = _find(db, course, scope, target_id)
    if row is None:
        # Ya estaba en su versión original: el resultado es el que se quería.
        return {"reverted": False, "detail": "Esa parte ya estaba sin editar."}

    db.delete(row)
    db.commit()

    return {"reverted": True, "detail": "La sección volvió a su versión original."}


# --- el camino de siempre: el Curso general ---------------------------------


@router.get("/overrides")
def list_overrides(response: Response, db: Session = Depends(get_db)):
    """Lo editado en el Curso general. Leer es público, como siempre."""
    return list_overrides_of(db, general_course(db), response)


@router.get("/overrides/{scope}/{target_id}")
def get_override(scope: str, target_id: str, db: Session = Depends(get_db)):
    """Lo editado de una sección o un módulo del Curso general."""
    return get_override_of(db, general_course(db), scope, target_id)


@router.put("/overrides/{scope}/{target_id}")
def save_override(
    scope: str,
    target_id: str,
    payload: OverridePayload,
    db: Session = Depends(get_db),
    caller: Caller = Depends(require_course_editor),
):
    """Edita el Curso general. Solo la coordinación."""
    course = require_edit(general_course(db), caller)
    return save_override_of(db, course, scope, target_id, payload.content, caller)


@router.delete("/overrides/{scope}/{target_id}", status_code=status.HTTP_200_OK)
def revert_override(
    scope: str,
    target_id: str,
    db: Session = Depends(get_db),
    caller: Caller = Depends(require_course_editor),
):
    """Deshace una edición del Curso general. Solo la coordinación."""
    course = require_edit(general_course(db), caller)
    return revert_override_of(db, course, scope, target_id)


@router.get("/can-edit")
def can_edit(caller: Caller = Depends(require_course_editor)):
    """Dice si quien llama puede editar, sin llegar a cambiar nada.

    La aplicación lo consulta para no enseñar botones de editar a quien luego
    va a recibir un 403 al pulsarlos.
    """
    return {"can_edit": True, "email": caller.email, "rol": caller.role}


# --- por curso ----------------------------------------------------------------


@course_router.get("/{course_id}/overrides")
def list_course_overrides(
    course_id: int,
    response: Response,
    db: Session = Depends(get_db),
    caller: Caller = Depends(current_caller),
):
    """Lo editado en un curso: lo ven su docente, sus estudiantes y la coordinación."""
    course = require_view(db, load_course(db, course_id), caller)
    return list_overrides_of(db, course, response)


@course_router.get("/{course_id}/overrides/{scope}/{target_id}")
def get_course_override(
    course_id: int,
    scope: str,
    target_id: str,
    db: Session = Depends(get_db),
    caller: Caller = Depends(current_caller),
):
    course = require_view(db, load_course(db, course_id), caller)
    return get_override_of(db, course, scope, target_id)


@course_router.put("/{course_id}/overrides/{scope}/{target_id}")
def save_course_override(
    course_id: int,
    scope: str,
    target_id: str,
    payload: OverridePayload,
    db: Session = Depends(get_db),
    caller: Caller = Depends(current_caller),
):
    """Guarda una edición en un curso. Solo su docente."""
    course = require_edit(load_course(db, course_id), caller)
    return save_override_of(db, course, scope, target_id, payload.content, caller)


@course_router.delete("/{course_id}/overrides/{scope}/{target_id}")
def revert_course_override(
    course_id: int,
    scope: str,
    target_id: str,
    db: Session = Depends(get_db),
    caller: Caller = Depends(current_caller),
):
    course = require_edit(load_course(db, course_id), caller)
    return revert_override_of(db, course, scope, target_id)
