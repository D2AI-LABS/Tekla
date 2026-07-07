using System;
using System.Collections.Generic;
using System.Net.Http;
using System.Text;
using System.Threading.Tasks;
using Task = System.Threading.Tasks.Task;
using Newtonsoft.Json;
using TeklaExtractor.Analysis;  // NEW: PlacementEngine, PlacementMode
using TeklaExtractor.Models;

namespace TeklaExtractor.AI
{
    public class UniversalPlanner
    {
        private static readonly HttpClient _http = new HttpClient
        {
            Timeout = TimeSpan.FromSeconds(30)
        };

        // ═════════════════════════════════════════════════════════════════
        //  PLAN() — UPDATED: uses PlacementEngine for clash-free origin
        // ═════════════════════════════════════════════════════════════════
        public BimModel Plan(string prompt, ModelContext ctx)
        {
            Console.WriteLine($"\n[Planner] Prompt: \"{prompt}\"");
            Console.WriteLine($"[Planner] Context: {ctx}");

            string lower = prompt.ToLowerInvariant();

            // ── Profile / material from context or safe defaults ──────────
            string profile  = !string.IsNullOrEmpty(ctx.DominantProfile)  ? ctx.DominantProfile  : "L50X50X5";
            string material = !string.IsNullOrEmpty(ctx.DominantMaterial) ? ctx.DominantMaterial : "S235JR";

            // ── Dimensions from prompt ────────────────────────────────────
            double height = ExtractDim(lower, "height", "h", 12000);
            double width  = ExtractDim(lower, "width",  "w", 5000);
            double depth  = ExtractDim(lower, "depth",  "d", 5000);
            bool   brace  = lower.Contains("brac") || lower.Contains("haunch");

            Console.WriteLine($"[Planner] Dims → H:{height} W:{width} D:{depth}");

            // ── NEW: PlacementEngine — clash-free, grid-snapped origin ────
            // "beside building" → RightOf
            // "behind tower"    → Behind
            // "above platform"  → Above
            // (nothing)         → Auto (aspect-ratio aware)
            PlacementMode mode = PlacementEngine.ParseMode(lower);
            Console.WriteLine($"[Planner] PlacementMode: {mode}");

            var (ox, oy, oz) = PlacementEngine.FindBestLocation(
                newW: width,
                newD: depth,
                newH: height,
                ctx:  ctx,
                mode: mode,
                gap:  3000);

            Console.WriteLine($"[Planner] Final Origin → ({ox}, {oy}, {oz})");

            // ── LOCAL RULES ───────────────────────────────────────────────

            // 0. Hex Tower + Staircase + Pipe Rack (combined)
            if (ContainsAny(lower, "hexagonal tower", "hex tower") &&
                ContainsAny(lower, "staircase", "stair") &&
                ContainsAny(lower, "pipe rack", "rack"))
                return BuildHexTowerWithStaircaseAndRack(ox, oy, oz, height, width, depth, profile, material, lower);

            // 1. Hexagonal Tower
            if (ContainsAny(lower, "hexagonal tower","hex tower","hexagon tower",
                                   "hexagonal vertical","hex vertical","6 sided tower",
                                   "hexagonal","hex frame","hex column"))
                return BuildHexagonalTower(ox, oy, oz, height, width, profile, material, lower);

            // 2. Building
            if (ContainsAny(lower, "building","multi floor","multi-floor","multistorey",
                                   "multi storey","office building","industrial building",
                                   "warehouse","shed","factory"))
                return BuildBuilding(ox, oy, oz, height, width, depth, profile, material, lower);

            // 3. Pipe Support
            if (ContainsAny(lower, "pipe support","simple support","single support","pipe stand"))
                return BuildPipeSupport(ox, oy, oz, height, width, profile, material);

            // 4. X Bracing
            if (ContainsAny(lower, "x brac","x-brac","cross brac","diagonal brac","xbracing"))
                return BuildXBracing(ox, oy, oz, height, width, profile, material);

            // 5. Double Level Rack
            if (ContainsAny(lower, "double level","two level","double deck","dual rack","2 level"))
                return BuildDoubleLevelRack(ox, oy, oz, height, width, profile, material);

            // 6. Four Column Rack
            if (ContainsAny(lower, "four column","4 column","quad column","4-column","wide rack"))
                return BuildFourColumnRack(ox, oy, oz, height, width, depth, profile, material);

            // 7. Portal Frame
            if (ContainsAny(lower, "portal frame","portal","gable frame","shed frame"))
                return BuildPortalFrame(ox, oy, oz, height, width, depth, brace, profile, material);

            // 8. Telecom Tower
            if (ContainsAny(lower, "telecom tower","mast","lattice tower","communication tower"))
                return BuildTelecomTower(ox, oy, oz, height, width, profile, material);

            // 9. Equipment Platform
            if (ContainsAny(lower, "equipment platform","platform","mezzanine","raised floor"))
                return BuildEquipmentPlatform(ox, oy, oz, height, width, depth, profile, material);

            // 10. Pipe Rack Multi-Bay
            if (ContainsAny(lower, "pipe rack","multi bay","multibay","multi-bay","rack"))
                return BuildMultiBayPipeRack(ox, oy, oz, height, width, depth, profile, material);

            // 11. Staircase
            if (ContainsAny(lower, "stair","staircase","steps","ladder"))
                return BuildStaircase(ox, oy, oz, height, width, profile, material);

            // 12. Truss
            if (ContainsAny(lower, "truss","roof truss","warren truss","pratt truss"))
                return BuildTruss(ox, oy, oz, height, width, profile, material);

            // 13. Canopy
            if (ContainsAny(lower, "canopy","cantilever","shade structure","awning"))
                return BuildCanopy(ox, oy, oz, height, width, profile, material);

            // 14. Water Tank Frame
            if (ContainsAny(lower, "water tank","tank frame","storage tank","overhead tank"))
                return BuildTankFrame(ox, oy, oz, height, width, depth, profile, material);

            // 15. Tower generic fallback
            if (ContainsAny(lower, "tower"))
                return BuildTelecomTower(ox, oy, oz, height, width, profile, material);

            // ── FALLBACK: AI API ──────────────────────────────────────────
            Console.WriteLine("[Planner] No local rule matched — trying AI API...");
            try
            {
                var aiModel = PlanWithAI(prompt, ctx, profile, material, ox, oy, oz).Result;
                if (aiModel != null && aiModel.Elements.Count > 0)
                    return aiModel;
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[Planner] AI API failed: {ex.Message}");
            }

            Console.WriteLine("[Planner] Falling back to basic pipe support");
            return BuildPipeSupport(ox, oy, oz, height, width, profile, material);
        }

