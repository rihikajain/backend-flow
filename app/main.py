"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import init_db, close_db
from app.api import router
from app.logging_config import setup_logging, get_logger

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    setup_logging(debug=settings.debug)
    logger = get_logger("main")
    logger.info("application_starting", app_name=settings.app_name)
    
    # Initialize MongoDB
    await init_db()
    logger.info("mongodb_initialized")
    
    yield
    
    # Shutdown
    await close_db()
    logger.info("application_shutting_down")


# Create FastAPI application
app = FastAPI(
    title=settings.app_name,
    description="Async job processing service using Celery",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(router, tags=["Jobs"])


@app.get("/")
async def root():
    """Root endpoint with service information."""
    return {
        "service": settings.app_name,
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
    }
