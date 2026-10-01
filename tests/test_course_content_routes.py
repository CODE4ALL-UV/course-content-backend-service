"""El temario editable, con los tokens que firma user-management.

Lo que más se arriesga al separar servicios es que la sesión deje de valer de
uno a otro. Por eso estas pruebas firman el token con el mismo código que usa
el inicio de sesión, en vez de saltarse la comprobación.
"""

import uuid

from fastapi.testclient import TestClient

from course_content_service.main import app
from user_management_service.core.security import create_access_token

client = TestClient(app)


def _auth(rol: str) -> dict:
    email = f"{rol}-{uuid.uuid4().hex[:8]}@example.com"
    token = create_access_token(data={"sub": "1", "email": email, "rol": rol})
    return {"Authorization": f"Bearer {token}"}


def test_reading_the_course_needs_no_session():
    response = client.get("/api/course/overrides")

    assert response.status_code == 200
    assert {"version", "count", "items"} <= set(response.json())


def test_editing_without_a_session_is_refused():
    response = client.put(
        "/api/course/overrides/section/m1-s1", json={"content": {"title": "x"}}
    )

    assert response.status_code == 401


def test_a_student_cannot_edit_the_course():
    response = client.put(
        "/api/course/overrides/section/m1-s1",
        json={"content": {"title": "x"}},
        headers=_auth("estudiante"),
    )

    assert response.status_code == 403


def test_a_teacher_edits_and_reverts_a_section():
    headers = _auth("docente")

    saved = client.put(
        "/api/course/overrides/section/m9-s9",
        json={"content": {"title": "Variables"}},
        headers=headers,
    )
    assert saved.status_code == 200
    assert saved.json()["content"] == {"title": "Variables"}

    listed = client.get("/api/course/overrides").json()["items"]
    assert any(item["target_id"] == "m9-s9" for item in listed)

    reverted = client.delete("/api/course/overrides/section/m9-s9", headers=headers)
    assert reverted.json()["reverted"] is True


def test_module_names_share_the_row_with_course_overrides():
    headers = _auth("director")

    created = client.post(
        "/api/modules/", json={"module_id": "07", "name": "Funciones"}, headers=headers
    )
    assert created.status_code == 201
    assert created.json()["id"] == "7"

    # El mismo cambio se ve por el otro camino: una sola verdad.
    override = client.get("/api/course/overrides/module/7").json()
    assert override["content"]["title"] == "Funciones"
