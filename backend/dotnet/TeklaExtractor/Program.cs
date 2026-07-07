// Program.cs — Universal AI BIM Engine v5.0   FINAL / BUILD-READY
// ═══════════════════════════════════════════════════════════════════════════════
//
// ROOT CAUSE OF CS0101 ERRORS (now fixed):
//   Old Program.cs defined inline: PointData, BimElement, BimPayload,
//   InsertionResult, Program — all of which conflict with our new split files.
//
//   SOLUTION: all model classes removed from this file.
//   They now live in:
//     Models/BimElement.cs          → BimElement, BimModel
//     Models/ModelContext.cs        → ModelContext
//     Models/OccupiedRegion.cs      → OccupiedRegion
//     Generator/UniversalCreator.cs → UniversalCreator
//
//   This file contains ONLY:
//     • PointJson, BimElementJson, BimPayloadJson  (local JSON read schemas)
//     • MemberRecord, GeoRecord                   (local extraction records)
//     • TeklaModelExtractor static class
//     • FileBasedInserter static class
//     • Program class (entry point + main loop + 11-step pipeline)
//
// ═══════════════════════════════════════════════════════════════════════════════

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Tekla.Structures.Model;
using Tekla.Structures.Geometry3d;
using Task = System.Threading.Tasks.Task;
using TeklaExtractor.AI;
using TeklaExtractor.Analysis;
using TeklaExtractor.Generator;
using TeklaExtractor.Models;
using TeklaExtractor.Services;

namespace TeklaExtractor
{
    // ── Local JSON schemas (for bim_elements.json written by Python only) ─────
    // These are INTERNAL to this file. Do NOT confuse with Models/BimElement.cs.

    internal class PointJson
    {
        public double X { get; set; }
        public double Y { get; set; }
        public double Z { get; set; }
    }

    internal class BimElementJson
    {
        public string    Type       { get; set; } = "BEAM";
        public string    Profile    { get; set; } = "IPE300";
        public string    Material   { get; set; } = "S275";
        public string    Name       { get; set; } = "";
        public string    Class      { get; set; } = "1";
        public PointJson StartPoint { get; set; } = new PointJson();
        public PointJson EndPoint   { get; set; } = new PointJson();
    }

    internal class BimPayloadJson
    {
        public string               StructureType   { get; set; } = "";
        public string               Prompt          { get; set; } = "";
        public bool                 ClearModelFirst { get; set; }
        public List<BimElementJson> Elements        { get; set; } = new List<BimElementJson>();
    }

    // ── Record written to output.json for Python dashboard ────────────────────

    internal class MemberRecord
    {
        public int       Id         { get; set; }
        public string    Guid       { get; set; } = "";
        public string    Type       { get; set; } = "";
        public string    Direction  { get; set; } = "";
        public string    Name       { get; set; } = "";
        public string    Profile    { get; set; } = "";
        public string    Material   { get; set; } = "";
        public string    Class      { get; set; } = "";
        public string    Finish     { get; set; } = "";
        public PointJson StartPoint { get; set; } = new PointJson();
        public PointJson EndPoint   { get; set; } = new PointJson();
        public GeoRecord Geometry   { get; set; } = new GeoRecord();
    }

    internal class GeoRecord
    {
        public double DeltaX { get; set; }
        public double DeltaY { get; set; }
        public double DeltaZ { get; set; }
        public double Length { get; set; }
    }

    // =========================================================================
    //  TEKLA MODEL EXTRACTOR
    //  Reads all Beam objects from Tekla → writes output.json
    // =========================================================================

