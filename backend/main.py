from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import uvicorn
from app.config import get_settings
from app.env_validation import validate_settings
from app.logger import setup_logger
from app.api.routes import router
from app.api.auth_routes import auth_router

logger = setup_logger(__name__)
settings = get_settings()
validate_settings(settings)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.API_TITLE} v{settings.API_VERSION}")
    yield
    logger.info("Shutting down application")

app = FastAPI(
    title=settings.API_TITLE,
    version=settings.API_VERSION,
    description="AI-powered scheduling assistant with Google Calendar integration",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
    
app.include_router(router)
app.include_router(auth_router, prefix="/api")

@app.get("/")
async def root():
    return {
        "name": settings.API_TITLE,
        "version": settings.API_VERSION,
        "status": "running"
    }

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower()
    )