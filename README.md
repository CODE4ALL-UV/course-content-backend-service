# course-content-backend-service
Repositorio Back-end para el módulo Curso y Contenidos de Python.

Guarda lo que el docente edita del temario. El curso de fábrica vive dentro de
la aplicación; aquí solo está lo cambiado, así que si este servicio se cae el
estudiante sigue viendo el curso original.

| Rutas | Quién |
|---|---|
| `GET /api/course/overrides`, `GET /api/course/overrides/{scope}/{target_id}` | Cualquiera |
| `PUT` y `DELETE /api/course/overrides/{scope}/{target_id}` | Docente o director |
| `GET /api/course/can-edit` | Docente o director |
| `GET /api/modules/{module_id}` | Cualquiera |
| `POST /api/modules/`, `PATCH /api/modules/{module_id}` | Docente o director |

**Tabla que escribe:** `CourseOverride`. Los nombres de módulo van en la
misma tabla (`scope = module`), así que `/api/modules` y `/api/course` son dos
caminos a los mismos datos.

**Sesión:** el rol se lee del token que firma user-management, con
`user_management_service.auth`.

## Cómo lo monta el gateway

`course_content_service/routes.py` tiene `register(app)`, que añade las rutas de este
servicio a una aplicación de FastAPI. El gateway lo llama para cada servicio, y
`course_content_service/main.py` hace lo mismo para arrancarlo solo.

## Correrlo

Lo normal es correrlo dentro del gateway (repo `Back-end`), que monta todos
los servicios juntos. Para correrlo solo hacen falta `neon-storage` y
`user-management` clonados al lado:

```powershell
$env:PYTHONPATH = "..\neon-storage-backend-service;..\user-management-backend-service"
pip install -r requirements.txt -r ..\neon-storage-backend-service\requirements.txt -r ..\user-management-backend-service\requirements.txt
copy .env.example .env
uvicorn course_content_service.main:app --reload
```

## Pruebas

```bash
pytest tests
```

No tocan Neon: usan una base SQLite temporal. Buscan `neon-storage` y
`user-management` en los repos hermanos, así que funcionan igual con los repos
clonados uno al lado del otro que dentro de `services/` del gateway.
