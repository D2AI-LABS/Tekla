# schemas/models.py — Pydantic request/response schemas
from pydantic import BaseModel
from typing import Optional, List, Any

class QueryRequest(BaseModel):
    query: str

class StructureElement(BaseModel):
    id: str
    role: str
    profile: Optional[str] = None
    material: Optional[str] = None
    length: Optional[float] = None
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None
    x2: Optional[float] = None
    y2: Optional[float] = None
    z2: Optional[float] = None

class ModelResponse(BaseModel):
    elements: List[StructureElement]
    summary: Optional[dict] = None

class AgentCommand(BaseModel):
    command: str
    args: Optional[dict] = None