        // ═════════════════════════════════════════════════════════════════
        //  STRUCTURE BUILDERS — ALL UNCHANGED FROM ORIGINAL
        // ═════════════════════════════════════════════════════════════════

        // ── COMBINED: Hex Tower + Staircase + Pipe Rack ───────────────────
        private BimModel BuildHexTowerWithStaircaseAndRack(
            double ox, double oy, double oz,
            double h, double w, double d,
            string prof, string mat, string lower)
        {
            var m = New("Hex Tower + Staircase + Pipe Rack");

            int levels = 10;
            foreach (var kw in new[] { "level", "floor", "storey", "tier" })
            {
                int idx = lower.IndexOf(kw);
                if (idx < 0) continue;
                string before = lower.Substring(0, idx).TrimEnd();
                string[] parts = before.Split(' ');
                if (parts.Length > 0 && int.TryParse(parts[parts.Length - 1], out int lv))
                { levels = Math.Max(2, Math.Min(lv, 20)); break; }
            }

            double r       = w / 2.0;
            double lh      = h / levels;
            double centerX = ox + r;
            double centerY = oy + r;

            double[] cx = new double[6];
            double[] cy = new double[6];
            for (int i = 0; i < 6; i++)
            {
                double angle = i * Math.PI / 3.0;
                cx[i] = centerX + r * Math.Cos(angle);
                cy[i] = centerY + r * Math.Sin(angle);
            }

            for (int i = 0; i < 6; i++)
                m.Elements.Add(Col(cx[i], cy[i], oz, oz + h, prof, mat));

            for (int lv = 1; lv <= levels; lv++)
            {
                double zF = oz + lv * lh;
                for (int i = 0; i < 6; i++)
                {
                    int next = (i + 1) % 6;
                    m.Elements.Add(Bm(cx[i], cy[i], zF, cx[next], cy[next], zF, prof, mat));
                }
                for (int i = 0; i < 3; i++)
                    m.Elements.Add(Bm(cx[i], cy[i], zF, cx[i + 3], cy[i + 3], zF, prof, mat));
            }

            for (int lv = 0; lv < levels; lv++)
            {
                double z0L = oz + lv * lh;
                double z1L = z0L + lh;
                for (int i = 0; i < 6; i++)
                {
                    int next = (i + 1) % 6;
                    if (lv % 2 == 0)
                        m.Elements.Add(Br(cx[i], cy[i], z0L, cx[next], cy[next], z1L, prof, mat));
                    else
                        m.Elements.Add(Br(cx[next], cy[next], z0L, cx[i], cy[i], z1L, prof, mat));
                }
            }

            for (int i = 0; i < 6; i++)
            {
                int next = (i + 1) % 6;
                m.Elements.Add(Bm(cx[i], cy[i], oz, cx[next], cy[next], oz, prof, mat));
            }

            // Staircase — tower ke right side
            double stairX = centerX + r;
            double stairY = centerY - 1000;
            double stairW = 2000;
            int    steps  = levels;
            double sh     = h / steps;
            double sd     = stairW / steps;

            for (int i = 0; i < steps; i++)
            {
                double z0S = oz + i * sh;
                double z1S = z0S + sh;
                double y0S = stairY + i * sd;
                double y1S = y0S + sd;

                m.Elements.Add(Col(stairX, y0S, z0S, z1S, prof, mat));
                m.Elements.Add(Bm(stairX, y0S, z1S, stairX, y1S, z1S, prof, mat));
                m.Elements.Add(Br(stairX, y0S, z0S, stairX, y1S, z1S, prof, mat));
            }

            m.Elements.Add(Br(stairX, stairY, oz, stairX, stairY + stairW, oz + h, prof, mat));
            m.Elements.Add(Br(stairX + 1000, stairY, oz, stairX + 1000, stairY + stairW, oz + h, prof, mat));

            // Pipe Rack — tower ke left side
            double rackDepth = d > 0 ? d : 5000;
            double rackX     = centerX - r - 3500;
            double rackY     = centerY - rackDepth / 2.0;
            double rackH     = 5000;
            double bayW2     = 3000;
            int    bays      = 3;

            for (int i = 0; i <= bays; i++)
            {
                double xi = rackX + i * bayW2;
                m.Elements.Add(Col(xi, rackY,              oz, oz + rackH, prof, mat));
                m.Elements.Add(Col(xi, rackY + rackDepth,  oz, oz + rackH, prof, mat));

                if (i < bays)
                {
                    double xi2 = xi + bayW2;
                    m.Elements.Add(Bm(xi, rackY,             oz + rackH, xi2, rackY,             oz + rackH, prof, mat));
                    m.Elements.Add(Bm(xi, rackY + rackDepth, oz + rackH, xi2, rackY + rackDepth, oz + rackH, prof, mat));
                }

                m.Elements.Add(Bm(xi, rackY, oz + rackH,       xi, rackY + rackDepth, oz + rackH,       prof, mat));
                m.Elements.Add(Bm(xi, rackY, oz + rackH * 0.6, xi, rackY + rackDepth, oz + rackH * 0.6, prof, mat));
            }

            // Tower ↔ Rack connection
            double rackRightX = rackX + bays * bayW2;
            m.Elements.Add(Bm(cx[3], cy[3], oz + rackH,
                              rackRightX, rackY + rackDepth / 2.0, oz + rackH, prof, mat));

            // Tower ↔ Staircase connection (har 2 levels)
            for (int lv = 2; lv <= levels; lv += 2)
            {
                double zConn = oz + lv * lh;
                double yConn = stairY + (stairW * lv / levels);
                m.Elements.Add(Bm(cx[0], cy[0], zConn, stairX, yConn, zConn, prof, mat));
            }

            Console.WriteLine($"[Combined] Total elements: {m.Elements.Count}");
            return m;
        }

