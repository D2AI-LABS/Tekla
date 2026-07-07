# routers/health.py — Health check endpoints
from fastapi import APIRouter

router = APIRouter(tags=["Health"])

@router.get("/health")
async def health_check():
    return {"status": "ok", "version": "5.0", "service": "Universal AI BIM Engine"}

@router.get("/")
async def root():
    return {"message": "Universal AI BIM Engine v5.0 is running"}
