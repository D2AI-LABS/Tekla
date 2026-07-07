# routers/model.py — Model data / export endpoints
from fastapi import APIRouter, HTTPException
from typing import List, Optional
import json, os

router = APIRouter(tags=["Model"])

OUTPUT_JSON = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output.json")

@router.get("/model")
async def get_model():
    """Return current model elements."""
    if not os.path.exists(OUTPUT_JSON):
        return []
    try:
        with open(OUTPUT_JSON, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/model")
async def clear_model():
    """Clear the current model."""
    if os.path.exists(OUTPUT_JSON):
        with open(OUTPUT_JSON, "w") as f:
            json.dump([], f)
    return {"status": "cleared"}