        // ── Hexagonal Tower ───────────────────────────────────────────────
        private BimModel BuildHexagonalTower(
            double ox, double oy, double oz,
            double h, double w,
            string prof, string mat, string lower)
        {
            int levels = 5;
            foreach (var kw in new[] { "level", "floor", "storey", "tier" })
            {
                int idx = lower.IndexOf(kw);
                if (idx < 0) continue;
                string before = lower.Substring(0, idx).TrimEnd();
                string[] parts = before.Split(' ');
                if (parts.Length > 0 && int.TryParse(parts[parts.Length - 1], out int lv))
                { levels = Math.Max(2, Math.Min(lv, 20)); break; }
            }

            double r       = w / 2.0;
            double lh      = h / levels;
            double centerX = ox + r;
            double centerY = oy + r;

            var m = New($"Hexagonal Vertical Tower ({w/1000:0.0}m dia × {h/1000:0.0}m tall, {levels} levels)");

            double[] cx = new double[6];
            double[] cy = new double[6];
            for (int i = 0; i < 6; i++)
            {
                double angle = i * Math.PI / 3.0;
                cx[i] = centerX + r * Math.Cos(angle);
                cy[i] = centerY + r * Math.Sin(angle);
            }

            for (int i = 0; i < 6; i++)
                m.Elements.Add(Col(cx[i], cy[i], oz, oz + h, prof, mat));

            for (int lv = 1; lv <= levels; lv++)
            {
                double zF = oz + lv * lh;
                for (int i = 0; i < 6; i++)
                {
                    int next = (i + 1) % 6;
                    m.Elements.Add(Bm(cx[i], cy[i], zF, cx[next], cy[next], zF, prof, mat));
                }
                for (int i = 0; i < 3; i++)
                    m.Elements.Add(Bm(cx[i], cy[i], zF, cx[i + 3], cy[i + 3], zF, prof, mat));
            }

            for (int lv = 0; lv < levels; lv++)
            {
                double z0L = oz + lv * lh;
                double z1L = z0L + lh;
                for (int i = 0; i < 6; i++)
                {
                    int next = (i + 1) % 6;
                    if (lv % 2 == 0)
                        m.Elements.Add(Br(cx[i], cy[i], z0L, cx[next], cy[next], z1L, prof, mat));
                    else
                        m.Elements.Add(Br(cx[next], cy[next], z0L, cx[i], cy[i], z1L, prof, mat));
                }
            }

            for (int i = 0; i < 6; i++)
            {
                int next = (i + 1) % 6;
                m.Elements.Add(Bm(cx[i], cy[i], oz, cx[next], cy[next], oz, prof, mat));
            }

            Console.WriteLine($"[HexTower] Total elements: {m.Elements.Count}");
            return m;
        }

