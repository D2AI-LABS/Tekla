using System;
using System.Collections.Generic;
using System.IO;
using Newtonsoft.Json;
using Tekla.Structures.Geometry3d;
using Tekla.Structures.Model;

namespace TeklaExtractor.Services
{
    /// <summary>
    /// When the catalog API returns nothing (common in Educational Tekla),
    /// probe the model by attempting short test inserts per structural role.
    /// </summary>
    internal static class ProfileInsertProbe
    {
        private static readonly Dictionary<string, string> RoleProfile =
            new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

        private static string _material;
        private static bool _probed;

        private static readonly string[] ColumnProfiles =
        {
            "HEA200","HEA160","HEA140","HEA120","HEA100","HEA80",
            "HEB200","HEB160","HEB140","HEB120","HEB100",
            "IPE200","IPE160","IPE140","IPE120",
            "UC152X152X23","UC203X203X46",
            "W8X31","W8X18","W6X9",
            "SHS100X100X5","SHS80X80X5","150X150X6.3RHS","100X100X6SHS",
        };

        private static readonly string[] BeamProfiles =
        {
            "IPE300","IPE240","IPE200","IPE180","IPE160","IPE140","IPE120","IPE100",
            "HEA200","HEA160","HEB200","HEB160",
            "UB203X133X25","W8X18","W6X9",
            "100X50X5RHS","RHS100X50X5",
        };

        private static readonly string[] SecondaryProfiles =
        {
            "L60X60X5","L50X50X5","L80X80X8","L100X100X8",
            "CHS114.3X5","CHS88.9X4","CHS76.1X4",
        };

        private static readonly string[] Materials =
        {
            "S275","S355","S235","S275JR","S355JR","S235JR",
            "Fe 410","A36","Steel_Undefined",
        };

        public static void Discover(Model model, string logDir = null)
        {
            if (_probed || model == null || !model.GetConnectionStatus()) return;
            _probed = true;

            ProbeRole(model, "COLUMN", ColumnProfiles);
            ProbeRole(model, "BEAM",   BeamProfiles);
            ProbeRole(model, "SECONDARY", SecondaryProfiles);

            if (RoleProfile.Count > 0)
            {
                Console.WriteLine(
                    "[ProfileProbe] Working profiles — " +
                    string.Join(", ", RoleProfile) +
                    (_material != null ? $" (mat={_material})" : ""));
            }
            else
            {
                Console.WriteLine("[ProfileProbe] No working profile found — inserts may fail.");
            }

            if (!string.IsNullOrEmpty(logDir))
            {
                try
                {
                    File.WriteAllText(
                        Path.Combine(logDir, "profile_probe.json"),
                        JsonConvert.SerializeObject(new { profiles = RoleProfile, material = _material }, Formatting.Indented));
                }
                catch { /* ignore */ }
            }
        }

        public static string GetWorkingProfile(string memberType)
        {
            if (memberType == null) return null;
            RoleProfile.TryGetValue(memberType.ToUpperInvariant(), out string prof);
            return prof;
        }

        public static string GetWorkingMaterial() => _material;

        public static IEnumerable<string> PrependWorking(string memberType, IEnumerable<string> candidates)
        {
            string working = GetWorkingProfile(memberType);
            if (!string.IsNullOrEmpty(working))
                yield return working;

            foreach (var c in candidates)
            {
                if (!string.Equals(c, working, StringComparison.OrdinalIgnoreCase))
                    yield return c;
            }
        }

        private static void ProbeRole(Model model, string role, IEnumerable<string> profiles)
        {
            if (RoleProfile.ContainsKey(role)) return;

            var testSegments = new[]
            {
                (new Point(200000, 200000, 0),     new Point(200000, 200000, 500)),
                (new Point(200000, 200000, 0),     new Point(200500, 200000, 0)),
                (new Point(0, 0, -3000),           new Point(0, 0, -2500)),
            };

            foreach (var (start, end) in testSegments)
            {
                foreach (var prof in profiles)
                {
                    foreach (var mat in Materials)
                    {
                        if (!TryInsertAndDelete(model, start, end, prof, mat, role))
                            continue;

                        RoleProfile[role] = prof;
                        _material = mat;
                        Console.WriteLine($"[ProfileProbe] {role} → {prof} / {mat}");
                        return;
                    }
                }
            }
        }

        private static bool TryInsertAndDelete(
            Model model, Point start, Point end,
            string profile, string material, string role)
        {
            if (string.IsNullOrWhiteSpace(profile) || string.IsNullOrWhiteSpace(material))
                return false;

            Beam beam = null;
            try
            {
                beam = new Beam(start, end)
                {
                    Profile  = { ProfileString  = profile },
                    Material = { MaterialString = material },
                    Name     = "PROBE",
                    Class    = role == "COLUMN" ? "1" : role == "BEAM" ? "2" : "3",
                };

                if (role == "COLUMN")
                    beam.Position.Rotation = Position.RotationEnum.TOP;

                if (!beam.Insert()) return false;

                model.CommitChanges();
                bool deleted = beam.Delete();
                if (deleted) model.CommitChanges();
                return true;
            }
            catch
            {
                try
                {
                    if (beam != null) beam.Delete();
                    model.CommitChanges();
                }
                catch { /* ignore */ }
                return false;
            }
        }
    }
}
