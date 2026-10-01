"""Los cursos de cada docente y la inscripción de los estudiantes.

Cada docente tiene los cursos que quiera: los crea aquí, los edita a su manera
y comparte el código de inscripción con sus estudiantes. El estudiante escribe
ese código y el curso aparece entre los suyos, junto al Curso general, que ve
todo el mundo sin inscribirse.

Qué ve cada uno en «mis cursos»:

- estudiante: el Curso general y los cursos en los que se inscribió;
- docente: los cursos que creó;
- coordinación: todos.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from neon_storage import get_db
from neon_storage.courses import (
    general_course,
    is_enrolled,
    new_join_code,
    normalize_join_code,
)
from neon_storage.models import (
    ActivityCompletion,
    ContentReview,
    Course,
    CourseEnrollment,
    CourseOverride,
    QuizAnswer,
    Usuario,
)

from course_content_service.access import (
    DIRECTOR,
    STUDENT,
    can_edit,
    can_see_students,
    load_course,
    owns,
    require_edit,
    require_view,
)
from user_management_service.auth import Caller, current_caller, require_course_editor

router = APIRouter(prefix="/api/courses", tags=["Cursos"])

MAX_TITLE = 120
MAX_DESCRIPTION = 1000


class CourseIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=MAX_TITLE)
    description: str = Field(default="", max_length=MAX_DESCRIPTION)
    # Empezar con las ediciones de otro curso: el general o uno propio. Sirve
    # para no perder lo que el docente ya había editado en el curso de todos,
    # o para abrir el grupo del semestre siguiente a partir del anterior.
    copy_from: Optional[int] = None


class CourseUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=MAX_TITLE)
    description: Optional[str] = Field(default=None, max_length=MAX_DESCRIPTION)


class JoinIn(BaseModel):
    code: str


def _student_count(db: Session, course: Course) -> int:
    if course.is_general:
        return (
            db.query(func.count(Usuario.id_usuario))
            .filter(func.lower(Usuario.rol) == STUDENT)
            .scalar()
            or 0
        )
    return (
        db.query(func.count(CourseEnrollment.id))
        .filter(CourseEnrollment.course_id == course.id)
        .scalar()
        or 0
    )


def course_out(db: Session, course: Course, caller: Caller) -> dict:
    """Un curso, con lo que quien pregunta puede saber de él.

    El código de inscripción solo lo ven su docente y la coordinación: con él
    cualquiera podría entrar al curso.
    """
    teacher = None
    if course.teacher_id is not None:
        user = db.query(Usuario).filter(Usuario.id_usuario == course.teacher_id).first()
        if user is not None:
            teacher = {"id": user.id_usuario, "nombre": user.nombre}

    manages = owns(course, caller) or caller.role == DIRECTOR

    return {
        "id": course.id,
        "title": course.title,
        "description": course.description or "",
        "is_general": bool(course.is_general),
        "teacher": teacher,
        "is_owner": owns(course, caller),
        "can_edit": can_edit(course, caller),
        "join_code": course.join_code if manages and not course.is_general else None,
        "students": _student_count(db, course) if can_see_students(course, caller) else None,
        "created_at": course.created_at.isoformat() if course.created_at else None,
    }


@router.get("/mine")
def my_courses(db: Session = Depends(get_db), caller: Caller = Depends(current_caller)):
    general = general_course(db)

    if caller.role == DIRECTOR:
        courses = db.query(Course).order_by(Course.is_general.desc(), Course.created_at).all()
    elif caller.role == STUDENT:
        enrolled = (
            db.query(Course)
            .join(CourseEnrollment, CourseEnrollment.course_id == Course.id)
            .filter(CourseEnrollment.student_id == caller.user_id)
            .order_by(CourseEnrollment.enrolled_at)
            .all()
        )
        courses = [general] + [c for c in enrolled if c.id != general.id]
    else:
        courses = (
            db.query(Course)
            .filter(Course.teacher_id == caller.user_id, Course.is_general.is_(False))
            .order_by(Course.created_at)
            .all()
        )

    items = [course_out(db, course, caller) for course in courses]
    # El docente no tiene el general entre los suyos, pero puede crear un
    # curso a partir de él: para eso necesita saber cuál es.
    return {"count": len(items), "general_course_id": general.id, "courses": items}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_course(
    payload: CourseIn,
    db: Session = Depends(get_db),
    caller: Caller = Depends(require_course_editor),
):
    """Un curso nuevo del docente que lo crea.

    Empieza con el temario de fábrica, o con las ediciones del curso que se
    indique en `copy_from`.
    """
    title = payload.title.strip()
    if not title:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ponle un nombre al curso.",
        )

    source = None
    if payload.copy_from is not None:
        source = load_course(db, payload.copy_from)
        if not (source.is_general or owns(source, caller)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Solo puedes copiar el curso general o uno de tus cursos.",
            )

    course = Course(
        teacher_id=caller.user_id,
        title=title,
        description=payload.description.strip(),
        join_code=new_join_code(db),
        is_general=False,
    )
    db.add(course)
    db.flush()

    if source is not None:
        for row in db.query(CourseOverride).filter(CourseOverride.course_id == source.id):
            db.add(
                CourseOverride(
                    course_id=course.id,
                    scope=row.scope,
                    target_id=row.target_id,
                    payload=row.payload,
                    updated_by=caller.email,
                )
            )

    db.commit()
    db.refresh(course)
    return course_out(db, course, caller)


@router.post("/join")
def join_course(
    payload: JoinIn,
    db: Session = Depends(get_db),
    caller: Caller = Depends(current_caller),
):
    """El estudiante entra a un curso con el código que le dio su docente."""
    if caller.role != STUDENT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Solo los estudiantes se inscriben en un curso.",
        )

    code = normalize_join_code(payload.code)
    course = db.query(Course).filter(Course.join_code == code).first() if code else None
    if course is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ese código no es de ningún curso. Revisa que esté bien escrito.",
        )

    if not course.is_general and not is_enrolled(db, course.id, caller.user_id):
        db.add(CourseEnrollment(course_id=course.id, student_id=caller.user_id))
        db.commit()

    return course_out(db, course, caller)


@router.get("/{course_id}")
def get_course(
    course_id: int,
    db: Session = Depends(get_db),
    caller: Caller = Depends(current_caller),
):
    course = require_view(db, load_course(db, course_id), caller)
    return course_out(db, course, caller)


@router.patch("/{course_id}")
def update_course(
    course_id: int,
    payload: CourseUpdate,
    db: Session = Depends(get_db),
    caller: Caller = Depends(current_caller),
):
    course = require_edit(load_course(db, course_id), caller)

    if payload.title is not None:
        title = payload.title.strip()
        if not title:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El curso necesita un nombre.",
            )
        course.title = title
    if payload.description is not None:
        course.description = payload.description.strip()

    db.commit()
    db.refresh(course)
    return course_out(db, course, caller)


@router.post("/{course_id}/join-code")
def renew_join_code(
    course_id: int,
    db: Session = Depends(get_db),
    caller: Caller = Depends(current_caller),
):
    """Un código nuevo, por si el anterior circuló donde no debía.

    Quien ya estaba inscrito sigue inscrito: el código solo sirve para entrar.
    """
    course = load_course(db, course_id)
    if course.is_general or not (owns(course, caller) or caller.role == DIRECTOR):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo el docente del curso puede cambiar su código.",
        )

    course.join_code = new_join_code(db)
    db.commit()
    db.refresh(course)
    return course_out(db, course, caller)


@router.delete("/{course_id}/enrollment")
def leave_course(
    course_id: int,
    db: Session = Depends(get_db),
    caller: Caller = Depends(current_caller),
):
    """El estudiante sale de un curso. Su avance se conserva por si vuelve."""
    course = load_course(db, course_id)
    if course.is_general:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Del curso general no se sale: es de todos los estudiantes.",
        )

    removed = (
        db.query(CourseEnrollment)
        .filter(
            CourseEnrollment.course_id == course.id,
            CourseEnrollment.student_id == caller.user_id,
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    return {"left": bool(removed)}


@router.delete("/{course_id}")
def delete_course(
    course_id: int,
    db: Session = Depends(get_db),
    caller: Caller = Depends(current_caller),
):
    """Borra un curso que todavía no ha usado nadie.

    Con estudiantes inscritos o con respuestas guardadas no se borra: se
    perdería su trabajo. Sirve para deshacer un curso creado por error.
    """
    course = load_course(db, course_id)
    if course.is_general or not owns(course, caller):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo el docente del curso puede borrarlo.",
        )

    in_use = any(
        db.query(model.id).filter(model.course_id == course.id).first() is not None
        for model in (CourseEnrollment, QuizAnswer, ActivityCompletion)
    )
    if in_use:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Este curso ya tiene estudiantes o respuestas; no se puede borrar.",
        )

    db.query(CourseOverride).filter(CourseOverride.course_id == course.id).delete(
        synchronize_session=False
    )
    db.query(ContentReview).filter(ContentReview.course_id == course.id).delete(
        synchronize_session=False
    )
    db.delete(course)
    db.commit()
    return {"deleted": True}
