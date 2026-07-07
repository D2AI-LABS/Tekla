using System;
using System.Collections.Generic;
using System.Linq;

namespace TeklaExtractor.Models
{
    /// <summary>
    /// Holds everything the planner needs to know about the current Tekla model state:
    ///   • Dominant profile / material
    ///   • Smart next-origin (width-aware, not always +Y)
    ///   • Full occupancy map (list of placed bounding boxes)
    ///   • Grid extents read from Tekla
    ///   • Detected structure type of the existing model
    /// </summary>
    public class ModelContext
    {
        // ── Dominant section & material ──────────────────────────────────────
        public string DominantProfile  { get; set; } = "";
        public string DominantMaterial { get; set; } = "";

        // ── Smart next-origin ────────────────────────────────────────────────
        // Computed by ModelAnalyzer using bounding-box aspect ratio logic.
        // Wide model  → expand in +X (right).
        // Deep model  → expand in +Y (front).
        public double NextOriginX { get; set; } = 0;
        public double NextOriginY { get; set; } = 0;
        public double NextOriginZ { get; set; } = 0;

        // ── Occupancy map ────────────────────────────────────────────────────
        // Every structure cluster that already exists in the model is recorded here.
        // PlacementEngine uses this list to find clash-free locations.
        public List<OccupiedRegion> OccupiedRegions { get; set; } = new List<OccupiedRegion>();

        // ── Grid bounds (read from Tekla Grid object) ────────────────────────
        public double GridMinX { get; set; } = 0;
        public double GridMaxX { get; set; } = 100000;
        public double GridMinY { get; set; } = 0;
        public double GridMaxY { get; set; } = 100000;
        public double GridMinZ { get; set; } = 0;
        public double GridMaxZ { get; set; } = 50000;

        // ── Overall bounding box of ALL existing elements ────────────────────
        public double BoundingMinX { get; set; } = 0;
        public double BoundingMaxX { get; set; } = 0;
        public double BoundingMinY { get; set; } = 0;
        public double BoundingMaxY { get; set; } = 0;
        public double BoundingMinZ { get; set; } = 0;
        public double BoundingMaxZ { get; set; } = 0;

        // ── Detected structure type ──────────────────────────────────────────
        public string DetectedStructureType { get; set; } = "Unknown";

        // ── Convenience helpers ──────────────────────────────────────────────
        public double ModelWidth  => BoundingMaxX - BoundingMinX;
        public double ModelDepth  => BoundingMaxY - BoundingMinY;
        public double ModelHeight => BoundingMaxZ - BoundingMinZ;

        /// <summary>Clamp a point to lie within the Tekla grid extents.</summary>
        public (double x, double y, double z) ClampToGrid(double x, double y, double z)
        {
            x = Math.Max(GridMinX, Math.Min(GridMaxX, x));
            y = Math.Max(GridMinY, Math.Min(GridMaxY, y));
            z = Math.Max(GridMinZ, Math.Min(GridMaxZ, z));
            return (x, y, z);
        }

        /// <summary>
        /// Returns the nearest occupied region (if any). Useful for relative
        /// placement ("beside building", "above platform", etc.).
        /// </summary>
        public OccupiedRegion NearestRegion =>
            OccupiedRegions.Count == 0 ? null : OccupiedRegions[0];

        public override string ToString() =>
            $"ModelContext[Profile:{DominantProfile} Mat:{DominantMaterial} " +
            $"NextOrigin:({NextOriginX:0},{NextOriginY:0},{NextOriginZ:0}) " +
            $"ModelW:{ModelWidth:0} ModelD:{ModelDepth:0} " +
            $"Regions:{OccupiedRegions.Count} Type:{DetectedStructureType}]";
    }
}