    internal static class TeklaModelExtractor
    {
        public static List<MemberRecord> ExtractAll(Model model)
        {
            var records = new List<MemberRecord>();
            var objs    = model.GetModelObjectSelector()
                               .GetAllObjectsWithType(ModelObject.ModelObjectEnum.BEAM);

            while (objs.MoveNext())
            {
                var beam = objs.Current as Beam;
                if (beam == null) continue;
                try
                {
                    var    sp  = beam.StartPoint;
                    var    ep  = beam.EndPoint;
                    double dx  = ep.X - sp.X, dy = ep.Y - sp.Y, dz = ep.Z - sp.Z;
                    double len = Math.Sqrt(dx * dx + dy * dy + dz * dz);
                    string dir = InferDirection(dx, dy, dz);
                    string cls = beam.Class ?? "";
                    string nam = beam.Name ?? "";
                    string prf = beam.Profile?.ProfileString ?? "";
                    string typ = InferMemberType(dir, cls, nam, prf);

                    records.Add(new MemberRecord
                    {
                        Id         = beam.Identifier.ID,
                        Guid       = beam.Identifier.GUID.ToString(),
                        Type       = typ,
                        Direction  = dir,
                        Name       = nam,
                        Profile    = beam.Profile?.ProfileString   ?? "",
                        Material   = beam.Material?.MaterialString ?? "",
                        Class      = beam.Class    ?? "",
                        Finish     = beam.Finish   ?? "",
                        StartPoint = new PointJson { X = sp.X, Y = sp.Y, Z = sp.Z },
                        EndPoint   = new PointJson { X = ep.X, Y = ep.Y, Z = ep.Z },
                        Geometry   = new GeoRecord { DeltaX = dx, DeltaY = dy, DeltaZ = dz,
                                                     Length = Math.Round(len, 1) },
                    });
                }
                catch { /* skip corrupt objects */ }
            }
            return records;
        }

        private static string InferDirection(double dx, double dy, double dz)
        {
            double adx = Math.Abs(dx), ady = Math.Abs(dy), adz = Math.Abs(dz);
            if (adz > adx * 1.2 && adz > ady * 1.2) return "VERTICAL";
            if (adz < adx * 0.35 && adz < ady * 0.35) return "HORIZONTAL";
            if (adz > 80 && (adx > 80 || ady > 80)) return "DIAGONAL";
            return adz >= adx && adz >= ady ? "VERTICAL" : "HORIZONTAL";
        }

        private static string InferMemberType(string dir, string cls, string name, string profile)
        {
            var c = (cls ?? "").Trim();
            if (c == "3") return "SECONDARY";
            if (c == "2") return "BEAM";
            if (c == "1") return "COLUMN";

            var n = (name ?? "").ToUpperInvariant();
            var p = (profile ?? "").ToUpperInvariant();
            if (n == "BEAM" || n.Contains("PLATFORM") || n.Contains("ANTENNA") || n.Contains("MOUNT"))
                return "BEAM";
            if (n.Contains("SECONDARY") || n.Contains("BRACE") || n.Contains("PURLIN")
                || n.Contains("LADDER") || n.Contains("RUNG") || n.Contains("GIRT"))
                return "SECONDARY";
            if (dir == "DIAGONAL") return "SECONDARY";
            if (dir == "HORIZONTAL" && (p.StartsWith("L") || p.StartsWith("C"))) return "SECONDARY";
            if (dir == "VERTICAL") return "COLUMN";
            return "BEAM";
        }

        public static void Run(Model model, string baseDir)
        {
            Console.WriteLine("\n[Extractor] Extracting model members...");
            var    records = ExtractAll(model);
            var    info    = model.GetInfo();
            string outPath = Path.Combine(baseDir, "output.json");
            string manPath = Path.Combine(baseDir, "extraction_manifest.json");

            File.WriteAllText(outPath,
                JsonConvert.SerializeObject(records, Formatting.Indented),
                System.Text.Encoding.UTF8);

            File.WriteAllText(manPath, JsonConvert.SerializeObject(new
            {
                member_count     = records.Count,
                model_name       = info.ModelName,
                export_timestamp = DateTime.UtcNow.ToString("o"),
            }, Formatting.Indented));

            Console.WriteLine($"[Extractor] {records.Count} members → {outPath}");
        }
    }

    // =========================================================================
    //  FILE-BASED INSERTER
    //  Reads bim_elements.json (written by Python /bim/generate) → inserts
    // =========================================================================

    internal static class FileBasedInserter
    {
        public static void Insert(Model model, BimPayloadJson payload)
        {
            var elements = payload.Elements ?? new List<BimElementJson>();
            var seen     = new HashSet<string>();
            int ok = 0, fail = 0, skip = 0, batch = 0;

            Console.WriteLine($"\n[Inserter] Type: {payload.StructureType}  Count: {elements.Count}");

            foreach (var e in elements)
            {
                if (e.StartPoint == null || e.EndPoint == null) { skip++; continue; }

                string key = $"{e.Type}|{e.Profile}|" +
                             $"{e.StartPoint.X:F0},{e.StartPoint.Y:F0},{e.StartPoint.Z:F0}|" +
                             $"{e.EndPoint.X:F0},{e.EndPoint.Y:F0},{e.EndPoint.Z:F0}";
                if (seen.Contains(key)) { skip++; continue; }

                if (TryInsert(model, e)) { seen.Add(key); ok++; batch++; }
                else fail++;

                if (batch > 0 && batch % 100 == 0)
                {
                    model.CommitChanges();
                    Console.WriteLine($"  [{ok}/{elements.Count}] committed...");
                }
            }

            if (ok > 0) model.CommitChanges();
            Console.WriteLine($"[Inserter] Created:{ok}  Failed:{fail}  Skipped:{skip}");
        }

