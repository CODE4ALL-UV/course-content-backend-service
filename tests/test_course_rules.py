"""Los límites del temario editable y las reglas de quién puede qué en un curso.

Lo que se comprueba aquí son los casos que la aplicación no debería provocar
nunca, pero que un cliente roto o una petición hecha a mano sí: partes del
curso que no existen, contenido ilegible o enorme, y cambios en un curso que
no es de quien los pide. Cada uno tiene que acabar en una respuesta clara, no
en un error del servidor ni en un cambio que no tocaba.
"""

import itertools
import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from course_content_service.access import require_students_view, resolve_course
from course_content_service.main import app
from course_content_service.presentation.api.course_content_routes import MAX_PAYLOAD_BYTES
from neon_storage import SessionLocal
from neon_storage.courses import new_join_code
from neon_storage.models import Course, CourseOverride, Usuario
from user_management_service.auth import Caller
from user_management_service.core.security import create_access_token

client = TestClient(app)

# Lejos de los ids de las otras pruebas, que empiezan en 1000.
_ids = itertools.count(50_000)


def _user(rol: str) -> dict:
    """Una sesión de alguien distinto cada vez, con su propio id."""
    user_id = next(_ids)
    token = create_access_token(
        data={"sub": str(user_id), "email": f"{rol}{user_id}@example.com", "rol": rol}
    )
    return {"Authorization": f"Bearer {token}"}


def _create(teacher: dict, title: str = "Python 2026-2") -> dict:
    response = client.post("/api/courses", json={"title": title}, headers=teacher)
    assert response.status_code == 201, response.text
    return response.json()


def _general_id(who: dict) -> int:
    return client.get("/api/courses/mine", headers=who).json()["general_course_id"]


def _unused_section() -> str:
    return f"m9-{uuid.uuid4().hex[:8]}"


# --- el temario editable -----------------------------------------------------


def test_only_sections_and_modules_can_be_edited():
    director = _user("director")

    read = client.get("/api/course/overrides/lesson/m1")
    saved = client.put(
        "/api/course/overrides/lesson/m1", json={"content": {"title": "x"}}, headers=director
    )
    reverted = client.delete("/api/course/overrides/lesson/m1", headers=director)

    for response in (read, saved, reverted):
        assert response.status_code == 400
        assert "lesson" in response.json()["detail"]


def test_a_section_that_was_never_edited_is_a_404():
    response = client.get(f"/api/course/overrides/section/{_unused_section()}")

    assert response.status_code == 404


def test_an_edit_must_say_which_section_it_is():
    response = client.put(
        "/api/course/overrides/section/%20",
        json={"content": {"title": "Sin sección"}},
        headers=_user("director"),
    )

    assert response.status_code == 400


def test_a_section_that_is_too_big_is_not_saved():
    section = _unused_section()

    response = client.put(
        f"/api/course/overrides/section/{section}",
        json={"content": {"body": "x" * (MAX_PAYLOAD_BYTES + 1)}},
        headers=_user("director"),
    )

    assert response.status_code == 413
    assert client.get(f"/api/course/overrides/section/{section}").status_code == 404


def test_reverting_a_section_that_was_not_edited_changes_nothing():
    response = client.delete(
        f"/api/course/overrides/section/{_unused_section()}", headers=_user("director")
    )

    assert response.status_code == 200
    assert response.json()["reverted"] is False


def test_the_app_asks_whether_it_may_edit_before_showing_the_buttons():
    teacher = _user("docente")

    allowed = client.get("/api/course/can-edit", headers=teacher)

    assert allowed.status_code == 200
    assert allowed.json()["can_edit"] is True
    assert allowed.json()["rol"] == "docente"
    assert allowed.json()["email"].startswith("docente")
    assert client.get("/api/course/can-edit", headers=_user("estudiante")).status_code == 403
    assert client.get("/api/course/can-edit").status_code == 401


def test_a_teacher_reads_and_reverts_one_section_of_their_course():
    teacher, other = _user("docente"), _user("docente")
    course_id = _create(teacher)["id"]
    url = f"/api/courses/{course_id}/overrides/section/m1-s1"
    client.put(url, json={"content": {"title": "Variables"}}, headers=teacher)

    read = client.get(url, headers=teacher)
    assert read.status_code == 200
    assert read.json()["content"] == {"title": "Variables"}
    assert read.json()["course_id"] == course_id

    assert client.delete(url, headers=other).status_code == 403
    assert client.delete(url, headers=teacher).json()["reverted"] is True
    assert client.get(url, headers=teacher).status_code == 404


def test_an_unreadable_edit_does_not_break_the_list():
    teacher = _user("docente")
    course_id = _create(teacher)["id"]
    with SessionLocal() as db:
        db.add(
            CourseOverride(
                course_id=course_id, scope="section", target_id="m1-s1", payload="{roto"
            )
        )
        db.commit()

    listed = client.get(f"/api/courses/{course_id}/overrides", headers=teacher)

    assert listed.status_code == 200
    assert [item["content"] for item in listed.json()["items"]] == [{}]


