using System;
using System.Collections.Generic;
using Tekla.Structures.Geometry3d;
using Tekla.Structures.Model;
using TeklaExtractor.Models;

namespace TeklaExtractor.Generator
{
    public class StructureGenerator
    {
        private readonly Model _model;

        // Instance-level cache (NOT static) — scoped to this generator/run only,
        // so it can't leak a "working" material/profile into an unrelated model
        // or a later, unrelated Generate() call.
        private string _workingMaterial = null;
        private string _workingColProfile = null;
        private string _workingBmProfile = null;

        // Every time CreateBeamSafe has to fall back to a profile/material that
        // was NOT what the request asked for, it logs a warning here. These are
        // surfaced loudly at the end of Generate() so a substitution is never
        // silently missed.
        private readonly List<string> _fallbackWarnings = new List<string>();
        public IReadOnlyList<string> FallbackWarnings => _fallbackWarnings;

        public StructureGenerator(Model model)
        {
            _model = model ?? throw new ArgumentNullException(nameof(model));
        }

        public bool Generate(StructureRequest req)
        {
            if (!req.IsValid(out string error))
            {
                Console.WriteLine($"[Generator] Invalid request: {error}");
                return false;
            }

            Console.WriteLine($"[Generator] Creating → {req}");
            Console.WriteLine($"[Generator] Material: {req.Material} | ColProfile: {req.ColumnProfile} | BmProfile: {req.BeamProfile}");

            bool ok = req.StructureType switch
            {
                1 => CreateStructure1(req),
                2 => CreateStructure2(req),
                3 => CreateStructure3(req),
                4 => CreateStructure4(req),
                5 => CreateStructure5(req),
                _ => throw new ArgumentOutOfRangeException(
                        nameof(req.StructureType), req.StructureType,
                        $"Unknown StructureType: {req.StructureType}")
            };

            if (ok)
            {
                _model.CommitChanges();
                Console.WriteLine("[Generator] ✅ CommitChanges() done");
            }

            if (_fallbackWarnings.Count > 0)
            {
                Console.WriteLine();
                Console.WriteLine("⚠️ ===================== FALLBACKS WERE USED ===================== ⚠️");
                Console.WriteLine("⚠️ The structure was NOT built entirely with the requested material/profile.");
                Console.WriteLine("⚠️ Review every line below before treating this model as final:");
                foreach (var w in _fallbackWarnings)
                    Console.WriteLine($"⚠️   {w}");
                Console.WriteLine("⚠️ ================================================================ ⚠️");
                Console.WriteLine();
            }

            return ok;
        }

        // ─── Structure 1: Simple Pipe Support ────────────────────────────────
        public bool CreateStructure1(StructureRequest req)
        {
            try
            {
                double x0 = req.OriginX;
                double y0 = req.OriginY;
                double z0 = req.OriginZ;

                bool ok = true;
                ok &= CreateBeamSafe(new Point(x0,             y0, z0), new Point(x0,             y0, z0 + req.Height), req.ColumnProfile, req.Material, "COLUMN");
                ok &= CreateBeamSafe(new Point(x0 + req.Width, y0, z0), new Point(x0 + req.Width, y0, z0 + req.Height), req.ColumnProfile, req.Material, "COLUMN");
                ok &= CreateBeamSafe(new Point(x0,             y0, z0 + req.Height), new Point(x0 + req.Width, y0, z0 + req.Height), req.BeamProfile, req.Material, "BEAM");
                return ok;
            }
            catch (Exception ex) { Console.WriteLine($"[Structure1] Error: {ex.Message}"); return false; }
        }

        // ─── Structure 2: X-Bracing ───────────────────────────────────────────
        public bool CreateStructure2(StructureRequest req)
        {
            try
            {
                double x0 = req.OriginX;
                double y0 = req.OriginY;
                double z0 = req.OriginZ;

                bool ok = true;
                ok &= CreateBeamSafe(new Point(x0,             y0, z0),              new Point(x0,             y0, z0 + req.Height), req.ColumnProfile,  req.Material, "COLUMN");
                ok &= CreateBeamSafe(new Point(x0 + req.Width, y0, z0),              new Point(x0 + req.Width, y0, z0 + req.Height), req.ColumnProfile,  req.Material, "COLUMN");
                ok &= CreateBeamSafe(new Point(x0,             y0, z0 + req.Height), new Point(x0 + req.Width, y0, z0 + req.Height), req.BeamProfile,    req.Material, "BEAM");

                // X bracing — always added for structure type 2
                ok &= CreateBeamSafe(new Point(x0,             y0, z0),              new Point(x0 + req.Width, y0, z0 + req.Height), req.BracingProfile, req.Material, "BRACE");
                ok &= CreateBeamSafe(new Point(x0 + req.Width, y0, z0),              new Point(x0,             y0, z0 + req.Height), req.BracingProfile, req.Material, "BRACE");

                return ok;
            }
            catch (Exception ex) { Console.WriteLine($"[Structure2] Error: {ex.Message}"); return false; }
        }