        private static bool TryInsert(Model model, BimElementJson e)
        {
            if (e.StartPoint == null || e.EndPoint == null) return false;

            double dx = e.EndPoint.X - e.StartPoint.X;
            double dy = e.EndPoint.Y - e.StartPoint.Y;
            double dz = e.EndPoint.Z - e.StartPoint.Z;
            double len = Math.Sqrt(dx * dx + dy * dy + dz * dz);
            if (len < 50.0)
            {
                Console.WriteLine($"[Inserter] SKIP zero-length: {e.Type} {e.Profile}");
                return false;
            }

            string requestedProfile = NormalizeProfile(e.Profile);
            string probedMat = ProfileInsertProbe.GetWorkingMaterial();

            foreach (var prof in ProfileCandidates(e.Profile, e.Type))
            {
                var mats = MaterialCandidates(e.Material, probedMat);
                foreach (var mat in mats)
                {
                    try
                    {
                        var beam = new Beam(
                            new Point(e.StartPoint.X, e.StartPoint.Y, e.StartPoint.Z),
                            new Point(e.EndPoint.X,   e.EndPoint.Y,   e.EndPoint.Z));

                        beam.Profile.ProfileString   = prof;
                        beam.Material.MaterialString = mat;
                        beam.Name  = e.Name  ?? e.Type ?? "BEAM";
                        beam.Class = ClassFor(e.Type);
                        if (!string.IsNullOrWhiteSpace(e.Class) && e.Class != "1" && e.Class != "0")
                            beam.Class = e.Class;

                        if ((e.Type ?? "").ToUpper() == "COLUMN")
                            beam.Position.Rotation = Position.RotationEnum.TOP;

                        if (beam.Insert())
                        {
                            if (!string.Equals(prof, requestedProfile, StringComparison.OrdinalIgnoreCase))
                            {
                                Console.WriteLine(
                                    $"[Inserter] WARNING: {e.Type} requested '{requestedProfile}' not in catalog " +
                                    $"— used fallback '{prof}' (mat={mat})");
                            }
                            return true;
                        }
                    }
                    catch { /* try next profile/material */ }
                }
            }

            Console.WriteLine(
                $"[Inserter] FAILED: {e.Type} profile={e.Profile} mat={e.Material} " +
                $"({e.StartPoint.X:F0},{e.StartPoint.Y:F0},{e.StartPoint.Z:F0})→" +
                $"({e.EndPoint.X:F0},{e.EndPoint.Y:F0},{e.EndPoint.Z:F0})");
            return false;
        }

        private static IEnumerable<string> ProfileCandidates(string profile, string memberType)
        {
            return ProfileResolver.GetCandidates(profile, memberType);
        }

        private static IEnumerable<string> MaterialCandidates(string material, string probedMaterial = null)
        {
            if (!string.IsNullOrWhiteSpace(probedMaterial))
                yield return probedMaterial;
            yield return NormalizeMaterial(material);
            yield return "S275";
            yield return "S355";
            yield return "S235";
            yield return "S275JR";
            yield return "S355JR";
        }

        private static string NormalizeProfile(string p)
        {
            if (string.IsNullOrWhiteSpace(p)) return "HEA200";
            return p.ToUpperInvariant().Replace("×", "X").Replace("*", "X");
        }

        private static string NormalizeMaterial(string m)
        {
            if (string.IsNullOrWhiteSpace(m)) return "S275";
            var u = m.ToUpperInvariant();
            if (u.StartsWith("S235")) return "S275";
            return m;
        }

        private static string ClassFor(string t)
        {
            if (t == null) return "0";
            switch (t.ToUpper())
            {
                case "COLUMN":    return "1";
                case "BEAM":      return "2";
                case "BRACE":
                case "SECONDARY": return "3";
                default:          return "0";
            }
        }
    }

