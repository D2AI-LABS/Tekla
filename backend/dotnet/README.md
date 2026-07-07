# .NET Backend — TeklaExtractor (Universal AI BIM Engine v5.0)

This is the C# .NET 4.8 backend that connects to a live Tekla Structures model, extracts structural data, and pushes it to the FastAPI AI engine.

## Prerequisites

- [.NET SDK](https://dotnet.microsoft.com/download) (6+ recommended for the CLI)
- Tekla Structures 2026.0 installed at `C:\Program Files\Tekla Structures\2026.0\`
- Tekla Structures open with a model connected before running

## Quick Start

```powershell
cd backend/dotnet/TeklaExtractor
dotnet run
```

Or to build first and then run the compiled executable:

```powershell
cd backend/dotnet/TeklaExtractor
dotnet build --configuration Debug
.\bin\Debug\net48\TeklaExtractor.exe
```

### Using the helper script (recommended)

The script handles stopping any existing process, rebuilding, and launching in one step:

```powershell
cd backend/dotnet
.\start-tekla-extractor.ps1
```

## Project Structure

```
backend/dotnet/
├── TeklaExtractor/
│   ├── Program.cs                  # Entry point + 11-step extraction pipeline
│   ├── TeklaExtractor.csproj
│   ├── appsettings.json
│   ├── AI/                         # Prompt parsing & planning logic
│   ├── Controllers/                # BIM HTTP controller
│   ├── Generator/                  # Structure generation
│   ├── Models/                     # BimElement, ModelContext, etc.
│   ├── Services/                   # Tekla connection, profile resolver
│   └── Utilities/
├── TeklaExtractor.sln
└── start-tekla-extractor.ps1       # Helper launch script
```

## Notes

- Target framework: `net48` (x64)
- Tekla DLL references are resolved from the local Tekla Structures installation path — do not move or rename those directories
- The extractor polls the connected Tekla model and writes `output.json` / `extraction_manifest.json` to `bin\Debug\net48\`
