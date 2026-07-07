using System;
using System.Collections.Generic;
using System.Linq;
using Tekla.Structures.Model;
using Tekla.Structures.Model.Operations;
using TeklaExtractor.Models;

namespace TeklaExtractor.Analysis
{
    /// <summary>
    /// Analyses the live Tekla model and populates a <see cref="ModelContext"/>:
    ///   1. Reads Tekla Grid → fills GridMin/Max bounds
    ///   2. Iterates all Beam objects → computes overall bounding box
    ///   3. Clusters beams spatially → builds OccupiedRegions list
    ///   4. Detects structure type (Tower / Building / PipeRack / etc.)
    ///   5. Computes smart NextOrigin (width-aware, not always +Y)
    ///   6. Picks dominant profile & material by frequency
    /// </summary>
    public class ModelAnalyzer
    {
        private const double GRID_SPACING  = 5000;   // mm — gap between new & existing
        private const double CLUSTER_MERGE = 8000;   // mm — max gap to merge into same cluster

        private readonly Model _model;

        public ModelAnalyzer(Model model)
        {
            _model = model ?? throw new ArgumentNullException(nameof(model));
        }

        // ── Public entry point ────────────────────────────────────────────────
        public ModelContext Analyse()
        {
            var ctx = new ModelContext();

            // Step 1 — Read grid bounds from Tekla
            ReadGridBounds(ctx);

            // Step 2 — Collect all beam/column primitives
            var beams = CollectAllBeams();

            if (beams.Count == 0)
            {
                Console.WriteLine("[Analyzer] Empty model — using grid origin as start.");
                ctx.NextOriginX = ctx.GridMinX;
                ctx.NextOriginY = ctx.GridMinY;
                ctx.NextOriginZ = ctx.GridMinZ;
                return ctx;
            }

            // Step 3 — Compute overall bounding box
            ComputeOverallBoundingBox(beams, ctx);

            // Step 4 — Cluster beams → OccupiedRegions
            BuildOccupancyMap(beams, ctx);

            // Step 5 — Detect dominant profile & material
            DetectDominantProperties(beams, ctx);

            // Step 6 — Detect structure type
            ctx.DetectedStructureType = DetectStructureType(beams, ctx);

            // Step 7 — Compute smart NextOrigin (aspect-ratio aware)
            ComputeSmartNextOrigin(ctx);

            Console.WriteLine($"[Analyzer] {ctx}");
            Console.WriteLine($"[Analyzer] Detected type: {ctx.DetectedStructureType}");
            Console.WriteLine($"[Analyzer] Occupied regions: {ctx.OccupiedRegions.Count}");
            foreach (var r in ctx.OccupiedRegions)
                Console.WriteLine($"  {r}");

            return ctx;
        }

