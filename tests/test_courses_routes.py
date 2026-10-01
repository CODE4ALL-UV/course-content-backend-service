"""Cada docente con sus cursos, y cada estudiante en los cursos de sus docentes.

Lo que no puede pasar: que lo que edita un docente cambie el curso de otro,
que un estudiante vea un curso al que no se inscribió, o que alguien que no es
su docente lo edite.
"""

import itertools

from fastapi.testclient import TestClient

from course_content_service.main import app
from user_management_service.core.security import create_access_token

client = TestClient(app)

_ids = itertools.count(1000)


def _user(rol: str) -> dict:
    """Una sesión de alguien distinto cada vez, con su propio id."""
    user_id = next(_ids)
    token = create_access_token(
        data={"sub": str(user_id), "email": f"{rol}{user_id}@example.com", "rol": rol}
    )
    return {"Authorization": f"Bearer {token}"}


def _create(teacher: dict, title: str = "Python 2026-1", **extra) -> dict:
    response = client.post("/api/courses", json={"title": title, **extra}, headers=teacher)
    assert response.status_code == 201, response.text
    return response.json()


def _edit(course_id: int, who: dict, title: str):
    return client.put(
        f"/api/courses/{course_id}/overrides/section/m1-s1",
        json={"content": {"title": title}},
        headers=who,
    )


def _titles(course_id: int, who: dict) -> list[str]:
    response = client.get(f"/api/courses/{course_id}/overrides", headers=who)
    assert response.status_code == 200, response.text
    return [item["content"].get("title") for item in response.json()["items"]]


def test_a_teacher_creates_a_course_with_its_own_join_code():
    teacher = _user("docente")

    course = _create(teacher)

    assert course["is_owner"] is True
    assert course["can_edit"] is True
    assert course["is_general"] is False
    assert len(course["join_code"]) == 6
    assert course["students"] == 0

    mine = client.get("/api/courses/mine", headers=teacher).json()["courses"]
    assert [c["id"] for c in mine] == [course["id"]]


def test_two_teachers_edit_the_same_section_without_stepping_on_each_other():
    ana, luis = _user("docente"), _user("docente")
    ana_course, luis_course = _create(ana), _create(luis)

    assert _edit(ana_course["id"], ana, "Variables, según Ana").status_code == 200
    assert _edit(luis_course["id"], luis, "Variables, según Luis").status_code == 200

    assert _titles(ana_course["id"], ana) == ["Variables, según Ana"]
    assert _titles(luis_course["id"], luis) == ["Variables, según Luis"]


def test_a_teacher_cannot_edit_someone_elses_course():
    owner, other = _user("docente"), _user("docente")
    course = _create(owner)

    assert _edit(course["id"], other, "Intruso").status_code == 403
    # Tampoco lo ve: no es suyo ni está inscrito.
    response = client.get(f"/api/courses/{course['id']}/overrides", headers=other)
    assert response.status_code == 403


def test_a_student_joins_with_the_code_and_then_sees_the_course():
    teacher, student = _user("docente"), _user("estudiante")
    course = _create(teacher)
    _edit(course["id"], teacher, "Editado por su docente")

    before = client.get(f"/api/courses/{course['id']}/overrides", headers=student)
    assert before.status_code == 403

    # Como se dicta en clase: con espacios y en minúsculas.
    typed = " ".join(course["join_code"].lower())
    joined = client.post("/api/courses/join", json={"code": typed}, headers=student)
    assert joined.status_code == 200, joined.text
    assert joined.json()["id"] == course["id"]
    # El estudiante no ve el código: con él cualquiera entraría.
    assert joined.json()["join_code"] is None

    assert _titles(course["id"], student) == ["Editado por su docente"]
    assert _edit(course["id"], student, "No").status_code == 403

    mine = client.get("/api/courses/mine", headers=student).json()["courses"]
    assert mine[0]["is_general"] is True
    assert course["id"] in [c["id"] for c in mine]

    # Su docente ya lo cuenta entre sus estudiantes.
    seen = client.get(f"/api/courses/{course['id']}", headers=teacher).json()
    assert seen["students"] == 1


def test_a_wrong_code_says_so():
    response = client.post(
        "/api/courses/join", json={"code": "NOEXIS"}, headers=_user("estudiante")
    )

    assert response.status_code == 404


def test_only_students_join_courses():
    course = _create(_user("docente"))

    response = client.post(
        "/api/courses/join", json={"code": course["join_code"]}, headers=_user("docente")
    )

    assert response.status_code == 400


def test_a_student_in_several_courses_sees_each_one():
    student = _user("estudiante")
    courses = [_create(_user("docente"), title=f"Grupo {n}") for n in range(3)]

    for course in courses:
        client.post("/api/courses/join", json={"code": course["join_code"]}, headers=student)

    mine = client.get("/api/courses/mine", headers=student).json()["courses"]
    assert [c["id"] for c in mine[1:]] == [c["id"] for c in courses]