    // =========================================================================
    //  PROGRAM  — entry point
    // =========================================================================

    class Program
    {
        private const string API_BASE = "http://127.0.0.1:8000";

        private static readonly HttpClient _http = new HttpClient
        {
            Timeout = TimeSpan.FromSeconds(15)
        };

        static void Main(string[] args)
        {
            PrintBanner();

            // ── 1. Tekla connection ───────────────────────────────────────────
            var tekla = new Model();
            if (!tekla.GetConnectionStatus())
            {
                Console.WriteLine("  Tekla Structures not connected.");
                Console.WriteLine("  Open Tekla Structures first, then run: dotnet run");
                Console.ReadLine();
                return;
            }

            var info = tekla.GetInfo();
            Console.WriteLine($"  Connected  : {info.ModelName}");

            string baseDir = ResolveBaseDir(args);
            ProfileResolver.Initialize();
            ProfileInsertProbe.Discover(tekla, baseDir);
            Console.WriteLine($"  Base dir   : {baseDir}\n");

            // ── 3. Handle CLI flags ───────────────────────────────────────────
            if (args.Contains("--extract"))
            {
                TeklaModelExtractor.Run(tekla, baseDir);
                return;
            }

            string elemPath = Path.Combine(baseDir, "bim_elements.json");
            if (args.Contains("--once") && File.Exists(elemPath))
            {
                try
                {
                    var pl = JsonConvert.DeserializeObject<BimPayloadJson>(
                                File.ReadAllText(elemPath));
                    if (pl?.ClearModelFirst == true)
                    {
                        TeklaModelCleaner.ClearAllBeams(tekla);
                        save_json_empty(baseDir);
                    }
                    if (pl?.Elements?.Count > 0)
                        FileBasedInserter.Insert(tekla, pl);
                    File.Delete(elemPath);
                    TeklaModelExtractor.Run(tekla, baseDir);
                    PostOutputJson(baseDir).Wait();
                }
                catch (Exception ex) { Console.WriteLine($"[Once] {ex.Message}"); }
                return;
            }

            // ── 4. Full boot ──────────────────────────────────────────────────
            TeklaModelExtractor.Run(tekla, baseDir);
            PostOutputJson(baseDir).Wait();

            var analyzer = new ModelAnalyzer(tekla);
            ModelContext ctx = analyzer.Analyse();
            PrintContextSummary(ctx);
            PrintHelp();

            RunMainLoop(tekla, ref ctx, baseDir);
        }

        // =====================================================================
        //  MAIN LOOP
        // =====================================================================

