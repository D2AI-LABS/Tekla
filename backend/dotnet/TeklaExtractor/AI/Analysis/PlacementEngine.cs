using System;
using System.Collections.Generic;
using TeklaExtractor.Models;

namespace TeklaExtractor.Analysis
{
    // ── Placement modes parsed from natural language ──────────────────────────
    public enum PlacementMode
    {
        Auto,       // system decides best free location
        RightOf,    // +X of reference
        LeftOf,     // -X of reference
        FrontOf,    // +Y of reference
        Behind,     // -Y of reference
        Above,      // +Z of reference
        Inside,     // centred within reference bounds
        Below,      // -Z (e.g. foundation under tower)
    }

    /// <summary>
    /// Finds a clash-free, grid-snapped origin for a new structure.
    ///
    /// Algorithm:
    ///   1. Parse PlacementMode from natural-language prompt
    ///   2. Compute candidate origin from the reference OccupiedRegion
    ///   3. Snap candidate to Tekla grid spacing
    ///   4. Walk in the placement direction until no clash is detected
    ///   5. Clamp result to grid extents
    /// </summary>
    public static class PlacementEngine
    {
        private const double GRID_SNAP    = 1000;  // mm — snap granularity
        private const double MAX_ATTEMPTS = 50;    // safety limit on clash search

        // ── Natural-language → PlacementMode ─────────────────────────────────
        public static PlacementMode ParseMode(string lowerPrompt)
        {
            if (ContainsAny(lowerPrompt, "right of", "right side", "beside", "next to", "adjacent"))
                return PlacementMode.RightOf;

            if (ContainsAny(lowerPrompt, "left of", "left side"))
                return PlacementMode.LeftOf;

            if (ContainsAny(lowerPrompt, "front of", "in front", "before"))
                return PlacementMode.FrontOf;

            if (ContainsAny(lowerPrompt, "behind", "back of", "rear"))
                return PlacementMode.Behind;

            if (ContainsAny(lowerPrompt, "above", "on top", "over"))
                return PlacementMode.Above;

            if (ContainsAny(lowerPrompt, "inside", "within", "internal"))
                return PlacementMode.Inside;

            if (ContainsAny(lowerPrompt, "below", "under", "beneath", "foundation"))
                return PlacementMode.Below;

            return PlacementMode.Auto;
        }

        // ── Main entry point ──────────────────────────────────────────────────
        /// <summary>
        /// Returns a clash-free, grid-snapped (X, Y, Z) origin for the new structure.
        /// </summary>
        /// <param name="newW">New structure footprint width  (X direction)</param>
        /// <param name="newD">New structure footprint depth  (Y direction)</param>
        /// <param name="newH">New structure height           (Z direction)</param>
        /// <param name="ctx">Current model context</param>
        /// <param name="mode">Placement direction</param>
        /// <param name="gap">Clear gap to leave between structures (mm)</param>
        public static (double X, double Y, double Z) FindBestLocation(
            double newW, double newD, double newH,
            ModelContext ctx,
            PlacementMode mode = PlacementMode.Auto,
            double gap = 3000)
        {
            OccupiedRegion reference = ctx.NearestRegion;

            double ox, oy, oz;

            if (reference == null)
            {
                // Empty model — start at grid origin
                ox = ctx.GridMinX;
                oy = ctx.GridMinY;
                oz = ctx.GridMinZ;
            }
            else
            {
                (ox, oy, oz) = ComputeCandidate(reference, newW, newD, newH, mode, gap, ctx);
            }

            // Snap to grid
            ox = Snap(ox, GRID_SNAP);
            oy = Snap(oy, GRID_SNAP);
            oz = Snap(oz, GRID_SNAP);

            // Resolve clashes by walking in the primary placement direction
            (ox, oy, oz) = ResolveClashes(ox, oy, oz, newW, newD, newH, ctx, mode, gap);

            // Clamp to grid bounds
            (ox, oy, oz) = ctx.ClampToGrid(ox, oy, oz);

            Console.WriteLine($"[PlacementEngine] Mode:{mode} → Final origin ({ox:0},{oy:0},{oz:0})");
            return (ox, oy, oz);
        }