        // ════════════════════════════════════════════════════════════════════
        //  STEP 1 — GRID BOUNDS
        // ════════════════════════════════════════════════════════════════════
        private void ReadGridBounds(ModelContext ctx)
        {
            try
            {
                var gridEnum = _model.GetModelObjectSelector()
                                     .GetAllObjectsWithType(ModelObject.ModelObjectEnum.GRID);

                while (gridEnum.MoveNext())
                {
                    if (gridEnum.Current is Grid grid)
                    {
                        // Tekla Grid exposes CoordinateX/Y/Z as strings like "0 3000 6000"
                        double[] xs = ParseGridCoords(grid.CoordinateX);
                        double[] ys = ParseGridCoords(grid.CoordinateY);
                        double[] zs = ParseGridCoords(grid.CoordinateZ);

                        if (xs.Length > 0)
                        {
                            ctx.GridMinX = xs.Min();
                            ctx.GridMaxX = xs.Max();
                        }
                        if (ys.Length > 0)
                        {
                            ctx.GridMinY = ys.Min();
                            ctx.GridMaxY = ys.Max();
                        }
                        if (zs.Length > 0)
                        {
                            ctx.GridMinZ = zs.Min();
                            ctx.GridMaxZ = zs.Max();
                        }

                        Console.WriteLine($"[Analyzer] Grid → " +
                            $"X:{ctx.GridMinX:0}–{ctx.GridMaxX:0}  " +
                            $"Y:{ctx.GridMinY:0}–{ctx.GridMaxY:0}  " +
                            $"Z:{ctx.GridMinZ:0}–{ctx.GridMaxZ:0}");
                        break; // use first grid only
                    }
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[Analyzer] Grid read failed ({ex.Message}); using defaults.");
            }
        }

        private double[] ParseGridCoords(string raw)
        {
            if (string.IsNullOrWhiteSpace(raw)) return Array.Empty<double>();
            return raw.Split(new[] { ' ', '\t', ',' }, StringSplitOptions.RemoveEmptyEntries)
                      .Select(s => double.TryParse(s, out double v) ? v : double.NaN)
                      .Where(v => !double.IsNaN(v))
                      .ToArray();
        }

        // ════════════════════════════════════════════════════════════════════
        //  STEP 2 — COLLECT BEAMS
        // ════════════════════════════════════════════════════════════════════
        private List<Beam> CollectAllBeams()
        {
            var result = new List<Beam>();
            try
            {
                var selector = _model.GetModelObjectSelector()
                                     .GetAllObjectsWithType(ModelObject.ModelObjectEnum.BEAM);
                while (selector.MoveNext())
                    if (selector.Current is Beam b)
                        result.Add(b);
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[Analyzer] Beam collection error: {ex.Message}");
            }
            Console.WriteLine($"[Analyzer] Found {result.Count} beam objects.");
            return result;
        }

        // ════════════════════════════════════════════════════════════════════
        //  STEP 3 — OVERALL BOUNDING BOX
        // ════════════════════════════════════════════════════════════════════
        private void ComputeOverallBoundingBox(List<Beam> beams, ModelContext ctx)
        {
            double minX = double.MaxValue, maxX = double.MinValue;
            double minY = double.MaxValue, maxY = double.MinValue;
            double minZ = double.MaxValue, maxZ = double.MinValue;

            foreach (var b in beams)
            {
                UpdateMinMax(b.StartPoint.X, ref minX, ref maxX);
                UpdateMinMax(b.StartPoint.Y, ref minY, ref maxY);
                UpdateMinMax(b.StartPoint.Z, ref minZ, ref maxZ);
                UpdateMinMax(b.EndPoint.X,   ref minX, ref maxX);
                UpdateMinMax(b.EndPoint.Y,   ref minY, ref maxY);
                UpdateMinMax(b.EndPoint.Z,   ref minZ, ref maxZ);
            }

            ctx.BoundingMinX = minX; ctx.BoundingMaxX = maxX;
            ctx.BoundingMinY = minY; ctx.BoundingMaxY = maxY;
            ctx.BoundingMinZ = minZ; ctx.BoundingMaxZ = maxZ;

            Console.WriteLine($"[Analyzer] Overall BB → " +
                $"W:{ctx.ModelWidth:0} D:{ctx.ModelDepth:0} H:{ctx.ModelHeight:0}");
        }

        private static void UpdateMinMax(double v, ref double mn, ref double mx)
        {
            if (v < mn) mn = v;
            if (v > mx) mx = v;
        }

        // ════════════════════════════════════════════════════════════════════
        //  STEP 4 — OCCUPANCY MAP (spatial clustering)
        // ════════════════════════════════════════════════════════════════════
        private void BuildOccupancyMap(List<Beam> beams, ModelContext ctx)
        {
            // Represent each endpoint as a small bounding box, then merge nearby ones.
            var boxes = new List<OccupiedRegion>();
            foreach (var b in beams)
            {
                foreach (var pt in new[] { b.StartPoint, b.EndPoint })
                {
                    boxes.Add(new OccupiedRegion
                    {
                        MinX = pt.X, MaxX = pt.X,
                        MinY = pt.Y, MaxY = pt.Y,
                        MinZ = pt.Z, MaxZ = pt.Z,
                    });
                }
            }

            // Iteratively merge overlapping/nearby boxes
            bool merged = true;
            while (merged)
            {
                merged = false;
                for (int i = 0; i < boxes.Count; i++)
                {
                    for (int j = i + 1; j < boxes.Count; j++)
                    {
                        if (NearbyOrOverlap(boxes[i], boxes[j], CLUSTER_MERGE))
                        {
                            boxes[i] = Merge(boxes[i], boxes[j]);
                            boxes.RemoveAt(j);
                            merged = true;
                            break;
                        }
                    }
                    if (merged) break;
                }
            }

            // Filter out degenerate micro-boxes (single isolated points)
            ctx.OccupiedRegions = boxes
                .Where(b => b.Width + b.Depth + b.Height > 100)
                .OrderBy(b => b.MinX)
                .ToList();
        }

        private static bool NearbyOrOverlap(OccupiedRegion a, OccupiedRegion b, double margin)
        {
            return !(a.MaxX + margin < b.MinX || b.MaxX + margin < a.MinX ||
                     a.MaxY + margin < b.MinY || b.MaxY + margin < a.MinY ||
                     a.MaxZ + margin < b.MinZ || b.MaxZ + margin < a.MinZ);
        }

        private static OccupiedRegion Merge(OccupiedRegion a, OccupiedRegion b) =>
            new OccupiedRegion
            {
                MinX = Math.Min(a.MinX, b.MinX), MaxX = Math.Max(a.MaxX, b.MaxX),
                MinY = Math.Min(a.MinY, b.MinY), MaxY = Math.Max(a.MaxY, b.MaxY),
                MinZ = Math.Min(a.MinZ, b.MinZ), MaxZ = Math.Max(a.MaxZ, b.MaxZ),
            };

        // ════════════════════════════════════════════════════════════════════
        //  STEP 5 — DOMINANT PROFILE & MATERIAL
        // ════════════════════════════════════════════════════════════════════
        private void DetectDominantProperties(List<Beam> beams, ModelContext ctx)
        {
            var profFreq = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            var matFreq  = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);

            foreach (var b in beams)
            {
                string p = b.Profile.ProfileString  ?? "";
                string m = b.Material.MaterialString ?? "";
                if (!string.IsNullOrWhiteSpace(p))
                    profFreq[p] = (profFreq.ContainsKey(p) ? profFreq[p] : 0) + 1;
                if (!string.IsNullOrWhiteSpace(m))
                    matFreq[m]  = (matFreq.ContainsKey(m)  ? matFreq[m]  : 0) + 1;
            }

            ctx.DominantProfile  = profFreq.Count > 0
                ? profFreq.OrderByDescending(kv => kv.Value).First().Key : "";
            ctx.DominantMaterial = matFreq.Count > 0
                ? matFreq.OrderByDescending(kv => kv.Value).First().Key : "";

            Console.WriteLine($"[Analyzer] Dominant → Profile:{ctx.DominantProfile} Mat:{ctx.DominantMaterial}");
        }

