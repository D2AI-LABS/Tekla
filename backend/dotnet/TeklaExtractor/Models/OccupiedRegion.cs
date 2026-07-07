using System;

namespace TeklaExtractor.Models
{
    /// <summary>
    /// Represents a 3D bounding box of an already-placed structure cluster.
    /// Used by PlacementEngine to avoid clashes and find free zones.
    /// </summary>
    public class OccupiedRegion
    {
        public double MinX { get; set; }
        public double MaxX { get; set; }
        public double MinY { get; set; }
        public double MaxY { get; set; }
        public double MinZ { get; set; }
        public double MaxZ { get; set; }

        public double Width  => MaxX - MinX;
        public double Depth  => MaxY - MinY;
        public double Height => MaxZ - MinZ;

        public double CenterX => (MinX + MaxX) / 2.0;
        public double CenterY => (MinY + MaxY) / 2.0;
        public double CenterZ => (MinZ + MaxZ) / 2.0;

        /// <summary>
        /// Returns true if the given box overlaps this region (with optional margin).
        /// </summary>
        public bool Overlaps(double minX, double maxX,
                             double minY, double maxY,
                             double minZ, double maxZ,
                             double margin = 500)
        {
            return !(maxX + margin <= MinX || minX - margin >= MaxX ||
                     maxY + margin <= MinY || minY - margin >= MaxY ||
                     maxZ + margin <= MinZ || minZ - margin >= MaxZ);
        }

        public override string ToString() =>
            $"Region[X:{MinX:0}-{MaxX:0} Y:{MinY:0}-{MaxY:0} Z:{MinZ:0}-{MaxZ:0}]";
    }
}