        // ── Multi-Floor Building ──────────────────────────────────────────
        private BimModel BuildBuilding(
            double ox, double oy, double oz,
            double h, double w, double d,
            string prof, string mat, string lower)
        {
            int floors = 3;
            int fi = lower.IndexOf("floor");
            if (fi > 0)
            {
                string before = lower.Substring(0, fi).TrimEnd();
                string[] parts = before.Split(' ');
                if (parts.Length > 0 && int.TryParse(parts[parts.Length - 1], out int f))
                    floors = Math.Max(1, Math.Min(f, 20));
            }
            foreach (var kw in new[] { "storey", "story", "storeyed" })
            {
                int si = lower.IndexOf(kw);
                if (si > 0)
                {
                    string before = lower.Substring(0, si).TrimEnd();
                    string[] parts = before.Split(' ');
                    if (parts.Length > 0 && int.TryParse(parts[parts.Length - 1], out int f))
                    { floors = Math.Max(1, Math.Min(f, 20)); break; }
                }
            }

            if (w <= 0) w = 10000;
            if (d <= 0) d = 8000;

            double floorH = h > 0 ? h / floors : 3000;
            double xR = ox + w;
            double yB = oy + d;

            var m = New($"{floors}-Floor Building ({w/1000:0.0}m x {d/1000:0.0}m)");

            int bayX = Math.Max(1, (int)(w / 5000));
            int bayY = Math.Max(1, (int)(d / 5000));
            double bayW = w / bayX;
            double bayD = d / bayY;

            for (int ix = 0; ix <= bayX; ix++)
                for (int iy = 0; iy <= bayY; iy++)
                {
                    double cx2 = ox + ix * bayW;
                    double cy2 = oy + iy * bayD;
                    m.Elements.Add(Col(cx2, cy2, oz, oz + floorH * floors, prof, mat));
                }

            for (int fl = 1; fl <= floors; fl++)
            {
                double zF  = oz + fl * floorH;
                double z0F = zF - floorH;

                for (int ix = 0; ix < bayX; ix++)
                    for (int iy = 0; iy <= bayY; iy++)
                    {
                        double x0  = ox + ix * bayW;
                        double cy2 = oy + iy * bayD;
                        m.Elements.Add(Bm(x0, cy2, zF, x0 + bayW, cy2, zF, prof, mat));
                    }

                for (int iy = 0; iy < bayY; iy++)
                    for (int ix = 0; ix <= bayX; ix++)
                    {
                        double cx2 = ox + ix * bayW;
                        double y0  = oy + iy * bayD;
                        m.Elements.Add(Bm(cx2, y0, zF, cx2, y0 + bayD, zF, prof, mat));
                    }

                m.Elements.Add(Br(ox, oy, z0F, ox + bayW, oy, zF, prof, mat));
                m.Elements.Add(Br(ox, yB, z0F, ox + bayW, yB, zF, prof, mat));
                m.Elements.Add(Br(ox, oy, z0F, ox, oy + bayD, zF, prof, mat));
                m.Elements.Add(Br(xR, oy, z0F, xR, oy + bayD, zF, prof, mat));
            }
            return m;
        }

        // ── Simple Pipe Support ───────────────────────────────────────────
        private BimModel BuildPipeSupport(
            double ox, double oy, double oz,
            double h, double w, string prof, string mat)
        {
            var m = New("Simple Pipe Support");
            m.Elements.Add(Col(ox,     oy, oz, oz + h, prof, mat));
            m.Elements.Add(Col(ox + w, oy, oz, oz + h, prof, mat));
            m.Elements.Add(Bm(ox, oy, oz + h, ox + w, oy, oz + h, prof, mat));
            return m;
        }