def test_a_course_that_does_not_exist_is_a_404():
    response = client.get("/api/courses/987654321/overrides", headers=_user("docente"))

    assert response.status_code == 404


# --- los cursos ----------------------------------------------------------------


def test_a_course_shows_the_name_of_its_teacher():
    email = f"docente-{uuid.uuid4().hex[:8]}@example.com"
    with SessionLocal() as db:
        user = Usuario(nombre="Ana Docente", correo=email, password="x", rol="docente")
        db.add(user)
        db.commit()
        teacher_id = user.id_usuario
    token = create_access_token(data={"sub": str(teacher_id), "email": email, "rol": "docente"})

    course = _create({"Authorization": f"Bearer {token}"})

    assert course["teacher"] == {"id": teacher_id, "nombre": "Ana Docente"}


def test_a_course_needs_a_name():
    response = client.post("/api/courses", json={"title": "   "}, headers=_user("docente"))

    assert response.status_code == 400


def test_a_course_keeps_its_name_when_only_the_description_changes():
    teacher = _user("docente")
    course = _create(teacher, title="Python sabatino")

    blank = client.patch(f"/api/courses/{course['id']}", json={"title": "  "}, headers=teacher)
    described = client.patch(
        f"/api/courses/{course['id']}",
        json={"description": "  Grupo de los sábados.  "},
        headers=teacher,
    )

    assert blank.status_code == 400
    assert described.status_code == 200
    assert described.json()["title"] == "Python sabatino"
    assert described.json()["description"] == "Grupo de los sábados."


def test_only_its_teacher_or_the_coordination_renews_the_join_code():
    teacher = _user("docente")
    course = _create(teacher)

    stranger = client.post(f"/api/courses/{course['id']}/join-code", headers=_user("docente"))
    director = client.post(f"/api/courses/{course['id']}/join-code", headers=_user("director"))

    assert stranger.status_code == 403
    assert director.status_code == 200
    assert director.json()["join_code"] != course["join_code"]


def test_the_general_course_has_no_join_code_to_renew():
    director = _user("director")

    response = client.post(f"/api/courses/{_general_id(director)}/join-code", headers=director)

    assert response.status_code == 403


def test_nobody_leaves_or_is_removed_from_the_general_course():
    student, director = _user("estudiante"), _user("director")
    general_id = _general_id(director)

    left = client.delete(f"/api/courses/{general_id}/enrollment", headers=student)
    removed = client.delete(f"/api/courses/{general_id}/students/123", headers=director)

    assert left.status_code == 400
    assert removed.status_code == 400


def test_only_its_teacher_deletes_a_course():
    teacher = _user("docente")
    url = f"/api/courses/{_create(teacher)['id']}"

    assert client.delete(url, headers=_user("docente")).status_code == 403
    assert client.delete(url, headers=_user("director")).status_code == 403
    assert client.get(url, headers=teacher).status_code == 200


def test_the_general_course_cannot_be_deleted():
    director = _user("director")

    response = client.delete(f"/api/courses/{_general_id(director)}", headers=director)

    assert response.status_code == 403


# --- las reglas que comparten assessment y progress-tracking --------------------


@pytest.fixture
def owned_course():
    """Un curso del docente 777 con su sesión en la base temporal."""
    db = SessionLocal()
    course = Course(teacher_id=777, title="Python", join_code=new_join_code(db))
    db.add(course)
    db.commit()
    try:
        yield db, course
    finally:
        db.delete(course)
        db.commit()
        db.close()


def test_without_a_course_the_general_one_is_used(owned_course):
    db, course = owned_course

    assert resolve_course(db, None).is_general is True
    assert resolve_course(db, course.id).id == course.id


def test_a_missing_course_cannot_be_resolved(owned_course):
    db, _ = owned_course

    with pytest.raises(HTTPException) as refused:
        resolve_course(db, 987654321)

    assert refused.value.status_code == 404


def test_only_its_teacher_and_the_coordination_see_the_students(owned_course):
    db, course = owned_course
    general = resolve_course(db, None)

    assert require_students_view(course, Caller("d@example.com", "docente", 777)) is course
    assert require_students_view(course, Caller("c@example.com", "director", 1)) is course

    for caller, target in (
        (Caller("otro@example.com", "docente", 778), course),
        (Caller("e@example.com", "estudiante", 779), course),
        # El general no es de ningún docente, ni siquiera de quien tenga su id.
        (Caller("d@example.com", "docente", 777), general),
    ):
        with pytest.raises(HTTPException) as refused:
            require_students_view(target, caller)
        assert refused.value.status_code == 403