        // ════════════════════════════════════════════════════════════════════
        //  STEP 6 — STRUCTURE TYPE DETECTION (enhanced)
        // ════════════════════════════════════════════════════════════════════
        private string DetectStructureType(List<Beam> beams, ModelContext ctx)
        {
            int columns = 0, horizontalBeams = 0, diagonalBraces = 0;
            double totalHeight = ctx.ModelHeight;
            double totalWidth  = ctx.ModelWidth;
            double totalDepth  = ctx.ModelDepth;

            foreach (var b in beams)
            {
                double dx = Math.Abs(b.EndPoint.X - b.StartPoint.X);
                double dy = Math.Abs(b.EndPoint.Y - b.StartPoint.Y);
                double dz = Math.Abs(b.EndPoint.Z - b.StartPoint.Z);
                double len = Math.Sqrt(dx*dx + dy*dy + dz*dz);
                if (len < 10) continue;

                double vertFraction = dz / len;

                if (vertFraction > 0.85)       columns++;
                else if (vertFraction < 0.15)  horizontalBeams++;
                else                           diagonalBraces++;
            }

            double aspectHW = totalHeight > 0 ? totalHeight / Math.Max(totalWidth, 1) : 0;
            double aspectHD = totalHeight > 0 ? totalHeight / Math.Max(totalDepth, 1) : 0;
            int    levels   = EstimateLevels(beams, ctx);

            Console.WriteLine($"[Analyzer] Cols:{columns} HBeams:{horizontalBeams} Braces:{diagonalBraces} " +
                $"AspHW:{aspectHW:0.00} AspHD:{aspectHD:0.00} Levels:{levels}");

            // ── Classification rules ──────────────────────────────────────
            if (aspectHW > 3 && aspectHD > 3 && diagonalBraces > columns)
                return "TelecomTower";

            if (aspectHW > 2 && diagonalBraces > 0 && columns >= 4 && levels >= 3)
                return "TransmissionTower";

            if (levels >= 2 && horizontalBeams > columns * 2 && totalDepth > 3000)
                return "Building";

            if (columns > 0 && horizontalBeams > 0 && totalDepth < 3000 && levels <= 2)
                return "PipeRack";

            if (totalDepth > 3000 && totalWidth > 3000 && levels == 1)
                return "Platform";

            if (diagonalBraces > horizontalBeams && totalHeight < totalWidth * 0.5)
                return "PortalFrame";

            if (columns == 2 && horizontalBeams >= 1 && diagonalBraces == 0)
                return "PipeSupport";

            if (diagonalBraces > 0 && horizontalBeams > 4)
                return "Truss";

            if (columns >= 4 && levels >= 2)
                return "TankFrame";

            return "GenericStructure";
        }

