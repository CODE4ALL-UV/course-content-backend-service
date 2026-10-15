"""El nombre y los temas de cada módulo, guardados en la fila de CourseOverride.

Estas rutas trabajan sobre el Curso general, así que escribir es solo de la
coordinación. Cada prueba usa un número de módulo propio para no pisarse con
las demás, porque la base temporal es la misma para todas.
"""

import uuid

from fastapi.testclient import TestClient

from course_content_service.main import app
from course_content_service.presentation.api.modules_routes import MAX_PAYLOAD_BYTES, MODULE_SCOPE
from neon_storage import SessionLocal
from neon_storage.courses import general_course
from neon_storage.models import CourseOverride
from user_management_service.core.security import create_access_token

client = TestClient(app)


def _auth(rol: str) -> dict:
    email = f"{rol}-{uuid.uuid4().hex[:8]}@example.com"
    token = create_access_token(data={"sub": "1", "email": email, "rol": rol})
    return {"Authorization": f"Bearer {token}"}


def _module_number() -> str:
    """Un módulo que ninguna otra prueba ha tocado."""
    return str(10_000 + uuid.uuid4().int % 1_000_000)


def _rows(target_id: str) -> list[CourseOverride]:
    with SessionLocal() as db:
        return (
            db.query(CourseOverride)
            .filter(
                CourseOverride.course_id == general_course(db).id,
                CourseOverride.scope == MODULE_SCOPE,
                CourseOverride.target_id == target_id,
            )
            .all()
        )


def test_a_module_is_named_by_its_number():
    director = _auth("director")

    assert client.get("/api/modules/abc").status_code == 400
    assert client.get("/api/modules/0").status_code == 400
    refused = client.post("/api/modules/", json={"module_id": "uno", "name": "x"}, headers=director)
    assert refused.status_code == 400
    assert "uno" in refused.json()["detail"]


def test_a_module_that_was_never_edited_is_a_404():
    module = _module_number()

    assert client.get(f"/api/modules/{module}").status_code == 404
    patched = client.patch(f"/api/modules/{module}", json={"name": "x"}, headers=_auth("director"))
    assert patched.status_code == 404
    assert _rows(module) == []


def test_reading_a_module_needs_no_session():
    module = _module_number()
    client.post(
        "/api/modules/",
        json={"module_id": module, "name": " Listas ", "topics": ["append", "pop"]},
        headers=_auth("director"),
    )

    response = client.get(f"/api/modules/{module}")

    assert response.status_code == 200
    assert response.json() == {"id": module, "name": "Listas", "topics": ["append", "pop"]}


def test_saving_a_module_again_replaces_it_in_the_same_row():
    module = _module_number()
    director = _auth("director")

    client.post("/api/modules/", json={"module_id": module, "name": "Primero"}, headers=director)
    again = client.post(
        "/api/modules/",
        json={"module_id": module, "name": "Segundo", "topics": ["for"]},
        headers=director,
    )

    assert again.status_code == 201
    assert again.json() == {"id": module, "name": "Segundo", "topics": ["for"]}
    assert len(_rows(module)) == 1


def test_a_patch_changes_only_what_it_sends():
    module = _module_number()
    director = _auth("director")
    client.post(
        "/api/modules/",
        json={"module_id": module, "name": "Listas", "topics": ["append", "pop"]},
        headers=director,
    )

    renamed = client.patch(f"/api/modules/{module}", json={"name": " Tuplas "}, headers=director)
    assert renamed.status_code == 200
    assert renamed.json() == {"id": module, "name": "Tuplas", "topics": ["append", "pop"]}

    retopiced = client.patch(f"/api/modules/{module}", json={"topics": ["index"]}, headers=director)
    assert retopiced.json() == {"id": module, "name": "Tuplas", "topics": ["index"]}


def test_a_module_with_too_much_text_is_not_saved():
    module = _module_number()

    response = client.post(
        "/api/modules/",
        json={"module_id": module, "name": "x" * (MAX_PAYLOAD_BYTES + 1)},
        headers=_auth("director"),
    )

    assert response.status_code == 413
    assert _rows(module) == []


def test_only_the_coordination_renames_the_general_modules():
    module = _module_number()
    body = {"module_id": module, "name": "Intento"}

    assert client.post("/api/modules/", json=body).status_code == 401
    assert client.post("/api/modules/", json=body, headers=_auth("estudiante")).status_code == 403
    assert client.post("/api/modules/", json=body, headers=_auth("docente")).status_code == 403
    assert _rows(module) == []


def test_an_unreadable_module_reads_as_empty_instead_of_failing():
    broken, not_an_object = _module_number(), _module_number()
    with SessionLocal() as db:
        course_id = general_course(db).id
        for target_id, payload in ((broken, "{roto"), (not_an_object, '["no", "objeto"]')):
            db.add(
                CourseOverride(
                    course_id=course_id, scope=MODULE_SCOPE, target_id=target_id, payload=payload
                )
            )
        db.commit()

    try:
        for module in (broken, not_an_object):
            response = client.get(f"/api/modules/{module}")
            assert response.status_code == 200
            assert response.json() == {"id": module, "name": "", "topics": []}
    finally:
        with SessionLocal() as db:
            db.query(CourseOverride).filter(
                CourseOverride.course_id == course_id,
                CourseOverride.target_id.in_([broken, not_an_object]),
            ).delete(synchronize_session=False)
            db.commit()
