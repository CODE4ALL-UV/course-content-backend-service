# course-content-backend-service · Curso y contenidos de Python

Este es el microservicio de **curso y contenidos** de Code4All. Se encarga de
dos cosas:

1. **Los cursos.** Cada docente puede tener sus propios cursos, con un código
   de 6 caracteres que les da a sus estudiantes para inscribirse. Además existe
   un **Curso general** que ven todos los estudiantes sin inscribirse.
2. **Lo que el docente cambia del temario.** Cuando un docente edita una
   sección del curso de Python (el texto, un ejemplo, una pregunta), aquí se
   guarda esa versión editada, aparte para cada curso.

## Una idea importante: aquí no está el temario

El curso de Python «de fábrica» (los módulos, las secciones, las lecturas, los
ejemplos y los quices) vive **dentro de la app**
([Front-end](https://github.com/CODE4ALL-UV/Front-end)). Este servicio solo
guarda **lo que se ha cambiado**. A cada cambio le llamamos *override*: la app
carga el curso original y encima pone las ediciones que le devuelve este
servicio.

Esto tiene una ventaja práctica: si el servicio se cae o tarda, el estudiante
sigue viendo el curso original y puede seguir estudiando.

Las secciones se identifican como `m1-s2` (módulo 1, sección 2) y los módulos
por su número (`1`, `2`…).

## Quién puede hacer qué

Las reglas están en un solo lugar, `course_content_service/access.py`, y el
servicio de evaluación
([assessment](https://github.com/CODE4ALL-UV/assessment-backend-service)) usa
esas mismas reglas.

| | Curso general | Curso de un docente |
|---|---|---|
| **Verlo** | Todos | Su docente, la dirección y los estudiantes inscritos |
| **Editar su contenido** | Solo la dirección (rol `director`) | Solo su docente |
| **Ver el código y cuántos estudiantes tiene** | Solo la dirección (no tiene código visible) | Su docente y la dirección |
| **Renovar el código o sacar a un estudiante** | No aplica | Su docente y la dirección |
| **Borrarlo** | No se puede | Solo su docente, y solo si está vacío |

Fíjense en que la dirección **no edita** los cursos de los docentes: los ve,
pero el contenido de cada curso es responsabilidad de su docente.

El rol se lee del token que firma
[user-management](https://github.com/CODE4ALL-UV/user-management-backend-service),
con `Authorization: Bearer <token>`.

## 1. Cursos (`/api/courses`)

### Crear un curso — `POST /api/courses`

Lo puede hacer un docente o la dirección. Responde 201.

```json
{ "title": "Python 10A - Mañana", "description": "Grupo de la profe Ana", "copy_from": 1 }
```

- `title` es obligatorio (hasta 120 caracteres). `description` es opcional
  (hasta 1000).
- `copy_from` es opcional: el id de un curso del que copiar todas las
  ediciones. Solo se puede copiar el Curso general o un curso propio. Sirve
  para arrancar un curso nuevo a partir de otro sin empezar de cero. No se
  copian los estudiantes.
- Al crearlo se le genera el **código de inscripción**.

### El código de inscripción

- Tiene **6 caracteres**, por ejemplo `K7MP3X`.
- Usa solo letras y números que no se confunden al dictarlos: no lleva `0`,
  `O`, `1`, `I` ni `L`. Así el docente lo puede decir en voz alta en clase o
  un estudiante con lector de pantalla lo puede oír sin dudas.
- Se genera al azar con `secrets` y se comprueba que no lo tenga otro curso.
- Al escribirlo da igual usar mayúsculas o minúsculas, o meter espacios:
  `k7m p3x` vale lo mismo que `K7MP3X`.

### Inscribirse — `POST /api/courses/join`

```json
{ "code": "K7MP3X" }
```

Solo lo pueden hacer estudiantes. Si el estudiante ya estaba inscrito, no pasa
nada (no se duplica). Si el código no existe, responde 404 con «Ese código no
es de ningún curso. Revisa que esté bien escrito.».

### Mis cursos — `GET /api/courses/mine`

Lo que devuelve depende de quién pregunta:

- **Estudiante:** el Curso general más los cursos en los que se inscribió.
- **Docente:** los cursos que creó (el Curso general no sale en su lista).
- **Dirección:** todos los cursos.

Cada curso viene con `id`, `title`, `description`, `is_general`, el docente
(`teacher`), `is_owner`, `can_edit`, `join_code` y `students`. El código y el
número de estudiantes solo vienen con valor si quien pregunta tiene permiso
para verlos; si no, llegan en `null`. También se devuelve `general_course_id`,
para que la app pueda ofrecer «crear un curso copiando el general».

### El resto de rutas de cursos

| Ruta | Qué hace | Quién |
|---|---|---|
| `GET /api/courses/{id}` | Los datos de un curso. | Quien lo puede ver |
| `PATCH /api/courses/{id}` | Cambia el nombre o la descripción (**renombrar**). | Su docente; en el general, la dirección |
| `POST /api/courses/{id}/join-code` | Genera un código nuevo. Los inscritos siguen inscritos y el código viejo deja de servir. Útil si el código se filtró. | Su docente o la dirección |
| `DELETE /api/courses/{id}/enrollment` | El estudiante se sale del curso. Del Curso general no se puede salir. | El propio estudiante |
| `DELETE /api/courses/{id}/students/{student_id}` | El docente saca a un estudiante. | Su docente o la dirección |
| `DELETE /api/courses/{id}` | Borra el curso. Si ya tiene estudiantes o respuestas, responde **409** y no lo borra. | Solo su docente |

Salir de un curso o ser sacado **no borra el avance** del estudiante: sus
respuestas se quedan guardadas y, si vuelve a entrar con el código, todo sigue
ahí. La lista de estudiantes de un curso, con su id, la da
`GET /api/analytics/students` en assessment.

Al borrar un curso vacío se borran también sus ediciones y las revisiones que
la dirección haya hecho de su contenido.

## 2. El contenido de cada curso (`/api/courses/{id}/overrides`)

| Ruta | Qué hace | Quién |
|---|---|---|
| `GET /api/courses/{id}/overrides` | Todas las ediciones del curso. | Quien lo puede ver |
| `GET /api/courses/{id}/overrides/{scope}/{target_id}` | Una edición concreta. 404 si esa parte no se ha editado. | Quien lo puede ver |
| `PUT /api/courses/{id}/overrides/{scope}/{target_id}` | Guarda una sección o un módulo editado. | Quien lo puede editar |
| `DELETE /api/courses/{id}/overrides/{scope}/{target_id}` | **Revierte**: borra la edición y vuelve a verse la versión original. | Quien lo puede editar |

- `scope` es `section` (una sección, como `m1-s2`) o `module` (un módulo, como
  `1`).
- Al guardar se manda `{ "content": { ... } }` con el contenido **completo** de
  la sección, no solo lo que cambió. El contenido es JSON libre: su forma la
  decide la app.
- Cada sección editada puede ocupar hasta **512 KB**. Si se pasa, responde 413.
- Se guarda quién la editó (su correo) y cuándo. Con eso la dirección puede
  ver qué hizo cada docente en
  [progress-tracking](https://github.com/CODE4ALL-UV/progress-tracking-backend-service).

La lista de ediciones trae un campo `version` (la fecha de la última edición)
para que la app sepa si algo cambió, y la cabecera `Cache-Control: no-store`
para que el navegador no le muestre una versión vieja.

```json
{
  "course_id": 7,
  "version": "2026-10-01T15:20:11+00:00",
  "count": 1,
  "items": [
    {
      "scope": "section",
      "target_id": "m1-s2",
      "content": { "title": "Variables", "...": "..." },
      "course_id": 7,
      "updated_by": "ana@colegio.edu.co",
      "updated_at": "2026-10-01T15:20:11+00:00"
    }
  ]
}
```

Dos docentes pueden editar la misma sección (`m1-s2`), cada uno en su curso,
sin pisarse.

## 3. Las rutas de antes, sobre el Curso general

Antes de que existieran los cursos por docente había un solo curso. Estas
rutas siguen funcionando igual que en el monolito para no romper nada, y
trabajan siempre sobre el **Curso general**:

| Ruta | Qué hace | Quién |
|---|---|---|
| `GET /api/course/overrides` | Las ediciones del Curso general. | Cualquiera, sin sesión |
| `GET /api/course/overrides/{scope}/{target_id}` | Una edición del Curso general. | Cualquiera |
| `PUT` y `DELETE /api/course/overrides/{scope}/{target_id}` | Editar o revertir el Curso general. | Solo la dirección |
| `GET /api/course/can-edit` | Si quien pregunta es docente o director. | Docente o director |
| `POST /api/modules/` | Pone nombre y temas a un módulo: `{ "module_id": "3", "name": "Bucles", "topics": ["for", "while"] }`. | Solo la dirección |
| `GET /api/modules/{module_id}` | El nombre y los temas de un módulo. | Cualquiera |
| `PATCH /api/modules/{module_id}` | Cambia solo lo que se mande del módulo. | Solo la dirección |

Los nombres de módulo se guardan en la misma tabla que las ediciones (con
`scope = module`), así que `/api/modules/3` y
`/api/course/overrides/module/3` son dos caminos a los mismos datos. El número
de módulo se normaliza: `"07"` se guarda como `"7"`.

> Antes, los módulos se guardaban como archivos JSON en `uploads/modules/`. Se
> dejó de hacer porque el disco de Render se borra en cada despliegue. Ahora
> todo está en la base de datos.

## Dónde lo usa la app

En el [Front-end](https://github.com/CODE4ALL-UV/Front-end):

- `lib/data/course/my_courses_store.dart`: mis cursos, crear, inscribirse,
  renombrar, código, sacar estudiantes.
- `lib/data/course/course_content_store.dart`: carga las ediciones del curso
  que se está viendo (`/api/courses/{id}/overrides` o, contra el servidor
  anterior, `/api/course/overrides`).
- `lib/ui/users_management/widgets/teacher_module_editor_screen.dart`: el
  editor de módulos (`/api/modules`).

Si `/api/courses/mine` responde 404, la app entiende que habla con el servidor
anterior (el monolito) y sigue con un solo curso.

## Tablas que usa

Están definidas en
[neon-storage](https://github.com/CODE4ALL-UV/neon-storage-backend-service).

| Tabla | Qué hace con ella |
|---|---|
| `Course` | Crea, renombra, cambia el código y borra cursos. Si el Curso general no existe todavía, lo crea. |
| `CourseEnrollment` | Inscribe, saca y cuenta estudiantes. |
| `CourseOverride` | Guarda, lee, copia y revierte las ediciones de cada curso. |
| `Usuario` | Lee el nombre del docente y cuenta los estudiantes del Curso general. |
| `QuizAnswer`, `ActivityCompletion` | Solo mira si hay datos antes de dejar borrar un curso. |
| `ContentReview` | Borra las revisiones de un curso cuando se borra el curso. |

## Cómo lo monta el gateway

Todos los servicios de Code4All siguen el mismo contrato:
`course_content_service/routes.py` tiene una función `register(app)` que añade
las rutas de este servicio a una aplicación de FastAPI. El
[gateway](https://github.com/CODE4ALL-UV/Back-end) trae este repositorio como
submódulo en `services/` y llama a esa función al arrancar.
`course_content_service/main.py` hace lo mismo para correrlo solo.

Depende de **neon-storage** (la base de datos) y de **user-management** (la
sesión).

## Estructura

```
course_content_service/
├── routes.py                        register(app): lo único que llama el gateway
├── main.py                          arranque independiente (lee el .env y prepara la base)
├── access.py                        quién puede ver, editar y administrar cada curso
└── presentation/api/
    ├── courses_routes.py            /api/courses: cursos, código, inscripción, estudiantes
    ├── course_content_routes.py     las ediciones, por curso y las de antes (/api/course)
    └── modules_routes.py            /api/modules: nombre y temas de cada módulo
tests/
├── conftest.py
├── test_courses_routes.py
└── test_course_content_routes.py
```

## Variables de entorno

| Variable | Para qué | ¿Obligatoria? |
|---|---|---|
| `DATABASE_URL` | La cadena de conexión de Neon. | Sí |
| `SECRET_KEY` | Para comprobar los tokens. Tiene que ser **la misma** que usa user-management. Si falta se usa una clave por defecto que no sirve para producción. | Sí |
| `ALGORITHM` | Algoritmo del token. Por defecto `HS256`. | No |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Duración del token. Aquí solo se usa en las pruebas. | No |

Están en `.env.example`.

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

```powershell
pytest tests
```

No tocan Neon: `conftest.py` fuerza una base SQLite temporal. Buscan
`neon-storage` y `user-management` en los repos hermanos, así que funcionan
igual con los repos clonados uno al lado del otro que dentro de `services/`
del gateway.

- `test_courses_routes.py` (17 pruebas): el docente crea su curso con un
  código de 6 caracteres; dos docentes editan la misma sección sin pisarse; un
  docente no ve ni edita el curso de otro; el estudiante se inscribe con el
  código en minúsculas y con espacios, ve las ediciones pero no el código ni
  puede editar; un código errado da 404; un estudiante en varios cursos; salir
  del curso; renovar el código; copiar del Curso general; no se puede copiar
  un curso ajeno; la dirección ve todos los cursos; no se borra un curso con
  estudiantes; solo el dueño renombra; sacar a un estudiante y que pueda
  volver.
- `test_course_content_routes.py` (6 pruebas): el Curso general se lee sin
  sesión; editar sin sesión da 401; un estudiante no edita; un docente no
  edita el general; la dirección edita y revierte; el número de módulo `"07"`
  se guarda como `"7"`.

Además, `tests/test_gateway_flow.py` del gateway prueba el recorrido completo
con assessment: el docente crea y edita un curso, el estudiante se inscribe,
ve la edición y responde, y el docente ve la respuesta.

En GitHub, cada push o pull request a `main` corre las pruebas con cobertura y
la sube a Codacy (`.github/workflows/codacycoverage.yml`).

## Cosas a tener en cuenta

- `GET /api/course/can-edit` le responde `true` a un docente, aunque desde que
  existen los cursos por docente ya no puede editar el Curso general.
- `POST /api/modules/` reemplaza todo lo que había guardado del módulo;
  `PATCH` solo cambia lo que se manda.
- Algunos `GET` sin sesión (como `/api/course/overrides`) crean el Curso
  general si todavía no existe.
- Las mismas rutas de antes (`/api/course/*`, `/api/modules/*`) siguen en el
  monolito de user-management mientras no se haga el corte en Render. Lo que
  el monolito guarde sin curso se asigna al Curso general al arrancar el
  gateway.
- Si se añade una ruta nueva hay que agregarla también a
  `tests/test_route_parity.py` del gateway.

## Los repositorios de Code4All

| Parte | Repositorio |
|---|---|
| App (Flutter) | [Front-end](https://github.com/CODE4ALL-UV/Front-end) |
| API Gateway | [Back-end](https://github.com/CODE4ALL-UV/Back-end) |
| Gestión de usuarios | [user-management-backend-service](https://github.com/CODE4ALL-UV/user-management-backend-service) |
| **Curso y contenidos de Python** | **este repositorio** |
| Ejercicios y evaluación | [assessment-backend-service](https://github.com/CODE4ALL-UV/assessment-backend-service) |
| Progreso y seguimiento | [progress-tracking-backend-service](https://github.com/CODE4ALL-UV/progress-tracking-backend-service) |
| Accesibilidad y adaptación | [accessibility-backend-service](https://github.com/CODE4ALL-UV/accessibility-backend-service) |
| Interacción multimodal | [multimodal-interaction-backend-service](https://github.com/CODE4ALL-UV/multimodal-interaction-backend-service) |
| Infraestructura y dispositivos | [device-management-backend-service](https://github.com/CODE4ALL-UV/device-management-backend-service) |
| Capa de datos compartida (Neon) | [neon-storage-backend-service](https://github.com/CODE4ALL-UV/neon-storage-backend-service) |