def test_leaving_a_course_takes_it_away():
    teacher, student = _user("docente"), _user("estudiante")
    course = _create(teacher)
    client.post("/api/courses/join", json={"code": course["join_code"]}, headers=student)

    left = client.delete(f"/api/courses/{course['id']}/enrollment", headers=student)

    assert left.json()["left"] is True
    response = client.get(f"/api/courses/{course['id']}/overrides", headers=student)
    assert response.status_code == 403


def test_a_new_code_stops_the_old_one_but_keeps_who_was_in():
    teacher, inside, late = _user("docente"), _user("estudiante"), _user("estudiante")
    course = _create(teacher)
    client.post("/api/courses/join", json={"code": course["join_code"]}, headers=inside)

    renewed = client.post(f"/api/courses/{course['id']}/join-code", headers=teacher).json()

    assert renewed["join_code"] != course["join_code"]
    old = client.post("/api/courses/join", json={"code": course["join_code"]}, headers=late)
    assert old.status_code == 404
    still = client.get(f"/api/courses/{course['id']}/overrides", headers=inside)
    assert still.status_code == 200


def test_a_course_can_start_from_the_general_one():
    director, teacher = _user("director"), _user("docente")
    client.put(
        "/api/course/overrides/section/m2-s1",
        json={"content": {"title": "Del curso general"}},
        headers=director,
    )
    general_id = client.get("/api/courses/mine", headers=teacher).json()["general_course_id"]

    course = _create(teacher, copy_from=general_id)

    assert "Del curso general" in _titles(course["id"], teacher)
    # Copiar no ata: editar la copia no cambia el general.
    client.put(
        f"/api/courses/{course['id']}/overrides/section/m2-s1",
        json={"content": {"title": "Mi versión"}},
        headers=teacher,
    )
    general = client.get("/api/course/overrides").json()["items"]
    assert any(i["content"].get("title") == "Del curso general" for i in general)


def test_a_teacher_cannot_copy_someone_elses_course():
    other = _create(_user("docente"))

    response = client.post(
        "/api/courses",
        json={"title": "Copia", "copy_from": other["id"]},
        headers=_user("docente"),
    )

    assert response.status_code == 403


def test_the_coordination_sees_every_course_with_its_code():
    teacher, director = _user("docente"), _user("director")
    course = _create(teacher)

    mine = client.get("/api/courses/mine", headers=director).json()["courses"]
    seen = next(c for c in mine if c["id"] == course["id"])

    assert seen["join_code"] == course["join_code"]
    assert seen["can_edit"] is False


def test_an_unused_course_can_be_deleted_but_not_one_with_students():
    teacher = _user("docente")
    empty, used = _create(teacher), _create(teacher)
    client.post("/api/courses/join", json={"code": used["join_code"]}, headers=_user("estudiante"))

    assert client.delete(f"/api/courses/{empty['id']}", headers=teacher).status_code == 200
    assert client.delete(f"/api/courses/{used['id']}", headers=teacher).status_code == 409


def test_renaming_is_for_its_teacher():
    teacher = _user("docente")
    course = _create(teacher)

    renamed = client.patch(
        f"/api/courses/{course['id']}", json={"title": "Python nocturno"}, headers=teacher
    )
    refused = client.patch(
        f"/api/courses/{course['id']}", json={"title": "x"}, headers=_user("docente")
    )

    assert renamed.json()["title"] == "Python nocturno"
    assert refused.status_code == 403


def _student() -> tuple[int, dict]:
    user_id = next(_ids)
    token = create_access_token(
        data={"sub": str(user_id), "email": f"e{user_id}@example.com", "rol": "estudiante"}
    )
    return user_id, {"Authorization": f"Bearer {token}"}


def test_the_teacher_removes_a_student_from_their_course():
    teacher = _user("docente")
    student_id, student = _student()
    course = _create(teacher)
    client.post("/api/courses/join", json={"code": course["join_code"]}, headers=student)

    removed = client.delete(
        f"/api/courses/{course['id']}/students/{student_id}", headers=teacher
    )

    assert removed.status_code == 200
    seen = client.get(f"/api/courses/{course['id']}/overrides", headers=student)
    assert seen.status_code == 403
    # Con el código puede volver.
    back = client.post("/api/courses/join", json={"code": course["join_code"]}, headers=student)
    assert back.status_code == 200


def test_only_the_teacher_of_the_course_removes_students():
    teacher = _user("docente")
    student_id, student = _student()
    course = _create(teacher)
    client.post("/api/courses/join", json={"code": course["join_code"]}, headers=student)

    other = client.delete(
        f"/api/courses/{course['id']}/students/{student_id}", headers=_user("docente")
    )
    itself = client.delete(
        f"/api/courses/{course['id']}/students/{student_id}", headers=student
    )

    assert other.status_code == 403
    assert itself.status_code == 403


def test_removing_someone_who_is_not_there_says_so():
    teacher = _user("docente")
    course = _create(teacher)

    response = client.delete(f"/api/courses/{course['id']}/students/999999", headers=teacher)

    assert response.status_code == 404

