using System;

namespace TeklaExtractor.Models
{
    public class StructureRequest
    {
        // ── ModelAnalyzer se match karo ───────────────────────────────────────
        public const double GRID_SPACING = 5000.0;

        // ─── Structure Identity ───────────────────────────────────────────────
        public int    StructureType  { get; set; } = 1;
        public string StructureName  { get; set; } = string.Empty;

        // ─── Geometry ─────────────────────────────────────────────────────────
        public int    Columns    { get; set; } = 2;
        public int    Beams      { get; set; } = 1;
        public int    Pipes      { get; set; } = 0;
        public bool   Bracing    { get; set; } = false;
        public bool   DoubleDeck { get; set; } = false;

        // ─── Dimensions (mm) ─────────────────────────────────────────────────
        public double Height { get; set; } = 3000.0;
        public double Width  { get; set; } = 5000.0;
        public double Depth  { get; set; } = 5000.0;

        // ─── Profile / Material ───────────────────────────────────────────────
        public string ColumnProfile  { get; set; } = "HEA200";
        public string BeamProfile    { get; set; } = "IPE200";
        public string BracingProfile { get; set; } = "L50X50X5";
        public string Material       { get; set; } = "S235JR";

        // ─── Origin (mm) ─────────────────────────────────────────────────────
        public double OriginX { get; set; } = 0.0;
        public double OriginY { get; set; } = 0.0;
        public double OriginZ { get; set; } = 0.0;

        // ─── Factory: ModelContext se banao ───────────────────────────────────
        public static StructureRequest FromContext(ModelContext ctx)
        {
            var req = new StructureRequest
            {
                OriginX  = ctx.NextOriginX,
                OriginY  = ctx.NextOriginY,
                OriginZ  = ctx.NextOriginZ,
                Material = string.IsNullOrEmpty(ctx.DominantMaterial)
                               ? "S235JR" : ctx.DominantMaterial,
                ColumnProfile = string.IsNullOrEmpty(ctx.DominantProfile)
                               ? "HEA200" : ctx.DominantProfile,
            };
            req.SnapOriginToGrid();
            return req;
        }

        // ─── Grid snap ────────────────────────────────────────────────────────
        public void SnapOriginToGrid()
        {
            OriginX = SnapValue(OriginX);
            OriginY = SnapValue(OriginY);
            // Z snap nahi — floor heights grid se alag hoti hain
            Console.WriteLine(
                $"[Request] Origin snapped → ({OriginX}, {OriginY}, {OriginZ})");
        }

        private static double SnapValue(double v)
        {
            if (GRID_SPACING <= 0) return v;
            return Math.Round(v / GRID_SPACING) * GRID_SPACING;
        }

        // ─── Validation ───────────────────────────────────────────────────────
        public bool IsValid(out string error)
        {
            if (StructureType < 1 || StructureType > 5)
            { error = $"StructureType must be 1–5, got {StructureType}."; return false; }

            if (Height <= 0)
            { error = $"Height must be > 0, got {Height}."; return false; }

            if (Width <= 0)
            { error = $"Width must be > 0, got {Width}."; return false; }

            if (Depth <= 0)
            { error = $"Depth must be > 0, got {Depth}."; return false; }

            if (string.IsNullOrWhiteSpace(ColumnProfile))
            { error = "ColumnProfile cannot be empty."; return false; }

            if (string.IsNullOrWhiteSpace(BeamProfile))
            { error = "BeamProfile cannot be empty."; return false; }

            if (string.IsNullOrWhiteSpace(Material))
            { error = "Material cannot be empty."; return false; }

            error = string.Empty;
            return true;
        }

        public override string ToString() =>
            $"[Type {StructureType}] {StructureName} | " +
            $"Cols:{Columns} Beams:{Beams} Bracing:{Bracing} | " +
            $"H:{Height} W:{Width} D:{Depth} | " +
            $"Profile:{ColumnProfile}/{BeamProfile} Mat:{Material} | " +
            $"Origin:({OriginX},{OriginY},{OriginZ})";
    }
}