        // ── X Bracing ─────────────────────────────────────────────────────
        private BimModel BuildXBracing(
            double ox, double oy, double oz,
            double h, double w, string prof, string mat)
        {
            var m = New("X-Bracing Frame");
            m.Elements.Add(Col(ox,     oy, oz, oz + h, prof, mat));
            m.Elements.Add(Col(ox + w, oy, oz, oz + h, prof, mat));
            m.Elements.Add(Bm(ox, oy, oz + h, ox + w, oy, oz + h, prof, mat));
            m.Elements.Add(Br(ox,     oy, oz, ox + w, oy, oz + h, prof, mat));
            m.Elements.Add(Br(ox + w, oy, oz, ox,     oy, oz + h, prof, mat));
            return m;
        }

        // ── Double Level Rack ───────────────────────────────────────────
        private BimModel BuildDoubleLevelRack(
            double ox, double oy, double oz,
            double h, double w, string prof, string mat)
        {
            var m       = New("Double Level Rack");
            double zMid = oz + h * 0.5;
            m.Elements.Add(Col(ox,     oy, oz, oz + h, prof, mat));
            m.Elements.Add(Col(ox + w, oy, oz, oz + h, prof, mat));
            m.Elements.Add(Bm(ox, oy, zMid,   ox + w, oy, zMid,   prof, mat));
            m.Elements.Add(Bm(ox, oy, oz + h, ox + w, oy, oz + h, prof, mat));
            return m;
        }

        // ── Four Column Rack ──────────────────────────────────────────────
        private BimModel BuildFourColumnRack(
            double ox, double oy, double oz,
            double h, double w, double d,
            string prof, string mat)
        {
            var m     = New("Four Column Rack");
            double zT = oz + h;
            double xR = ox + w, yB = oy + d;

            m.Elements.Add(Col(ox, oy, oz, zT, prof, mat));
            m.Elements.Add(Col(xR, oy, oz, zT, prof, mat));
            m.Elements.Add(Col(ox, yB, oz, zT, prof, mat));
            m.Elements.Add(Col(xR, yB, oz, zT, prof, mat));
            m.Elements.Add(Bm(ox, oy, zT, xR, oy, zT, prof, mat));
            m.Elements.Add(Bm(ox, yB, zT, xR, yB, zT, prof, mat));
            m.Elements.Add(Bm(ox, oy, zT, ox, yB, zT, prof, mat));
            m.Elements.Add(Bm(xR, oy, zT, xR, yB, zT, prof, mat));
            return m;
        }

        // ── Portal Frame ──────────────────────────────────────────────────
        private BimModel BuildPortalFrame(
            double ox, double oy, double oz,
            double h, double w, double d,
            bool brace, string prof, string mat)
        {
            var m       = New("Portal Frame");
            double xR   = ox + w;
            double xMid = ox + w / 2.0;
            double zE   = oz + h;
            double zA   = zE + d;

            m.Elements.Add(Col(ox, oy, oz, zE, prof, mat));
            m.Elements.Add(Col(xR, oy, oz, zE, prof, mat));
            m.Elements.Add(Bm(ox, oy, zE, xMid, oy, zA, prof, mat));
            m.Elements.Add(Bm(xR, oy, zE, xMid, oy, zA, prof, mat));

            if (brace)
            {
                double hH = h * 0.15;
                m.Elements.Add(Br(ox, oy, zE - hH, ox + hH, oy, zE, prof, mat));
                m.Elements.Add(Br(xR, oy, zE - hH, xR - hH, oy, zE, prof, mat));
            }
            return m;
        }

        // ── Telecom Tower ─────────────────────────────────────────────────
        private BimModel BuildTelecomTower(
            double ox, double oy, double oz,
            double h, double w, string prof, string mat)
        {
            var m      = New("Telecom Tower");
            int levels = 6;
            double lh  = h / levels;
            double hw  = w / 2.0;

            for (int i = 0; i < levels; i++)
            {
                double z0L   = oz + i * lh;
                double z1L   = z0L + lh;
                double taper = 1.0 - (i * 0.7 / levels);
                double bw    = hw * taper;

                m.Elements.Add(Col(ox - bw, oy - bw, z0L, z1L, prof, mat));
                m.Elements.Add(Col(ox + bw, oy - bw, z0L, z1L, prof, mat));
                m.Elements.Add(Col(ox - bw, oy + bw, z0L, z1L, prof, mat));
                m.Elements.Add(Col(ox + bw, oy + bw, z0L, z1L, prof, mat));
                m.Elements.Add(Bm(ox - bw, oy - bw, z1L, ox + bw, oy - bw, z1L, prof, mat));
                m.Elements.Add(Bm(ox - bw, oy + bw, z1L, ox + bw, oy + bw, z1L, prof, mat));
                m.Elements.Add(Bm(ox - bw, oy - bw, z1L, ox - bw, oy + bw, z1L, prof, mat));
                m.Elements.Add(Bm(ox + bw, oy - bw, z1L, ox + bw, oy + bw, z1L, prof, mat));
                m.Elements.Add(Br(ox - bw, oy - bw, z0L, ox + bw, oy + bw, z1L, prof, mat));
                m.Elements.Add(Br(ox + bw, oy - bw, z0L, ox - bw, oy + bw, z1L, prof, mat));
            }
            return m;
        }

