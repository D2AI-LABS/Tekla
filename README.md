# TeklaExtractor — Universal AI BIM Engine v5.0

A full-stack AI-powered BIM (Building Information Modeling) platform that integrates with **Tekla Structures** to generate, analyze, and manage structural models through natural language commands.

---

## 🏗️ Project Architecture

```
TeklaExtractor/
│
├── frontend/                        # React.js Dashboard (Tekla Dashboard)
│   ├── src/
│   │   ├── App.js                   # Main application component (all UI logic)
│   │   ├── App.css                  # Stylesheet
│   │   ├── index.js                 # React entry point
│   │   ├── index.css                # Global styles
│   │   └── ...
│   ├── public/                      # Static assets (favicon, index.html, manifest)
│   ├── package.json                 # npm dependencies & scripts
│   ├── .env                         # Frontend environment variables
│   └── .gitignore
│
├── backend/
│   │
│   ├── fastapi/                     # Python FastAPI — AI BIM Engine
│   │   ├── app/
│   │   │   ├── main.py              # FastAPI app entry point & route registration
│   │   │   ├── config.py            # Environment settings (Pydantic)
│   │   │   │
│   │   │   ├── routers/             # API route handlers
│   │   │   │   ├── health.py        # GET /health, GET /
│   │   │   │   ├── structure.py     # POST /api/v1/generate, /edit
│   │   │   │   └── model.py         # GET/DELETE /api/v1/model
│   │   │   │
│   │   │   ├── ai/                  # AI & reasoning engines
│   │   │   │   ├── agent_engine.py  # Agent command processor
│   │   │   │   ├── completion_engine.py  # Model completion logic
│   │   │   │   ├── coordinate_solver.py  # 3D coordinate resolution
│   │   │   │   ├── topology_engine.py    # Structural topology analysis
│   │   │   │   ├── universal_rule_engine.py  # Validation rules
│   │   │   │   └── ai_engine.py     # Core AI integration (Groq/LLM)
│   │   │   │
│   │   │   ├── planner/             # Structural planning
│   │   │   │   └── structure_planner.py
│   │   │   │
│   │   │   ├── knowledge_graph/     # Graph-based knowledge
│   │   │   │   └── graph_builder.py
│   │   │   │
│   │   │   ├── services/            # Business logic services
│   │   │   │   ├── structure_engine.py   # Core structural model builder
│   │   │   │   ├── connection_engine.py  # Connection generator
│   │   │   │   ├── grid_engine.py        # Structural grid generator
│   │   │   │   └── placement_engine.py   # Element placement logic
│   │   │   │
│   │   │   ├── extractor/           # Tekla model extraction
│   │   │   │   ├── structure_detector.py  # Command/type detection
│   │   │   │   ├── structure_editor.py    # Model editing pipeline
│   │   │   │   ├── tekla_extractor.py     # Tekla API extraction
│   │   │   │   ├── tekla_runner.py        # Tekla runner wrapper
│   │   │   │   └── tekla_summary.py       # Model summarizer
│   │   │   │
│   │   │   ├── models/              # Domain models
│   │   │   │   └── role_classifier.py
│   │   │   │
│   │   │   ├── schemas/             # Pydantic schemas (request/response)
│   │   │   │   └── models.py
│   │   │   │
│   │   │   └── utils/               # Utility helpers
│   │   │       ├── generator.py
│   │   │       └── json_to_csv.py
│   │   │
│   │   ├── requirements.txt         # Python dependencies
│   │   ├── .env                     # Backend environment variables
│   │   └── README.md
│   │
│   └── dotnet/                      # C# .NET 4.8 — Tekla Structures Plugin
│       ├── TeklaExtractor.sln       # Visual Studio solution
│       └── TeklaExtractor/
│           ├── Program.cs           # Entry point + 11-step pipeline
│           ├── TeklaExtractor.csproj
│           ├── App.config
│           ├── appsettings.json
│           │
│           ├── AI/                  # AI integration layer
│           │   ├── Analysis/
│           │   │   ├── ModelAnalyzer.cs   # BIM model analysis
│           │   │   ├── PipelineStubs.cs   # Pipeline step stubs
│           │   │   └── PlacementEngine.cs # Tekla placement engine
│           │   ├── PromptParser.cs        # Natural language parser
│           │   └── UniversalPlanner.cs    # Universal structure planner
│           │
│           ├── Generator/           # Tekla element generation
│           │   ├── StructureGenerator.cs
│           │   └── UniversalCreator.cs
│           │
│           └── Models/              # C# domain models
│               ├── BimElement.cs
│               ├── ModelContext.cs
│               ├── OccupiedRegion.cs
│               └── StructureRequest.cs
│
├── docs/                            # Documentation
├── README.md                        # This file
└── .gitignore
```