        static void RunMainLoop(Model tekla, ref ModelContext ctx, string baseDir)
        {
            var creator  = new UniversalCreator(tekla);
            var planner  = new UniversalPlanner();
            bool running = true;

            string pendingFile  = Path.Combine(baseDir, "pending_prompt.txt");
            string elementsFile = Path.Combine(baseDir, "bim_elements.json");
            string clearFile    = Path.Combine(baseDir, "clear_tekla.json");

            // ── Background poller ─────────────────────────────────────────────
            // Capture ctx in a wrapper so the lambda can update it
            ModelContext[] ctxHolder = { ctx };

            var poll = new Thread(() =>
            {
                while (running)
                {
                    try
                    {
                        // Priority 1: standalone clear request
                        if (File.Exists(clearFile))
                        {
                            Console.WriteLine("\n[Poll] clear_tekla.json detected — clearing model...");
                            TeklaModelCleaner.ClearAllBeams(tekla);
                            File.Delete(clearFile);
                            save_json_empty(baseDir);
                            TeklaModelExtractor.Run(tekla, baseDir);
                            PostOutputJson(baseDir).Wait();
                            ctxHolder[0] = new ModelAnalyzer(tekla).Analyse();
                            Console.Write("\n  Command: ");
                        }

                        // Priority 2: bim_elements.json
                        if (File.Exists(elementsFile))
                        {
                            Console.WriteLine("\n[Poll] bim_elements.json detected — inserting...");
                            var pl = JsonConvert.DeserializeObject<BimPayloadJson>(
                                        File.ReadAllText(elementsFile));
                            if (pl?.ClearModelFirst == true)
                            {
                                Console.WriteLine("[Poll] ClearModelFirst=true — removing existing members...");
                                TeklaModelCleaner.ClearAllBeams(tekla);
                                save_json_empty(baseDir);
                            }
                            if (pl?.Elements?.Count > 0)
                                FileBasedInserter.Insert(tekla, pl);
                            File.Delete(elementsFile);
                            TeklaModelExtractor.Run(tekla, baseDir);
                            PostOutputJson(baseDir).Wait();
                            ctxHolder[0] = new ModelAnalyzer(tekla).Analyse();
                            Console.Write("\n  Command: ");
                        }

                        // Priority 3: pending_prompt.txt
                        if (File.Exists(pendingFile))
                        {
                            string prompt = File.ReadAllText(pendingFile).Trim();
                            File.Delete(pendingFile);
                            if (!string.IsNullOrWhiteSpace(prompt))
                            {
                                Console.WriteLine($"\n[Poll] Prompt: \"{prompt}\"");
                                ctxHolder[0] = new ModelAnalyzer(tekla).Analyse();
                                ExecutePipeline(prompt, planner, creator, tekla, baseDir, ref ctxHolder[0]);
                                Console.Write("\n  Command: ");
                            }
                        }
                    }
                    catch (Exception ex) { Console.WriteLine($"[Poll] {ex.Message}"); }

                    Thread.Sleep(2000);
                }
            }) { IsBackground = true };
            poll.Start();

            // ── Console command loop ──────────────────────────────────────────
            while (true)
            {
                Console.Write("\n  Command: ");
                string input = (Console.ReadLine() ?? "").Trim();
                if (string.IsNullOrEmpty(input)) continue;

                string lower = input.ToLowerInvariant();

                if (lower == "exit" || lower == "quit")
                { running = false; Console.WriteLine("  Bye!"); break; }

                if (lower == "help")     { PrintHelp();                                              continue; }
                if (lower == "status")   { PrintContextSummary(ctxHolder[0]);                        continue; }
                if (lower == "refresh")  { RefreshAll(tekla, baseDir, ref ctxHolder[0]);             continue; }
                if (lower == "clear")    { ClearTeklaModel(tekla, baseDir, ref ctxHolder[0]);        continue; }
                if (lower == "defects")  { CallApi("GET",  "/defects",        null);                 continue; }
                if (lower == "complete") { CallApi("POST", "/complete-model",  "{\"apply\":false}"); continue; }
                if (lower == "semantic") { CallApi("GET",  "/semantic-labels", null);                continue; }
                if (lower == "topology") { CallApi("GET",  "/topology",        null);                continue; }
                if (lower == "grid")     { CallApi("GET",  "/grid-info",       null);                continue; }
                if (lower == "boundary") { CallApi("GET",  "/boundary-info",   null);                continue; }
                if (lower == "ratios")   { CallApi("GET",  "/ratios",          null);                continue; }

                ctxHolder[0] = new ModelAnalyzer(tekla).Analyse();
                ExecutePipeline(input, planner, creator, tekla, baseDir, ref ctxHolder[0]);
            }

            // Propagate final ctx back
            ctx = ctxHolder[0];
        }

        // =====================================================================
        //  11-STEP UNIVERSAL PIPELINE
        // =====================================================================