        // ── Equipment Platform ────────────────────────────────────────────
        private BimModel BuildEquipmentPlatform(
            double ox, double oy, double oz,
            double h, double w, double d,
            string prof, string mat)
        {
            var m     = New("Equipment Platform");
            double xR = ox + w, yB = oy + d, zT = oz + h;

            m.Elements.Add(Col(ox, oy, oz, zT, prof, mat));
            m.Elements.Add(Col(xR, oy, oz, zT, prof, mat));
            m.Elements.Add(Col(ox, yB, oz, zT, prof, mat));
            m.Elements.Add(Col(xR, yB, oz, zT, prof, mat));
            m.Elements.Add(Bm(ox, oy, zT, xR, oy, zT, prof, mat));
            m.Elements.Add(Bm(ox, yB, zT, xR, yB, zT, prof, mat));
            m.Elements.Add(Bm(ox, oy, zT, ox, yB, zT, prof, mat));
            m.Elements.Add(Bm(xR, oy, zT, xR, yB, zT, prof, mat));
            m.Elements.Add(Bm(ox, oy + d / 2.0, zT, xR, oy + d / 2.0, zT, prof, mat));
            m.Elements.Add(Bm(ox, oy, oz + h * 0.5, xR, oy, oz + h * 0.5, prof, mat));
            m.Elements.Add(Bm(ox, yB, oz + h * 0.5, xR, yB, oz + h * 0.5, prof, mat));
            m.Elements.Add(Br(ox, oy, oz, xR, oy, zT, prof, mat));
            m.Elements.Add(Br(ox, yB, oz, xR, yB, zT, prof, mat));
            m.Elements.Add(Br(ox, oy, oz, ox, yB, zT, prof, mat));
            m.Elements.Add(Br(xR, oy, oz, xR, yB, zT, prof, mat));
            return m;
        }

        // ── Multi-Bay Pipe Rack ───────────────────────────────────────────
        private BimModel BuildMultiBayPipeRack(
            double ox, double oy, double oz,
            double h, double w, double d,
            string prof, string mat)
        {
            var m       = New("Multi-Bay Pipe Rack");
            int bays    = 3;
            double bayW = w;

            for (int i = 0; i <= bays; i++)
            {
                double xi = ox + i * bayW;
                m.Elements.Add(Col(xi, oy,     oz, oz + h, prof, mat));
                m.Elements.Add(Col(xi, oy + d, oz, oz + h, prof, mat));

                if (i < bays)
                {
                    double xi2 = xi + bayW;
                    m.Elements.Add(Bm(xi, oy,     oz + h, xi2, oy,     oz + h, prof, mat));
                    m.Elements.Add(Bm(xi, oy + d, oz + h, xi2, oy + d, oz + h, prof, mat));
                }

                m.Elements.Add(Bm(xi, oy, oz + h,       xi, oy + d, oz + h,       prof, mat));
                m.Elements.Add(Bm(xi, oy, oz + h * 0.6, xi, oy + d, oz + h * 0.6, prof, mat));
            }
            return m;
        }

        // ── Staircase ─────────────────────────────────────────────────────
        private BimModel BuildStaircase(
            double ox, double oy, double oz,
            double h, double w, string prof, string mat)
        {
            var m     = New("Staircase Structure");
            int steps = 8;
            double sh = h / steps;
            double sd = w / steps;

            for (int i = 0; i < steps; i++)
            {
                double z0S = oz + i * sh;
                double z1S = z0S + sh;
                double y0S = oy + i * sd;
                double y1S = y0S + sd;

                m.Elements.Add(Col(ox, y0S, z0S, z1S, prof, mat));
                m.Elements.Add(Bm(ox, y0S, z1S, ox, y1S, z1S, prof, mat));
                m.Elements.Add(Br(ox, y0S, z0S, ox, y1S, z1S, prof, mat));
            }

            m.Elements.Add(Br(ox,     oy, oz, ox,     oy + w, oz + h, prof, mat));
            m.Elements.Add(Br(ox + w, oy, oz, ox + w, oy + w, oz + h, prof, mat));
            return m;
        }

