using System;
using Tekla.Structures.Geometry3d;
using Tekla.Structures.Model;
using TeklaExtractor.Models;

namespace TeklaExtractor.Generator
{
    /// <summary>
    /// BimModel ko Tekla mein actually insert karta hai
    /// Auto-detects working profile/material
    /// </summary>
    public class UniversalCreator
    {
        private readonly Model _model;

        private static string _mat  = null;
        private static string _prof = null;

        private static readonly string[] PROFILES = {
            "L50X50X5","L75X75X8","L100X100X10","L60X60X6","L65X65X7","L80X80X8",
            "HEA100","HEA120","HEA140","HEA160","HEA200",
            "HEB100","HEB120","HEB140","HEB160","HEB200",
            "IPE80","IPE100","IPE120","IPE140","IPE160","IPE200",
            "SHS100X100X5","SHS80X80X5","RHS100X50X5",
            "CHS88.9X4","CHS76.1X4",
            "UC152X152X23","UC203X203X46",
            "W6X9","W8X18","W8X31",
        };

        private static readonly string[] MATERIALS = {
            "S235JR","S355JR","S275JR",
            "S235","S355","S275",
            "Fe 410","Fe 250","Fe 510",
            "IS 2062 E250","IS 2062 E350",
            "A36","A992",
            "Steel_Undefined","STEEL","",
        };

        public UniversalCreator(Model model)
        {
            _model = model ?? throw new ArgumentNullException(nameof(model));
        }

        public bool Create(BimModel bimModel)
        {
            Console.WriteLine($"\n[Creator] ════════════════════════════════");
            Console.WriteLine($"[Creator] Building: {bimModel.Name}");
            Console.WriteLine($"[Creator] Elements: {bimModel.TotalCount} " +
                              $"({bimModel.ColumnCount} cols, {bimModel.BeamCount} beams, {bimModel.BraceCount} braces)");
            Console.WriteLine($"[Creator] ════════════════════════════════");

            int ok = 0, fail = 0;

            foreach (var el in bimModel.Elements)
            {
                bool inserted = InsertElement(el);
                if (inserted) ok++;
                else          fail++;
            }

            if (ok > 0)
            {
                _model.CommitChanges();
                Console.WriteLine($"\n[Creator] ✅ Done: {ok} inserted, {fail} failed");
                Console.WriteLine($"[Creator] Working profile='{_prof}' material='{_mat}'");
                Console.WriteLine($"[Creator] Press Ctrl+F5 in Tekla to see the structure");
                return true;
            }

            Console.WriteLine($"[Creator] ❌ All {fail} elements failed");
            return false;
        }

        private bool InsertElement(BimElement el)
        {
            var s = new Point(el.StartX, el.StartY, el.StartZ);
            var e = new Point(el.EndX,   el.EndY,   el.EndZ);

            string prof = el.Profile  != "" ? el.Profile  : (_prof ?? PROFILES[0]);
            string mat  = el.Material != "" ? el.Material : (_mat  ?? MATERIALS[0]);

            // Fast path — cached
            if (_mat != null && _prof != null)
            {
                if (TryInsert(s, e, _prof, _mat, el.Type, el.Name))
                {
                    Console.WriteLine($"  ✅ {el.Type} [{_prof}/{_mat}]");
                    return true;
                }
            }

            // Auto-detect
            foreach (var m in MATERIALS)
            {
                string[] profs = prof != "" ? new[]{ prof, PROFILES[0], PROFILES[1], PROFILES[2] } : PROFILES;
                foreach (var p in profs)
                {
                    if (TryInsert(s, e, p, m, el.Type, el.Name))
                    {
                        _prof = p; _mat = m;
                        Console.WriteLine($"  ✅ {el.Type} FOUND → [{p}/{m}]");
                        return true;
                    }
                }
            }

            Console.WriteLine($"  ❌ {el.Type} FAILED");
            return false;
        }

        private bool TryInsert(Point s, Point e,
                               string profile, string material,
                               string type, string name)
        {
            try
            {
                var beam = new Beam
                {
                    StartPoint = s, EndPoint = e,
                    Profile    = { ProfileString  = profile  },
                    Material   = { MaterialString = material },
                    Name       = string.IsNullOrEmpty(name) ? type : name,
                    Class      = type switch
                    {
                        "COLUMN" => "1", "BEAM" => "2", "BRACE" => "3", _ => "0"
                    }
                };
                return beam.Insert();
            }
            catch { return false; }
        }
    }
}