        // ─── Structure 3: Double Level Rack ──────────────────────────────────
        public bool CreateStructure3(StructureRequest req)
        {
            try
            {
                double x0   = req.OriginX;
                double y0   = req.OriginY;
                double z0   = req.OriginZ;
                double zMid = z0 + req.Height * 0.5;

                bool ok = true;
                ok &= CreateBeamSafe(new Point(x0,             y0, z0),              new Point(x0,             y0, z0 + req.Height), req.ColumnProfile, req.Material, "COLUMN");
                ok &= CreateBeamSafe(new Point(x0 + req.Width, y0, z0),              new Point(x0 + req.Width, y0, z0 + req.Height), req.ColumnProfile, req.Material, "COLUMN");
                ok &= CreateBeamSafe(new Point(x0,             y0, zMid),            new Point(x0 + req.Width, y0, zMid),            req.BeamProfile,   req.Material, "BEAM");
                ok &= CreateBeamSafe(new Point(x0,             y0, z0 + req.Height), new Point(x0 + req.Width, y0, z0 + req.Height), req.BeamProfile,   req.Material, "BEAM");
                return ok;
            }
            catch (Exception ex) { Console.WriteLine($"[Structure3] Error: {ex.Message}"); return false; }
        }

        // ─── Structure 4: Four Column Rack ───────────────────────────────────
        public bool CreateStructure4(StructureRequest req)
        {
            try
            {
                double x0 = req.OriginX;
                double y0 = req.OriginY;
                double z0 = req.OriginZ;
                double zT = z0 + req.Height;

                var p1 = new Point(x0,             y0,             z0);
                var p2 = new Point(x0 + req.Width, y0,             z0);
                var p3 = new Point(x0,             y0 + req.Depth, z0);
                var p4 = new Point(x0 + req.Width, y0 + req.Depth, z0);
                var p1T = new Point(p1.X, p1.Y, zT);
                var p2T = new Point(p2.X, p2.Y, zT);
                var p3T = new Point(p3.X, p3.Y, zT);
                var p4T = new Point(p4.X, p4.Y, zT);

                bool ok = true;
                ok &= CreateBeamSafe(p1, p1T, req.ColumnProfile, req.Material, "COLUMN");
                ok &= CreateBeamSafe(p2, p2T, req.ColumnProfile, req.Material, "COLUMN");
                ok &= CreateBeamSafe(p3, p3T, req.ColumnProfile, req.Material, "COLUMN");
                ok &= CreateBeamSafe(p4, p4T, req.ColumnProfile, req.Material, "COLUMN");
                ok &= CreateBeamSafe(p1T, p2T, req.BeamProfile, req.Material, "BEAM");
                ok &= CreateBeamSafe(p3T, p4T, req.BeamProfile, req.Material, "BEAM");
                ok &= CreateBeamSafe(p1T, p3T, req.BeamProfile, req.Material, "BEAM");
                ok &= CreateBeamSafe(p2T, p4T, req.BeamProfile, req.Material, "BEAM");
                return ok;
            }
            catch (Exception ex) { Console.WriteLine($"[Structure4] Error: {ex.Message}"); return false; }
        }

        // ─── Structure 5: Portal Frame ────────────────────────────────────────
        public bool CreateStructure5(StructureRequest req)
        {
            try
            {
                double x0    = req.OriginX;
                double y0    = req.OriginY;
                double z0    = req.OriginZ;
                double zEave = z0 + req.Height;
                double zApex = zEave + req.Depth;
                double xMid  = x0 + req.Width / 2.0;

                bool ok = true;
                ok &= CreateBeamSafe(new Point(x0,             y0, z0),    new Point(x0,             y0, zEave), req.ColumnProfile, req.Material, "COLUMN");
                ok &= CreateBeamSafe(new Point(x0 + req.Width, y0, z0),    new Point(x0 + req.Width, y0, zEave), req.ColumnProfile, req.Material, "COLUMN");
                ok &= CreateBeamSafe(new Point(x0,             y0, zEave), new Point(xMid,           y0, zApex), req.BeamProfile,   req.Material, "BEAM");
                ok &= CreateBeamSafe(new Point(x0 + req.Width, y0, zEave), new Point(xMid,           y0, zApex), req.BeamProfile,   req.Material, "BEAM");

                if (req.Bracing)
                {
                    double hH = req.Height * 0.15;
                    ok &= CreateBeamSafe(new Point(x0,             y0, zEave - hH), new Point(x0 + hH,             y0, zEave), req.BracingProfile, req.Material, "BRACE");
                    ok &= CreateBeamSafe(new Point(x0 + req.Width, y0, zEave - hH), new Point(x0 + req.Width - hH, y0, zEave), req.BracingProfile, req.Material, "BRACE");
                }
                return ok;
            }
            catch (Exception ex) { Console.WriteLine($"[Structure5] Error: {ex.Message}"); return false; }
        }