        // ── Candidate origin from reference region ───────────────────────────
        private static (double, double, double) ComputeCandidate(
            OccupiedRegion r,
            double newW, double newD, double newH,
            PlacementMode mode, double gap,
            ModelContext ctx)
        {
            switch (mode)
            {
                case PlacementMode.RightOf:
                    return (r.MaxX + gap, r.MinY, r.MinZ);

                case PlacementMode.LeftOf:
                    return (r.MinX - newW - gap, r.MinY, r.MinZ);

                case PlacementMode.FrontOf:
                    return (r.MinX, r.MaxY + gap, r.MinZ);

                case PlacementMode.Behind:
                    return (r.MinX, r.MinY - newD - gap, r.MinZ);

                case PlacementMode.Above:
                    return (r.MinX, r.MinY, r.MaxZ + gap);

                case PlacementMode.Below:
                    return (r.MinX, r.MinY, r.MinZ - newH - gap);

                case PlacementMode.Inside:
                    // Centre the new structure within the reference footprint
                    double cx = r.CenterX - newW / 2.0;
                    double cy = r.CenterY - newD / 2.0;
                    return (cx, cy, r.MinZ);

                case PlacementMode.Auto:
                default:
                    // Use ModelContext's aspect-ratio-aware smart origin
                    return (ctx.NextOriginX, ctx.NextOriginY, ctx.NextOriginZ);
            }
        }

        // ── Clash-free search ─────────────────────────────────────────────────
        private static (double, double, double) ResolveClashes(
            double ox, double oy, double oz,
            double newW, double newD, double newH,
            ModelContext ctx,
            PlacementMode mode,
            double gap)
        {
            for (int attempt = 0; attempt < MAX_ATTEMPTS; attempt++)
            {
                if (!IsOccupied(ox, oy, oz, newW, newD, newH, ctx))
                    return (ox, oy, oz);

                Console.WriteLine($"[PlacementEngine] Clash at ({ox:0},{oy:0},{oz:0}) attempt {attempt+1} — stepping...");

                // Walk in the primary direction of the chosen mode
                switch (mode)
                {
                    case PlacementMode.LeftOf:
                        ox -= (newW + gap); break;
                    case PlacementMode.FrontOf:
                        oy += (newD + gap); break;
                    case PlacementMode.Behind:
                        oy -= (newD + gap); break;
                    case PlacementMode.Above:
                        oz += (newH + gap); break;
                    case PlacementMode.Below:
                        oz -= (newH + gap); break;
                    case PlacementMode.RightOf:
                    case PlacementMode.Auto:
                    default:
                        // Default walk: try +X first, then +Y
                        if (attempt % 2 == 0) ox += (newW + gap);
                        else                  oy += (newD + gap);
                        break;
                }

                ox = Snap(ox, GRID_SNAP);
                oy = Snap(oy, GRID_SNAP);
                oz = Snap(oz, GRID_SNAP);
            }

            Console.WriteLine("[PlacementEngine] ⚠️ MAX_ATTEMPTS reached — returning last candidate.");
            return (ox, oy, oz);
        }

        // ── Clash test ────────────────────────────────────────────────────────
        private static bool IsOccupied(
            double ox, double oy, double oz,
            double w, double d, double h,
            ModelContext ctx,
            double margin = 500)
        {
            foreach (var region in ctx.OccupiedRegions)
            {
                if (region.Overlaps(ox, ox + w, oy, oy + d, oz, oz + h, margin))
                    return true;
            }
            return false;
        }

        // ── Grid snap ─────────────────────────────────────────────────────────
        private static double Snap(double value, double snap) =>
            Math.Round(value / snap) * snap;

        private static bool ContainsAny(string text, params string[] keywords)
        {
            foreach (var kw in keywords)
                if (text.Contains(kw)) return true;
            return false;
        }
    }
}