        private int EstimateLevels(List<Beam> beams, ModelContext ctx)
        {
            if (ctx.ModelHeight < 100) return 1;

            // Count unique horizontal beam elevations
            var zSet = new HashSet<int>();
            foreach (var b in beams)
            {
                double dx = Math.Abs(b.EndPoint.X - b.StartPoint.X);
                double dy = Math.Abs(b.EndPoint.Y - b.StartPoint.Y);
                double dz = Math.Abs(b.EndPoint.Z - b.StartPoint.Z);
                double len = Math.Sqrt(dx*dx + dy*dy + dz*dz);
                if (len < 10) continue;
                if (dz / len < 0.15)
                    zSet.Add((int)(b.StartPoint.Z / 500) * 500); // bucket to 500mm
            }
            return Math.Max(1, zSet.Count);
        }

        // ════════════════════════════════════════════════════════════════════
        //  STEP 7 — SMART NEXT ORIGIN (aspect-ratio aware)
        // ════════════════════════════════════════════════════════════════════
        private void ComputeSmartNextOrigin(ModelContext ctx)
        {
            double w = ctx.ModelWidth;
            double d = ctx.ModelDepth;

            double nextX, nextY;

            if (w >= d)
            {
                // Wide model → place new structure to the RIGHT (+X direction)
                nextX = ctx.BoundingMaxX + GRID_SPACING;
                nextY = ctx.BoundingMinY;
                Console.WriteLine($"[Analyzer] Wide model ({w:0}>{d:0}) → expanding in +X");
            }
            else
            {
                // Deep model → place new structure to the FRONT (+Y direction)
                nextX = ctx.BoundingMinX;
                nextY = ctx.BoundingMaxY + GRID_SPACING;
                Console.WriteLine($"[Analyzer] Deep model ({d:0}>{w:0}) → expanding in +Y");
            }

            // Clamp to grid extents
            (nextX, nextY, double nextZ) = ctx.ClampToGrid(nextX, nextY, ctx.BoundingMinZ);

            ctx.NextOriginX = nextX;
            ctx.NextOriginY = nextY;
            ctx.NextOriginZ = nextZ;

            Console.WriteLine($"[Analyzer] SmartNextOrigin → ({ctx.NextOriginX:0}, {ctx.NextOriginY:0}, {ctx.NextOriginZ:0})");
        }
    }
}