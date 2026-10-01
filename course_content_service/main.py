"""Arranca solo este microservicio, sin el gateway.

Sirve para desarrollarlo o desplegarlo por separado. En producción lo monta el
API Gateway (repo Back-end) junto a los demás. Necesita neon-storage y
user-management clonados al lado y en el PYTHONPATH:

    uvicorn course_content_service.main:app --reload
"""

from dotenv import load_dotenv

# El .env de este repositorio, antes de importar nada que lea variables.
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from neon_storage import prepare_database
from course_content_service.routes import register

prepare_database()

app = FastAPI(title="Course Content Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register(app)
