# Architecture Guide

## System Overview

TeklaExtractor is a three-tier system:

1. **React Frontend** — Dashboard UI for natural language interaction, 3D visualization, and model management
2. **FastAPI Backend** — Python AI engine that processes commands, runs LLM inference via Groq, and writes BIM element data
3. **.NET Plugin** — C# Tekla Structures plugin that reads element data and inserts objects into the open Tekla model

## Communication Patterns

- **Frontend ↔ FastAPI**: REST/HTTP JSON over port 8000
- **FastAPI → .NET**: File-based IPC (`bim_elements.json`)
- **FastAPI ↔ Groq**: HTTPS API calls

## Key Design Decisions

### File-based IPC (FastAPI → .NET)
Tekla Structures runs as a COM server on Windows and cannot easily be called from Python. The bridge is:
- FastAPI writes `bim_elements.json`
- .NET plugin polls every 500ms and reads new elements
- This avoids COM interop from Python and keeps languages fully decoupled

### Modular AI Pipeline
Each reasoning step is a separate module:
- `structure_planner` → determine what to build
- `coordinate_solver` → resolve 3D positions
- `placement_engine` → place elements in grid
- `completion_engine` → fill gaps (roof, connections, secondary)
- `universal_rule_engine` → validate against structural rules

### Knowledge Graph
`graph_builder.py` builds a NetworkX graph of structural elements and their relationships, used by topology analysis and clash detection.