        // ── Roof Truss ────────────────────────────────────────────────────
        private BimModel BuildTruss(
            double ox, double oy, double oz,
            double h, double w, string prof, string mat)
        {
            var m      = New("Roof Truss");
            int panels = 6;
            double pw  = w / panels;

            for (int i = 0; i < panels; i++)
                m.Elements.Add(Bm(ox + i * pw, oy, oz,
                                  ox + (i + 1) * pw, oy, oz, prof, mat));

            for (int i = 0; i < panels / 2; i++)
            {
                double x0T = ox + i * pw;
                double x1T = x0T + pw;
                double z0T = oz + h * i / (panels / 2.0);
                double z1T = oz + h * (i + 1) / (panels / 2.0);
                m.Elements.Add(Bm(x0T, oy, z0T, x1T, oy, z1T, prof, mat));
            }
            for (int i = panels / 2; i < panels; i++)
            {
                double x0T = ox + i * pw;
                double x1T = x0T + pw;
                double z0T = oz + h * (panels - i) / (panels / 2.0);
                double z1T = oz + h * (panels - i - 1) / (panels / 2.0);
                m.Elements.Add(Bm(x0T, oy, z0T, x1T, oy, z1T, prof, mat));
            }

            for (int i = 1; i < panels; i++)
            {
                double xi  = ox + i * pw;
                double zTp = i <= panels / 2
                    ? oz + h * i / (panels / 2.0)
                    : oz + h * (panels - i) / (panels / 2.0);
                m.Elements.Add(Br(xi, oy, oz, xi, oy, zTp, prof, mat));
            }
            return m;
        }

        // ── Canopy ────────────────────────────────────────────────────────
        private BimModel BuildCanopy(
            double ox, double oy, double oz,
            double h, double w, string prof, string mat)
        {
            var m     = New("Canopy / Cantilever");
            double xR = ox + w;
            double d  = w * 0.8;

            m.Elements.Add(Col(ox, oy, oz, oz + h, prof, mat));
            m.Elements.Add(Col(xR, oy, oz, oz + h, prof, mat));
            m.Elements.Add(Bm(ox, oy, oz + h,       ox, oy + d, oz + h * 0.85, prof, mat));
            m.Elements.Add(Bm(xR, oy, oz + h,       xR, oy + d, oz + h * 0.85, prof, mat));
            m.Elements.Add(Bm(ox, oy + d, oz + h * 0.85, xR, oy + d, oz + h * 0.85, prof, mat));
            m.Elements.Add(Bm(ox, oy, oz + h, xR, oy, oz + h, prof, mat));
            m.Elements.Add(Br(ox, oy, oz + h * 0.6, ox, oy + d, oz + h * 0.85, prof, mat));
            m.Elements.Add(Br(xR, oy, oz + h * 0.6, xR, oy + d, oz + h * 0.85, prof, mat));
            return m;
        }

        // ── Water Tank Frame ──────────────────────────────────────────────
        private BimModel BuildTankFrame(
            double ox, double oy, double oz,
            double h, double w, double d,
            string prof, string mat)
        {
            var m       = New("Water Tank Support Frame");
            double xR   = ox + w, yB = oy + d, zT = oz + h;
            double zMid = oz + h * 0.6;

            m.Elements.Add(Col(ox, oy, oz, zT, prof, mat));
            m.Elements.Add(Col(xR, oy, oz, zT, prof, mat));
            m.Elements.Add(Col(ox, yB, oz, zT, prof, mat));
            m.Elements.Add(Col(xR, yB, oz, zT, prof, mat));
            m.Elements.Add(Bm(ox, oy, zT,   xR, oy, zT,   prof, mat));
            m.Elements.Add(Bm(ox, yB, zT,   xR, yB, zT,   prof, mat));
            m.Elements.Add(Bm(ox, oy, zT,   ox, yB, zT,   prof, mat));
            m.Elements.Add(Bm(xR, oy, zT,   xR, yB, zT,   prof, mat));
            m.Elements.Add(Bm(ox, oy, zMid, xR, oy, zMid, prof, mat));
            m.Elements.Add(Bm(ox, yB, zMid, xR, yB, zMid, prof, mat));
            m.Elements.Add(Bm(ox, oy, zMid, ox, yB, zMid, prof, mat));
            m.Elements.Add(Bm(xR, oy, zMid, xR, yB, zMid, prof, mat));
            m.Elements.Add(Br(ox, oy, oz,   xR, oy, zMid, prof, mat));
            m.Elements.Add(Br(xR, oy, oz,   ox, oy, zMid, prof, mat));
            m.Elements.Add(Br(ox, yB, oz,   xR, yB, zMid, prof, mat));
            m.Elements.Add(Br(xR, yB, oz,   ox, yB, zMid, prof, mat));
            m.Elements.Add(Br(ox, oy, zMid, ox, yB, zT,   prof, mat));
            m.Elements.Add(Br(ox, yB, zMid, ox, oy, zT,   prof, mat));
            return m;
        }