        static void ExecutePipeline(
            string prompt,
            UniversalPlanner planner,
            UniversalCreator creator,
            Model tekla,
            string baseDir,
            ref ModelContext ctx)
        {
            Console.WriteLine($"\n  {'═'.ToString().PadRight(56, '═')}");
            Console.WriteLine($"  PIPELINE: \"{prompt}\"");
            Console.WriteLine($"  {'═'.ToString().PadRight(56, '═')}");

            try
            {
                // Step 1: Intent Detection
                var detected = StructureDetectorBridge.Detect(prompt);
                Console.WriteLine($"\n  [1] Detected: {detected.Type} ({detected.Category}) " +
                                  $"conf={detected.Confidence:0.00}");

                // Step 2: Engineering Planner
                double h = detected.Height > 0 ? detected.Height : ExtractDim(prompt, "height", 12000);
                double w = detected.Width  > 0 ? detected.Width  : ExtractDim(prompt, "width",  6000);
                double d = w;
                string profile  = string.IsNullOrEmpty(ctx.DominantProfile)  ? "L50X50X5" : ctx.DominantProfile;
                string material = string.IsNullOrEmpty(ctx.DominantMaterial) ? "S235JR"   : ctx.DominantMaterial;

                var plan = EngineeringPlanner.Plan(
                    detected.Type,
                    ctx.NextOriginX, ctx.NextOriginY, ctx.NextOriginZ,
                    h, w, d, profile, material,
                    prompt.ToLowerInvariant());

                Console.WriteLine($"  [2] Plan: {plan.Layers.Count} layers | " +
                                  $"Legs={plan.Rules.LegCount} Panels={plan.Rules.PanelCount}");

                // Step 3: Geometry Generation
                BimModel raw = UniversalGeometryGenerator.Generate(plan);
                Console.WriteLine($"  [3] Generated: {raw.Elements.Count} raw elements");

                // Step 4: Semantic Labels
                var labeled = SemanticPlaceRecognizer.Recognize(raw);
                SemanticPlaceRecognizer.PrintSummary(labeled);

                // Step 5: Coordinate Solver
                var solved    = UniversalCoordinateSolver.Solve(raw, ctx);
                BimModel valid = UniversalCoordinateSolver.ToBimModel(solved);
                Console.WriteLine($"  [5] Solver: {solved.ValidCount} valid | " +
                                  $"{solved.InvalidCount} removed | " +
                                  $"{solved.SnappedCount} snapped | " +
                                  $"{solved.ClampedCount} clamped");

                // Step 6: Engineering Validator
                var vr = EngineeringValidator.Validate(valid);
                Console.WriteLine($"  [6] Validation: {(vr.IsValid ? "PASS" : "ISSUES")} " +
                                  $"({vr.ErrorCount} errors, {vr.WarnCount} warnings)");
                foreach (var iss in vr.Issues.Where(i => i.Severity == "ERROR").Take(3))
                    Console.WriteLine($"      ERROR [{iss.Category}]: {iss.Message}");

                // Step 7: Topology
                var topo = TopologyLearningEngine.Learn(valid.Elements);
                Console.WriteLine($"  [7] Topology: nodes={topo.Nodes.Count} " +
                                  $"edges={topo.Edges.Count} " +
                                  $"disconnected={topo.DisconnectedNodes.Count}");

                // Step 8: Pattern Completion (only if sparse)
                double disconnRatio = topo.Nodes.Count > 0
                    ? (double)topo.DisconnectedNodes.Count / topo.Nodes.Count : 0;

                if (disconnRatio > 0.20 || vr.Summary.Disconnected > 5)
                {
                    Console.WriteLine("  [8] Pattern Reasoning Engine...");
                    var pr = PatternReasoningEngine.Analyse(valid);
                    if (pr.MissingElements.Count > 0)
                    {
                        valid.Elements.AddRange(pr.MissingElements);
                        Console.WriteLine($"      Added {pr.MissingElements.Count} " +
                                          $"→ {valid.Elements.Count} total");
                    }
                    else
                        Console.WriteLine($"      Pattern={pr.StructurePattern} " +
                                          $"Completion={pr.CompletionPercent}% (no gaps)");
                }
                else
                    Console.WriteLine("  [8] Pattern: skipped (well-connected)");

                // Step 9: Placement
                var mode = PlacementEngine.ParseMode(prompt.ToLowerInvariant());
                var (ox, oy, oz) = PlacementEngine.FindBestLocation(w, d, h, ctx, mode, gap: 3000);
                Console.WriteLine($"  [9] Placement: mode={mode} origin=({ox:0},{oy:0},{oz:0})");

                double offX = ox - plan.OriginX;
                double offY = oy - plan.OriginY;
                double offZ = oz - plan.OriginZ;
                if (Math.Abs(offX) + Math.Abs(offY) + Math.Abs(offZ) > 10)
                {
                    foreach (var e in valid.Elements)
                    {
                        e.StartX += offX; e.StartY += offY; e.StartZ += offZ;
                        e.EndX   += offX; e.EndY   += offY; e.EndZ   += offZ;
                    }
                }

                // Step 10: Tekla Insert
                Console.WriteLine($"\n  [10] Inserting {valid.Elements.Count} elements...");
                bool ok = creator.Create(valid);

                if (!ok)
                {
                    Console.WriteLine("  [10] Insert failed — check profile/material in Tekla catalog");
                    return;
                }

                Console.WriteLine($"\n  '{valid.Name}' inserted! Press Ctrl+F5 in Tekla.");

                // Step 11: Refresh
                ctx = new ModelAnalyzer(tekla).Analyse();
                TeklaModelExtractor.Run(tekla, baseDir);
                PostOutputJson(baseDir).Wait();
                Console.WriteLine($"  [11] Refreshed: regions={ctx.OccupiedRegions.Count} " +
                                  $"next=({ctx.NextOriginX:0},{ctx.NextOriginY:0},{ctx.NextOriginZ:0})");
            }
            catch (Exception ex)
            {
                Console.WriteLine($"\n  Pipeline error: {ex.Message}");
                var stack = (ex.StackTrace ?? "").Split('\n');
                if (stack.Length > 0)
                    Console.WriteLine($"  {stack[0].Trim()}");
            }

            Console.WriteLine($"  {'═'.ToString().PadRight(56, '═')}");
        }