---

## ⚙️ Tech Stack

| Layer     | Technology                        |
|-----------|-----------------------------------|
| Frontend  | React 19, Chart.js, Recharts, Axios |
| Backend   | Python 3.11, FastAPI, Groq AI, Pydantic |
| Plugin    | C# .NET 4.8, Tekla Structures API |
| AI/LLM    | Groq (LLaMA 3 / Mixtral)          |

---

## 🚀 Setup & Installation

### Prerequisites

- Node.js v18+ and npm
- Python 3.11+
- .NET Framework 4.8 (for Tekla plugin)
- Tekla Structures 2026.0 (installed at default path)
- [Groq API Key](https://console.groq.com/)

---

### 1. Clone the Repository

```bash
git clone https://github.com/your-org/TeklaExtractor.git
cd TeklaExtractor
```

---

### 2. Frontend Setup

```bash
# Navigate to frontend
cd frontend

# Install dependencies
npm install

# Configure environment
cp .env .env.local
# Edit .env.local → set REACT_APP_API_URL=http://localhost:8000

# Start development server
npm start
```

Frontend will run at: **http://localhost:3000**

To build for production:
```bash
npm run build
```

---

### 3. FastAPI Backend Setup

```bash
# Navigate to FastAPI backend
cd backend/fastapi

# Create virtual environment
python -m venv .venv

# Activate (Windows)
.venv\Scripts\activate

# Activate (Linux/Mac)
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env .env.local
# Edit .env.local → add your GROQ_API_KEY

# Run the development server
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

API will be available at: **http://localhost:8000**  
Swagger UI (API Docs): **http://localhost:8000/docs**  
ReDoc: **http://localhost:8000/redoc**

---

### 4. .NET Plugin Setup (Tekla Structures Plugin)

> **Requires:** Tekla Structures 2026.0 installed on Windows.

```bash
# Open solution in Visual Studio
cd backend/dotnet
start TeklaExtractor.sln /  .\start-tekla-extractor.ps1
```

Or via CLI:
```bash
dotnet build TeklaExtractor/TeklaExtractor.csproj --configuration Debug
```

**Run the plugin:**
```bash
cd backend/dotnet/TeklaExtractor/bin/Debug/net48
TeklaExtractor.exe
```

The plugin polls `bim_elements.json` (written by FastAPI) and inserts elements into the open Tekla Structures model.

---

## 🔑 Environment Variables

### Frontend (`frontend/.env`)

| Variable              | Description                          | Default                       |
|-----------------------|--------------------------------------|-------------------------------|
| `REACT_APP_API_URL`   | FastAPI backend base URL             | `http://localhost:8000`       |
| `REACT_APP_ENV`       | Environment name                     | `development`                 |

### FastAPI Backend (`backend/fastapi/.env`)

| Variable         | Description                        | Required |
|------------------|------------------------------------|----------|
| `GROQ_API_KEY`   | Groq LLM API key                   | ✅ Yes   |
| `HOST`           | Server host                        | `0.0.0.0` |
| `PORT`           | Server port                        | `8000`   |
| `DEBUG`          | Enable debug mode                  | `true`   |
| `CORS_ORIGINS`   | Allowed CORS origins (comma-sep)   | `*`      |
| `OUTPUT_DIR`     | Directory for output JSON files    | `./data` |

---

## 📡 API Documentation

### Health

| Method | Endpoint   | Description          |
|--------|-----------|----------------------|
| GET    | `/health`  | Health check         |
| GET    | `/`        | Root — service info  |

### Structure Generation

| Method | Endpoint              | Description                          |
|--------|-----------------------|--------------------------------------|
| POST   | `/bim/generate`       | Generate structure from NL query     |
| POST   | `/bim/agent`          | Run agent command                    |
| POST   | `/bim/complete`       | Complete partial model               |
| POST   | `/bim/analyse`        | Analyse current model topology       |
| POST   | `/bim/clashes`        | Run clash detection                  |
| POST   | `/bim/defects`        | Detect model defects                 |
| POST   | `/query`              | Free-form NL query                   |

### Model Management

| Method | Endpoint              | Description                         |
|--------|-----------------------|-------------------------------------|
| GET    | `/model-data`         | Get current model elements          |
| DELETE | `/model`              | Clear current model                 |
| GET    | `/bim/tekla-status`   | Get Tekla sync status               |
| GET    | `/bim/agent/commands` | List available agent commands       |

---

## 🔄 System Flow

```
User (Browser)
    │
    ▼
React Dashboard (frontend/)
    │  HTTP/JSON
    ▼
FastAPI Backend (backend/fastapi/)
    │  Groq AI API calls
    ▼
LLM (Groq — LLaMA3 / Mixtral)
    │
    ▼ (AI response → structure plan)
FastAPI writes → bim_elements.json
    │
    ▼ (file polling every 500ms)
.NET Plugin (backend/dotnet/)
    │  Tekla Structures API
    ▼
Tekla Structures 2026.0 (3D Model)
```

---

## 🗂️ Data Flow — Key Files

| File                  | Written by  | Read by      | Purpose                        |
|-----------------------|-------------|--------------|--------------------------------|
| `output.json`         | FastAPI      | Frontend     | Current model element list     |
| `bim_elements.json`   | FastAPI      | .NET plugin  | Elements to insert into Tekla  |
| `extraction_manifest.json` | .NET   | FastAPI      | Tekla extraction status        |

---

## 🛠️ Troubleshooting

### Frontend: "Failed to fetch" / Cannot reach backend
- Ensure FastAPI is running: `uvicorn app.main:app --reload`
- Check CORS settings in `backend/fastapi/app/main.py`
- Frontend auto-tries `127.0.0.1:8000` and `localhost:8000`

### FastAPI: `ModuleNotFoundError`
- Make sure you activated the virtual environment: `.venv\Scripts\activate`
- Run from `backend/fastapi/`: `uvicorn app.main:app --reload`

### .NET Plugin: Build errors (CS0656, CS0101)
- Ensure Tekla Structures 2026.0 is installed at the default path
- Check `TeklaExtractor.csproj` HintPaths match your Tekla install directory
- Open solution in Visual Studio and restore NuGet packages

### .NET Plugin: Not connecting to Tekla
- Tekla Structures must be open with a model loaded
- Run `TeklaExtractor.exe` as Administrator

### Groq API errors
- Verify `GROQ_API_KEY` is set correctly in `backend/fastapi/.env`
- Get a free key at https://console.groq.com/

---

## 📁 Scripts Reference

```bash
# ── Frontend ──────────────────────────────────────────────────
npm start              # Dev server on :3000
npm run build          # Production build → frontend/build/
npm test               # Run tests

# ── FastAPI Backend ───────────────────────────────────────────
uvicorn app.main:app --reload              # Dev mode (auto-reload)
uvicorn app.main:app --host 0.0.0.0 --port 8000  # Production

# ── .NET Plugin ───────────────────────────────────────────────
dotnet build           # Build solution
dotnet run             # Run plugin
```

---

## 📄 License

Proprietary — All rights reserved.