        // ═════════════════════════════════════════════════════════════════
        //  AI API FALLBACK — UNCHANGED
        // ═════════════════════════════════════════════════════════════════
        private async System.Threading.Tasks.Task<BimModel> PlanWithAI(
            string prompt, ModelContext ctx,
            string profile, string material,
            double ox, double oy, double oz)
        {
            try
            {
                var payload = new
                {
                    command =
                        $"Create structural elements JSON for: {prompt}. " +
                        $"Use profile={profile}, material={material}, " +
                        $"origin=({ox},{oy},{oz}). " +
                        $"Return ONLY JSON array of elements with fields: " +
                        $"type(COLUMN/BEAM/BRACE), startX, startY, startZ, " +
                        $"endX, endY, endZ, profile, material, name"
                };

                string json    = JsonConvert.SerializeObject(payload);
                var content    = new StringContent(json, Encoding.UTF8, "application/json");
                var resp       = await _http.PostAsync("http://127.0.0.1:8000/agent", content);
                string body    = await resp.Content.ReadAsStringAsync();
                dynamic data   = JsonConvert.DeserializeObject(body);
                string aiResp  = data?.response?.ToString() ?? "";

                int start = aiResp.IndexOf('[');
                int end   = aiResp.LastIndexOf(']');
                if (start < 0 || end < 0) return null;

                string jsonArr = aiResp.Substring(start, end - start + 1);
                var elements   = JsonConvert.DeserializeObject<List<dynamic>>(jsonArr);

                var model = New($"AI: {prompt}");
                foreach (var el in elements)
                {
                    model.Elements.Add(new BimElement
                    {
                        Type     = el.type?.ToString()     ?? "BEAM",
                        Profile  = el.profile?.ToString()  ?? profile,
                        Material = el.material?.ToString() ?? material,
                        Name     = el.name?.ToString()     ?? "AI_MEMBER",
                        StartX   = (double)(el.startX ?? ox),
                        StartY   = (double)(el.startY ?? oy),
                        StartZ   = (double)(el.startZ ?? oz),
                        EndX     = (double)(el.endX   ?? ox + 1000),
                        EndY     = (double)(el.endY   ?? oy),
                        EndZ     = (double)(el.endZ   ?? oz),
                    });
                }
                return model;
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[Planner] AI API error: {ex.Message}");
                return null;
            }
        }

        // ═════════════════════════════════════════════════════════════════
        //  HELPER FACTORIES — UNCHANGED
        // ═════════════════════════════════════════════════════════════════
        private BimModel New(string name) => new BimModel { Name = name };

        private BimElement Col(double x, double y, double z0, double z1,
                               string prof, string mat) => new BimElement
        {
            Type = "COLUMN", Profile = prof, Material = mat, Name = "COLUMN",
            StartX = x, StartY = y, StartZ = z0,
            EndX   = x, EndY   = y, EndZ   = z1
        };

        private BimElement Bm(double x0, double y0, double z0,
                              double x1, double y1, double z1,
                              string prof, string mat) => new BimElement
        {
            Type = "BEAM", Profile = prof, Material = mat, Name = "BEAM",
            StartX = x0, StartY = y0, StartZ = z0,
            EndX   = x1, EndY   = y1, EndZ   = z1
        };

        private BimElement Br(double x0, double y0, double z0,
                              double x1, double y1, double z1,
                              string prof, string mat) => new BimElement
        {
            Type = "BRACE", Profile = prof, Material = mat, Name = "BRACE",
            StartX = x0, StartY = y0, StartZ = z0,
            EndX   = x1, EndY   = y1, EndZ   = z1
        };

        // ── Dimension extractor — UNCHANGED ──────────────────────────────
        private double ExtractDim(string lower, string kw1, string kw2, double def)
        {
            foreach (var kw in new[] { kw1, kw2 })
            {
                int idx = lower.IndexOf(kw);
                if (idx < 0) continue;
                string after = lower
                    .Substring(idx + kw.Length)
                    .TrimStart(':', '=', ' ');
                string num = "";
                foreach (char c in after)
                {
                    if (char.IsDigit(c) || c == '.') num += c;
                    else break;
                }
                if (!double.TryParse(num, out double val)) continue;
                string unit = after.Substring(num.Length).TrimStart();
                if (unit.StartsWith("mm")) return val;
                if (unit.StartsWith("cm")) return val * 10;
                if (unit.StartsWith("m"))  return val * 1000;
                return val < 100 ? val * 1000 : val;
            }
            return def;
        }

        private bool ContainsAny(string text, params string[] keywords)
        {
            foreach (var kw in keywords)
                if (text.Contains(kw)) return true;
            return false;
        }
    }
}