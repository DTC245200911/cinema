from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.config import STATIC_DIR
from backend.app.database import initialize_database
from backend.app.routers import ai, maintenance, movies, pages, showtimes, tickets


@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_database()
    yield


def create_app():
    application = FastAPI(title="Cinema AI Management", lifespan=lifespan)

    @application.middleware("http")
    async def security_headers_and_origin_check(request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            referer = request.headers.get("referer")
            source = origin or referer
            response = None
            if source:
                try:
                    source_host = urlsplit(source).netloc.casefold()
                except ValueError:
                    source_host = ""
                request_host = request.headers.get("host", "").casefold()
                if not source_host or source_host != request_host:
                    response = JSONResponse(
                        {"error": "Yêu cầu khác nguồn bị từ chối."},
                        status_code=403,
                    )

            if response is None:
                response = await call_next(request)
        else:
            response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        return response

    application.mount(
        "/static",
        StaticFiles(directory=STATIC_DIR),
        name="static",
    )
    application.include_router(pages.router)
    application.include_router(movies.router)
    application.include_router(showtimes.router)
    application.include_router(tickets.router)
    application.include_router(ai.router)
    application.include_router(maintenance.router)
    return application


app = create_app()
