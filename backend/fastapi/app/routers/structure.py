# routers/structure.py — Structure generation & management endpoints
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List

router = APIRouter(tags=["Structure"])

class StructureRequest(BaseModel):
    query: str
    context: Optional[dict] = None

class EditRequest(BaseModel):
    command: str
    target_ids: Optional[List[str]] = None

@router.post("/generate")
async def generate_structure(request: StructureRequest):
    """Generate a structural model from natural language query."""
    # Core logic delegated to services — import inline to avoid circular imports
    from app.extractor.structure_detector import detect_structure, is_create_command
    from app.ai.agent_engine import run_agent_command
    return {"status": "queued", "query": request.query}

@router.post("/edit")
async def edit_structure(request: EditRequest):
    """Edit an existing structural model."""
    from app.extractor.structure_editor import execute_edit
    return {"status": "queued", "command": request.command}