        // ─── AUTO-DETECTING CreateBeamSafe ────────────────────────────────────
        // Tries the requested profile/material first. Only if that fails does it
        // search a fallback list to find *something* the Tekla catalog accepts —
        // and every time it has to do that, it records a warning so the
        // substitution is impossible to miss in the output.
        private bool CreateBeamSafe(
            Point  startPoint,
            Point  endPoint,
            string profile,
            string material,
            string name)
        {
            Console.WriteLine($"\n[CreateBeam] {name} | Profile:{profile} | Material:{material}");

            // 1) Always try exactly what was requested first.
            if (TryInsert(startPoint, endPoint, profile, material, name, out string directErr))
            {
                Console.WriteLine($"[CreateBeam] ✅ Used requested profile='{profile}' material='{material}'");
                return true;
            }
            Console.WriteLine($"[CreateBeam] Requested combo failed: {directErr}");

            // 2) If a previous beam of this kind already found a working combo
            //    in *this* run, try that next (still a substitution — warn).
            if (_workingMaterial != null)
            {
                string cachedProfile = name == "COLUMN" ? (_workingColProfile ?? profile)
                                     : name == "BEAM"   ? (_workingBmProfile  ?? profile)
                                     : profile;

                if (TryInsert(startPoint, endPoint, cachedProfile, _workingMaterial, name, out _))
                {
                    string msg = $"{name}: requested profile='{profile}' material='{material}' failed; " +
                                 $"used cached profile='{cachedProfile}' material='{_workingMaterial}' instead.";
                    Console.WriteLine($"[CreateBeam] ⚠️ {msg}");
                    _fallbackWarnings.Add(msg);
                    return true;
                }
            }

            // ── Profile fallbacks (catalog-discovery list, not engineering choices) ──
            string[] colProfiles = {
                "HEA200", "HEB200", "HEA160", "HEB160",
                "HEA300", "HEB300", "UC203X203X46", "W8X31",
                "150X150X6.3RHS", "100X100X6SHS", "IPE200",
            };
            string[] bmProfiles = {
                "IPE200", "IPE160", "IPE300", "IPE240",
                "UB203X133X25", "W8X18", "HEA160", "HEB160",
                "100X50X5RHS", "IPE180",
            };
            string[] brProfiles = {
                "L100X100X10", "L75X75X8", "L80X80X8",
                "L50X50X5", "CHS88.9X4", "50X50X5UA",
            };

            // ── Material fallbacks ────────────────────────────────────────────
            string[] materials = {
                "S235JR", "S355JR", "S275JR",
                "S235", "S355", "S275",
                "Fe 410", "Fe 250", "Fe 510",
                "A36", "A572 Gr.50",
                "Steel_Undefined", "STEEL", "steel",
                "Grade 43", "Grade 50",
                "250", "350",
            };

            string[] profiles = name == "COLUMN" ? colProfiles
                              : name == "BEAM"   ? bmProfiles
                              : brProfiles;

            // 3) Last resort: brute-force the catalog to find *anything* that
            //    inserts. This is meant for diagnosing what your Tekla catalog
            //    actually contains — not for quietly shipping a different
            //    structure than what was asked for. Hence the loud warning.
            foreach (var mat in materials)
            {
                foreach (var prof in profiles)
                {
                    if (TryInsert(startPoint, endPoint, prof, mat, name, out _))
                    {
                        string msg = $"{name}: requested profile='{profile}' material='{material}' is NOT in the " +
                                     $"catalog (or otherwise invalid); substituted profile='{prof}' material='{mat}'. " +
                                     $"Update StructureRequest defaults or fix the catalog entry — do not ship this as-is.";
                        Console.WriteLine($"[CreateBeam] ⚠️⚠️ {msg}");
                        _fallbackWarnings.Add(msg);

                        // Cache for the rest of this run only.
                        _workingMaterial = mat;
                        if (name == "COLUMN") _workingColProfile = prof;
                        if (name == "BEAM")   _workingBmProfile  = prof;

                        return true;
                    }
                }
            }

            Console.WriteLine($"[CreateBeam] ❌ ALL COMBINATIONS FAILED for {name}");
            Console.WriteLine($"[CreateBeam] Action needed: manually check the Tekla catalog for a valid profile+material.");
            return false;
        }

        private bool TryInsert(Point start, Point end, string profile, string material, string name, out string errorMessage)
        {
            errorMessage = null;
            if (string.IsNullOrWhiteSpace(profile) || string.IsNullOrWhiteSpace(material))
            {
                errorMessage = "empty profile or material";
                return false;
            }

            try
            {
                var beam = new Beam
                {
                    StartPoint = start,
                    EndPoint   = end,
                    Profile    = { ProfileString  = profile  },
                    Material   = { MaterialString = material },
                    Name       = name,
                    Class      = name switch
                    {
                        "COLUMN" => "1",
                        "BEAM"   => "2",
                        "BRACE"  => "3",
                        _        => "0"
                    }
                };

                bool inserted = beam.Insert();
                if (!inserted)
                    errorMessage = "Insert() returned false (profile/material likely not in catalog)";
                return inserted;
            }
            catch (Exception ex)
            {
                errorMessage = ex.Message;
                return false;
            }
        }
    }
}