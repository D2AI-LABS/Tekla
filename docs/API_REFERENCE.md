# API Reference — Universal AI BIM Engine v5.0

## Base URL
```
http://localhost:8000
```

## Authentication
Currently no authentication required (development mode). Add API key middleware for production.

---

## Endpoints

### Health
```
GET /health
Response: { "status": "ok", "version": "5.0", "service": "Universal AI BIM Engine" }
```

### Generate Structure
```
POST /bim/generate
Body: { "query": "Create a 3-bay 4-storey steel frame" }
Response: { "elements": [...], "summary": {...} }
```

### AI Agent Command
```
POST /bim/agent
Body: { "command": "add roof", "send_to_tekla": true }
Response: { "result": "...", "elements_added": 5 }
```

### Complete Model
```
POST /bim/complete
Body: { "roof_type": "flat", "use_mirror": false, "send_to_tekla": true }
```

### Analyse Topology
```
POST /bim/analyse
Response: { "topology": {...}, "issues": [...] }
```

### Clash Detection
```
POST /bim/clashes
Response: { "clashes": [...] }
```

### Defect Detection
```
POST /bim/defects
Response: { "defects": [...] }
```

### Free-form Query
```
POST /query
Body: { "message": "How many columns are in the model?" }
Response: { "answer": "...", "data": {...} }
```

### Get Model Data
```
GET /model-data
Response: [ { "id": "COL-01", "role": "COLUMN", "profile": "HEA200", ... } ]
```

### Tekla Sync Status
```
GET /bim/tekla-status
Response: { "connected": true, "last_sync": "2026-06-30T10:00:00" }
```

### List Agent Commands
```
GET /bim/agent/commands
Response: { "commands": ["add roof", "mirror", "complete", ...] }
```