        // =====================================================================
        //  HELPERS
        // =====================================================================

        static void RefreshAll(Model tekla, string baseDir, ref ModelContext ctx)
        {
            TeklaModelExtractor.Run(tekla, baseDir);
            PostOutputJson(baseDir).Wait();
            ctx = new ModelAnalyzer(tekla).Analyse();
            Console.WriteLine($"  Refreshed: regions={ctx.OccupiedRegions.Count}");
        }

        static void ClearTeklaModel(Model tekla, string baseDir, ref ModelContext ctx)
        {
            TeklaModelCleaner.ClearAllBeams(tekla);
            save_json_empty(baseDir);
            TeklaModelExtractor.Run(tekla, baseDir);
            PostOutputJson(baseDir).Wait();
            ctx = new ModelAnalyzer(tekla).Analyse();
            Console.WriteLine("  Tekla model cleared.");
        }

        static void save_json_empty(string baseDir)
        {
            string outPath = Path.Combine(baseDir, "output.json");
            File.WriteAllText(outPath, "[]", System.Text.Encoding.UTF8);
        }

       static async System.Threading.Tasks.Task PostOutputJson(string baseDir)
        {
            try
            {
                string p = Path.Combine(baseDir, "output.json");
                if (!File.Exists(p)) return;
                var json = File.ReadAllText(p);
                var content = new StringContent(json, Encoding.UTF8, "application/json");
                var resp = await _http.PostAsync($"{API_BASE}/upload-model-json", content);
                if (resp.IsSuccessStatusCode)
                {
                    string body = await resp.Content.ReadAsStringAsync();
                    dynamic d = JsonConvert.DeserializeObject(body);
                    Console.WriteLine($"  [Dashboard] synced: {d?.members ?? "?"} members");
                }
                else
                {
                    string errBody = await resp.Content.ReadAsStringAsync();
                    if (errBody.Length > 200) errBody = errBody.Substring(0, 200) + "…";
                    Console.WriteLine($"  [Dashboard] sync failed HTTP {(int)resp.StatusCode}: {errBody}");
                }
            }
            catch (Exception ex) { Console.WriteLine($"  [Dashboard] {ex.Message}"); }
        }

        static void CallApi(string method, string endpoint, string body)
        {
            try
            {
                HttpResponseMessage resp;
                if (method == "GET")
                    resp = _http.GetAsync($"{API_BASE}{endpoint}").Result;
                else
                    resp = _http.PostAsync($"{API_BASE}{endpoint}",
                        new StringContent(body ?? "{}", Encoding.UTF8, "application/json")).Result;

                string b = resp.Content.ReadAsStringAsync().Result;
                Console.WriteLine(b.Length > 1500 ? b.Substring(0, 1500) + "\n  ...(truncated)" : b);
            }
            catch (Exception ex) { Console.WriteLine($"  [{endpoint}] {ex.Message}"); }
        }

        static double ExtractDim(string prompt, string keyword, double def)
        {
            string lower = prompt.ToLowerInvariant();
            int idx = lower.IndexOf(keyword, StringComparison.Ordinal);
            if (idx < 0) return def;
            string after = lower.Substring(idx + keyword.Length).TrimStart(':', '=', ' ');
            string num   = new string(after.TakeWhile(c => char.IsDigit(c) || c == '.').ToArray());
            if (!double.TryParse(num, out double v)) return def;
            string unit  = after.Substring(num.Length).TrimStart().ToLower();
            if (unit.StartsWith("mm")) return v;
            if (unit.StartsWith("cm")) return v * 10;
            if (unit.StartsWith("m"))  return v * 1000;
            return v < 100 ? v * 1000 : v;
        }

