"""Lo que este microservicio aporta al API Gateway.

El gateway (repo Back-end) llama a `register(app)` de cada servicio, y el
`main.py` de este paquete hace lo mismo para arrancarlo solo. Así las rutas se
declaran una sola vez, se arranque como se arranque.
"""

from fastapi import FastAPI

from course_content_service.presentation.api.course_content_routes import router as course_content_router
from course_content_service.presentation.api.modules_routes import router as modules_router


def register(app: FastAPI) -> None:
    app.include_router(modules_router)
    app.include_router(course_content_router)
