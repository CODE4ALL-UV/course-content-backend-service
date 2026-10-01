"""Quién puede ver y quién puede editar cada curso.

Las reglas, en una sola página:

- El **Curso general** lo ve cualquiera con sesión y solo lo edita la
  coordinación (rol director). Es el de antes de que hubiera cursos por
  docente, y ningún docente debe poder cambiárselo a todos.
- El curso de un **docente** lo edita solo ese docente. Lo ven él, sus
  estudiantes inscritos y la coordinación.

Assessment y progress-tracking importan de aquí en vez de repetir las reglas:
si cambian, cambian para todos a la vez.
"""

from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from neon_storage.courses import general_course, is_enrolled
from neon_storage.models import Course

from user_management_service.auth import Caller

DIRECTOR = "director"
TEACHER = "docente"
STUDENT = "estudiante"


def load_course(db: Session, course_id: int) -> Course:
    course = db.query(Course).filter(Course.id == course_id).first()
    if course is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ese curso no existe.",
        )
    return course


def resolve_course(db: Session, course_id: Optional[int]) -> Course:
    """El curso pedido, o el general si no se pidió ninguno.

    Así las rutas de siempre, que no conocían los cursos, siguen funcionando
    exactamente igual que antes: sobre el curso de todos.
    """
    return general_course(db) if course_id is None else load_course(db, course_id)


def owns(course: Course, caller: Caller) -> bool:
    return (
        not course.is_general
        and caller.user_id is not None
        and course.teacher_id == caller.user_id
    )


def can_edit(course: Course, caller: Caller) -> bool:
    if course.is_general:
        return caller.role == DIRECTOR
    return owns(course, caller)


def can_view(db: Session, course: Course, caller: Caller) -> bool:
    if course.is_general or caller.role == DIRECTOR or owns(course, caller):
        return True
    return is_enrolled(db, course.id, caller.user_id)


def can_see_students(course: Course, caller: Caller) -> bool:
    """Las respuestas y el avance de los estudiantes: su docente y la coordinación."""
    return caller.role == DIRECTOR or owns(course, caller)


def require_view(db: Session, course: Course, caller: Caller) -> Course:
    if not can_view(db, course, caller):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No estás inscrito en este curso. Pide el código a tu docente.",
        )
    return course


def require_edit(course: Course, caller: Caller) -> Course:
    if not can_edit(course, caller):
        detail = (
            "El curso general solo lo edita la coordinación. Edita uno de tus cursos."
            if course.is_general
            else "Solo el docente de este curso puede editarlo."
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    return course


def require_students_view(course: Course, caller: Caller) -> Course:
    if not can_see_students(course, caller):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo el docente de este curso y la coordinación ven a sus estudiantes.",
        )
    return course
