"""El temario que el docente ha editado.

Aquí se guarda **solo lo que el docente cambia**, no el curso entero. El
temario de fábrica sigue viviendo dentro de la aplicación, así que si este
servidor se cae el estudiante no se queda sin curso: ve la versión original en
lugar de la editada.

Ese reparto tiene una consecuencia práctica que conviene tener presente: una
sección que el docente no haya tocado nunca no aparece aquí, y eso es lo
normal, no un error.

Leer es público —el estudiante necesita ver lo editado— pero escribir exige
ser docente, comprobado contra el token que firma este mismo servidor.
"""

import json
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from neon_storage import get_db
from neon_storage.models import CourseOverride

from user_management_service.auth import Caller, require_course_editor

router = APIRouter(prefix="/api/course", tags=["Contenido del curso"])

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
        updated_by=row.updated_by,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )


@router.get("/overrides")
def list_overrides(response: Response, db: Session = Depends(get_db)):
    """Todo lo que el docente ha cambiado.

    La aplicación lo pide una vez y lo va combinando con su temario de fábrica.
    Una lista vacía significa que nadie ha editado nada todavía, que es el
    estado normal al principio.
    """
    rows = db.query(CourseOverride).all()
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

    return {"version": version, "count": len(items), "items": items}


@router.get("/overrides/{scope}/{target_id}")
def get_override(scope: str, target_id: str, db: Session = Depends(get_db)):
    """Lo editado de una sección o un módulo concretos."""
    _check_scope(scope)

    row = (
        db.query(CourseOverride)
        .filter(CourseOverride.scope == scope, CourseOverride.target_id == target_id)
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Esa parte del curso no se ha editado todavía.",
        )

    return _to_out(row)


@router.put("/overrides/{scope}/{target_id}")
def save_override(
    scope: str,
    target_id: str,
    payload: OverridePayload,
    db: Session = Depends(get_db),
    caller: Caller = Depends(require_course_editor),
):
    """Guarda lo que el docente acaba de editar.

    Se guarda entero, no por trozos: es lo que permite que el docente vea en
    su pantalla exactamente lo que va a ver el estudiante.
    """
    _check_scope(scope)

    if not target_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Falta decir qué sección o módulo se está editando.",
        )

    serialized = json.dumps(payload.content, ensure_ascii=False)
    if len(serialized.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="El contenido de esta sección es demasiado grande para guardarlo.",
        )

    row = (
        db.query(CourseOverride)
        .filter(CourseOverride.scope == scope, CourseOverride.target_id == target_id)
        .first()
    )

    now = datetime.now(timezone.utc)
    if row is None:
        row = CourseOverride(
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


@router.delete("/overrides/{scope}/{target_id}", status_code=status.HTTP_200_OK)
def revert_override(
    scope: str,
    target_id: str,
    db: Session = Depends(get_db),
    caller: Caller = Depends(require_course_editor),
):
    """Devuelve una sección a su versión original.

    No borra contenido del curso: borra la edición, con lo que vuelve a verse
    el material de fábrica. Por eso se puede deshacer un cambio sin miedo.
    """
    _check_scope(scope)

    row = (
        db.query(CourseOverride)
        .filter(CourseOverride.scope == scope, CourseOverride.target_id == target_id)
        .first()
    )
    if row is None:
        # Ya estaba en su versión original: el resultado es el que se quería.
        return {"reverted": False, "detail": "Esa parte ya estaba sin editar."}

    db.delete(row)
    db.commit()

    return {"reverted": True, "detail": "La sección volvió a su versión original."}


@router.get("/can-edit")
def can_edit(caller: Caller = Depends(require_course_editor)):
    """Dice si quien llama puede editar, sin llegar a cambiar nada.

    La aplicación lo consulta para no enseñar botones de editar a quien luego
    va a recibir un 403 al pulsarlos.
    """
    return {"can_edit": True, "email": caller.email, "rol": caller.role}