        static string ResolveBaseDir(string[] args)
        {
            if (args.Length > 0 && !args[0].StartsWith("--") && Directory.Exists(args[0]))
                return Path.GetFullPath(args[0]);

            string envDir = Environment.GetEnvironmentVariable("TEKLA_SHARED_DIR");
            if (!string.IsNullOrWhiteSpace(envDir))
            {
                string envFull = Path.GetFullPath(envDir);
                if (File.Exists(Path.Combine(envFull, "main.py")))
                    return envFull;
            }

            string exe = System.Reflection.Assembly.GetExecutingAssembly().Location;
            string dir = !string.IsNullOrEmpty(exe)
                ? Path.GetDirectoryName(exe)
                : Directory.GetCurrentDirectory();

            // Walk up from bin/ — shared folder with Python FastAPI (main.py + bim_elements.json)
            while (!string.IsNullOrEmpty(dir))
            {
                foreach (string candidate in new[]
                {
                    Path.Combine(dir, "fastapi", "app"),
                    Path.Combine(dir, "backend", "fastapi", "app"),
                })
                {
                    if (File.Exists(Path.Combine(candidate, "main.py")))
                        return candidate;
                }
                string parent = Path.GetDirectoryName(dir);
                if (parent == null || parent == dir) break;
                dir = parent;
            }

            return !string.IsNullOrEmpty(exe)
                ? Path.GetDirectoryName(exe)
                : Directory.GetCurrentDirectory();
        }

        static void PrintBanner()
        {
            Console.WriteLine("  ╔══════════════════════════════════════════════════════════╗");
            Console.WriteLine("  ║  Universal AI BIM Platform  v5.0  —  FINAL BUILD-READY  ║");
            Console.WriteLine("  ║  Engineering-First 11-Step Pipeline                     ║");
            Console.WriteLine("  ╚══════════════════════════════════════════════════════════╝");
            Console.WriteLine();
        }

        static void PrintContextSummary(ModelContext ctx)
        {
            Console.WriteLine();
            Console.WriteLine($"  Detected type   : {ctx.DetectedStructureType}");
            Console.WriteLine($"  Profile         : {ctx.DominantProfile}");
            Console.WriteLine($"  Material        : {ctx.DominantMaterial}");
            Console.WriteLine($"  Occupied regions: {ctx.OccupiedRegions.Count}");
            Console.WriteLine($"  Grid X          : [{ctx.GridMinX:0} - {ctx.GridMaxX:0}]");
            Console.WriteLine($"  Grid Y          : [{ctx.GridMinY:0} - {ctx.GridMaxY:0}]");
            Console.WriteLine($"  Next origin     : ({ctx.NextOriginX:0}, {ctx.NextOriginY:0}, {ctx.NextOriginZ:0})");
        }

        static void PrintHelp()
        {
            Console.WriteLine();
            Console.WriteLine("  Commands:");
            Console.WriteLine("    create 40m telecom tower");
            Console.WriteLine("    create transmission tower height 50m");
            Console.WriteLine("    create monopole tower 30m");
            Console.WriteLine("    create triangular tower height 25m width 4m");
            Console.WriteLine("    create hexagonal tower 12 level height 36m");
            Console.WriteLine("    create water tower height 20m");
            Console.WriteLine("    create floodlight tower 25m");
            Console.WriteLine("    create wind turbine tower height 80m");
            Console.WriteLine("    create firewatch tower height 15m");
            Console.WriteLine("    create guard tower height 8m");
            Console.WriteLine("    create antenna frame tower height 12m");
            Console.WriteLine("    create lattice guyed tower 35m");
            Console.WriteLine("    create 5 floor office building width 20m");
            Console.WriteLine("    create warehouse width 30m depth 20m");
            Console.WriteLine("    create pipe rack height 8m");
            Console.WriteLine("    create equipment platform");
            Console.WriteLine("    create portal frame width 12m");
            Console.WriteLine("    create hex tower with staircase and pipe rack 10 level");
            Console.WriteLine("    status | refresh | clear | defects | complete | semantic");
            Console.WriteLine("    topology | grid | boundary | ratios | help | exit");
        }
    }
}