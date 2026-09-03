from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from psycopg_pool import ConnectionPool

from app.config import get_settings
from app.gis.errors import GisError
from app.gis.repository import SpatialRepository
from app.gis.router import router as gis_router
from app.gis.valhalla import ValhallaClient


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    http_client = httpx.AsyncClient(trust_env=False)
    pool = ConnectionPool(
        conninfo=settings.database_url,
        open=False,
        min_size=0,
        max_size=4,
        kwargs={"connect_timeout": 3},
    )
    try:
        pool.open()
        app.state.settings = settings
        app.state.http_client = http_client
        app.state.db_pool = pool
        app.state.valhalla_client = ValhallaClient(http_client, settings)
        app.state.spatial_repository = SpatialRepository(pool)
        yield
    finally:
        pool.close()
        await http_client.aclose()


app = FastAPI(title="HomeOrbit API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_origin_regex=r"^http://(?:127\.0\.0\.1|192\.168\.\d{1,3}\.\d{1,3}):3000$",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/gis/health")
async def health(request: Request):
    repository = getattr(request.app.state, "spatial_repository", None)
    settings = getattr(request.app.state, "settings", None)
    http_client = getattr(request.app.state, "http_client", None)
    if repository is None or settings is None or http_client is None:
        return {"status": "ok", "service": "homeorbit-api"}
    try:
        response = await http_client.get(
            f"{settings.valhalla_url.rstrip('/')}/status",
            timeout=min(settings.valhalla_timeout_seconds, 1.0),
        )
        valhalla_ready = response.status_code == 200
    except Exception:
        valhalla_ready = False
    components = {
        "api": "ready",
        "valhalla": "ready" if valhalla_ready else "unavailable",
        "postgis": "ready" if await repository.check() else "unavailable",
        "population_density": "ready" if settings.population_density_path.is_file() else "unavailable",
        "baseline": "ready" if await repository.baseline_ready() else "unavailable",
    }
    return {
        "status": "ok" if all(value == "ready" for value in components.values()) else "degraded",
        "service": "homeorbit-api",
        "components": components,
    }


@app.exception_handler(GisError)
async def handle_gis_error(request: Request, error: GisError) -> JSONResponse:
    return JSONResponse(status_code=error.status_code, content=error.as_dict())


@app.exception_handler(RequestValidationError)
async def handle_request_validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
    safe_error = GisError("POINT_OUTSIDE_COVERAGE", status_code=400)
    return JSONResponse(status_code=400, content=safe_error.as_dict())


app.include_router(gis_router)


def openapi_schema():
    if app.openapi_schema is None:
        schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
        schema["paths"]["/gis/site-analysis"]["post"]["responses"].pop("422", None)
        app.openapi_schema = schema
    return app.openapi_schema


app.openapi = openapi_schema
