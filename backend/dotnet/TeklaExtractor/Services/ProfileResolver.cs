using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.RegularExpressions;
using Tekla.Structures.Catalogs;

namespace TeklaExtractor.Services
{
    /// <summary>
    /// Loads installed Tekla profile catalog once and resolves requested profiles
    /// to the closest available same-family match. Role-aware fallbacks never
    /// substitute COLUMN/BEAM with angle (L) profiles.
    /// </summary>
    internal static class ProfileResolver
    {
        private static HashSet<string> _catalog;
        private static List<string> _allProfiles;
        private static bool _loaded;

        private static readonly string[] ProbeProfiles =
        {
            "HEA100","HEA120","HEA140","HEA160","HEA180","HEA200","HEA220","HEA240","HEA260","HEA280","HEA300",
            "HEB100","HEB120","HEB140","HEB160","HEB180","HEB200","HEB220","HEB240",
            "HEM100","HEM120","HEM160","HEM200",
            "IPE80","IPE100","IPE120","IPE140","IPE160","IPE180","IPE200","IPE220","IPE240",
            "IPE270","IPE300","IPE330","IPE360","IPE400","IPE450","IPE500",
            "L40X40X4","L50X50X5","L60X60X5","L60X60X6","L70X70X6","L75X75X8","L80X80X8","L100X100X8",
            "CHS76.1X4","CHS88.9X4","CHS114.3X5","CHS139.7X5","CHS168.3X6.3","CHS193.7X6.3","CHS273.1X6.3",
            "RHS100X50X5","RHS100X5","SHS100X100X5","SHS80X80X5",
            "UC152X152X23","UC203X203X46","UB203X133X25",
            "W6X9","W8X18","W8X31",
        };

        public static bool HasCatalog => _catalog != null && _catalog.Count > 0;

        public static void Initialize()
        {
            if (_loaded) return;

            _catalog = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            _allProfiles = new List<string>();

            try
            {
                var handler = new CatalogHandler();
                if (!handler.GetConnectionStatus())
                {
                    Console.WriteLine("[ProfileResolver] Catalog not connected — probing known profiles.");
                    ProbeKnownProfiles();
                    _loaded = true;
                    LogLoadResult("probe-only");
                    return;
                }

                LoadFromEnumerator(handler.GetLibraryProfileItems(), "library");
                if (_catalog.Count == 0)
                    LoadFromEnumerator(handler.GetProfileItems(), "all");

                if (_catalog.Count == 0)
                    ProbeKnownProfiles();

                LogLoadResult("catalog");
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[ProfileResolver] Failed to load catalog: {ex.Message}");
                ProbeKnownProfiles();
            }

            _loaded = true;
        }

        private static void LogLoadResult(string mode)
        {
            if (_catalog.Count > 0)
            {
                Console.WriteLine($"[ProfileResolver] Loaded {_catalog.Count} profiles ({mode}).");
                return;
            }

            Console.WriteLine("[ProfileResolver] No profiles resolved — using role-aware hardcoded fallbacks.");
        }

        private static void LoadFromEnumerator(ProfileItemEnumerator enumerator, string label)
        {
            if (enumerator == null) return;

            try
            {
                enumerator.SelectInstances = true;
                while (enumerator.MoveNext())
                {
                    string name = ExtractProfileName(enumerator.Current);
                    RegisterProfile(name);
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[ProfileResolver] Enumerator ({label}) error: {ex.Message}");
            }
        }

        private static string ExtractProfileName(object current)
        {
            if (current == null) return null;

            if (current is LibraryProfileItem lib && !string.IsNullOrWhiteSpace(lib.ProfileName))
                return lib.ProfileName;

            if (current is ProfileItem item)
            {
                try
                {
                    if (!string.IsNullOrWhiteSpace(item.ParameterString))
                        return item.ParameterString;
                }
                catch { /* optional property */ }
            }

            return current.ToString();
        }

        private static void ProbeKnownProfiles()
        {
            try
            {
                var handler = new CatalogHandler();
                if (!handler.GetConnectionStatus()) return;

                foreach (var candidate in ProbeProfiles)
                {
                    try
                    {
                        var item = new LibraryProfileItem();
                        if (item.Select(candidate))
                            RegisterProfile(candidate);
                    }
                    catch { /* not in catalog */ }
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[ProfileResolver] Probe error: {ex.Message}");
            }
        }

        private static void RegisterProfile(string name)
        {
            name = Normalize(name);
            if (string.IsNullOrEmpty(name)) return;
            if (_catalog.Add(name))
                _allProfiles.Add(name);
        }

        public static IEnumerable<string> GetCandidates(string requested, string memberType)
        {
            Initialize();

            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var list = new List<string>();

            void add(string p)
            {
                p = Normalize(p);
                if (string.IsNullOrEmpty(p) || !seen.Add(p)) return;
                list.Add(p);
            }

            string req = Normalize(requested);
            if (string.IsNullOrEmpty(req)) req = DefaultForRole(memberType);

            add(req);

            if (HasCatalog)
            {
                if (!_catalog.Contains(req))
                {
                    string closest = FindClosestInFamily(req);
                    if (closest != null) add(closest);

                    foreach (var alt in SameFamilyByCloseness(req).Take(12))
                        add(alt);
                }
                else
                {
                    foreach (var alt in SameFamilyByCloseness(req).Where(a => a != req).Take(6))
                        add(alt);
                }

                foreach (var fb in RoleFallbacks(memberType, req))
                {
                    if (_catalog.Contains(Normalize(fb)))
                        add(fb);
                }

                foreach (var fb in RoleFallbacksFromCatalog(memberType, req))
                    add(fb);
            }
            else
            {
                foreach (var fb in RoleFallbacks(memberType, req))
                    add(fb);
            }

            return ProfileInsertProbe.PrependWorking(memberType, list);
        }

        public static bool IsInCatalog(string profile)
        {
            Initialize();
            return !HasCatalog || _catalog.Contains(Normalize(profile));
        }

        private static string Normalize(string p)
        {
            if (string.IsNullOrWhiteSpace(p)) return "";
            return p.ToUpperInvariant()
                    .Replace("×", "X")
                    .Replace("*", "X")
                    .Replace(" ", "");
        }

        private static string DefaultForRole(string memberType)
        {
            switch ((memberType ?? "").ToUpperInvariant())
            {
                case "COLUMN": return "HEA200";
                case "BEAM":   return "IPE200";
                default:       return "L60X60X5";
            }
        }

        private static readonly Regex FamilyRegex = new Regex(
            @"^(HEA|HEB|HEM|IPE|IPN|INP|UPN|UC|UB|WF|W|CHS|SHS|RHS|L|C|P)([\d\.]+)",
            RegexOptions.Compiled | RegexOptions.IgnoreCase);

        private static (string family, double size) ParseFamily(string profile)
        {
            var m = FamilyRegex.Match(Normalize(profile));
            if (!m.Success) return ("", 0);

            double size;
            double.TryParse(m.Groups[2].Value, NumberStyles.Float, CultureInfo.InvariantCulture, out size);
            return (m.Groups[1].Value.ToUpperInvariant(), size);
        }

        private static string FindClosestInFamily(string requested)
        {
            var (fam, size) = ParseFamily(requested);
            if (string.IsNullOrEmpty(fam) || !HasCatalog) return null;

            string best = null;
            double bestDiff = double.MaxValue;

            foreach (var p in _allProfiles)
            {
                var (pf, ps) = ParseFamily(p);
                if (pf != fam) continue;

                double diff = Math.Abs(ps - size);
                if (diff < bestDiff)
                {
                    bestDiff = diff;
                    best = p;
                }
            }

            return best;
        }

        private static IEnumerable<string> SameFamilyByCloseness(string requested)
        {
            var (fam, size) = ParseFamily(requested);
            if (string.IsNullOrEmpty(fam) || !HasCatalog) yield break;

            foreach (var p in _allProfiles
                         .Where(p => ParseFamily(p).family == fam)
                         .OrderBy(p => Math.Abs(ParseFamily(p).size - size)))
                yield return p;
        }

        private static readonly HashSet<string> ColumnFamilies =
            new HashSet<string>(StringComparer.OrdinalIgnoreCase)
            { "HEA", "HEB", "HEM", "UC", "W", "WF", "SHS", "RHS" };

        private static readonly HashSet<string> BeamFamilies =
            new HashSet<string>(StringComparer.OrdinalIgnoreCase)
            { "IPE", "IPN", "INP", "HEA", "HEB", "UB", "W", "WF", "UPN" };

        private static readonly HashSet<string> SecondaryFamilies =
            new HashSet<string>(StringComparer.OrdinalIgnoreCase)
            { "L", "C", "CHS", "SHS", "RHS", "P" };

        private static IEnumerable<string> RoleFallbacks(string memberType, string requested)
        {
            string t = (memberType ?? "").ToUpperInvariant();
            var (fam, _) = ParseFamily(requested);

            if (t == "COLUMN")
            {
                foreach (var p in new[]
                {
                    "HEA200", "HEA160", "HEA140", "HEA120", "HEA100",
                    "HEB200", "HEB160", "HEB140", "HEB120", "HEB100",
                })
                    yield return p;
            }
            else if (t == "BEAM")
            {
                foreach (var p in new[]
                {
                    "IPE300", "IPE240", "IPE200", "IPE180", "IPE160", "IPE140", "IPE120",
                    "HEA200", "HEA160", "HEB200",
                })
                    yield return p;
            }
            else if (fam == "CHS" || Normalize(requested).StartsWith("CHS"))
            {
                foreach (var p in new[]
                {
                    "CHS273.1X6.3", "CHS193.7X6.3", "CHS168.3X6.3",
                    "CHS139.7X5", "CHS114.3X5", "CHS88.9X4", "CHS76.1X4",
                })
                    yield return p;
            }
            else if (fam == "L" || Normalize(requested).StartsWith("L"))
            {
                foreach (var p in new[]
                {
                    "L100X100X8", "L80X80X8", "L70X70X6",
                    "L60X60X5", "L50X50X5", "L40X40X4",
                })
                    yield return p;
            }
            else
            {
                foreach (var p in new[] { "L60X60X5", "L80X80X8", "CHS114.3X5", "HEA200" })
                    yield return p;
            }
        }

        private static IEnumerable<string> RoleFallbacksFromCatalog(string memberType, string requested)
        {
            if (!HasCatalog) yield break;

            string t = (memberType ?? "").ToUpperInvariant();
            HashSet<string> families;

            if (t == "COLUMN") families = ColumnFamilies;
            else if (t == "BEAM") families = BeamFamilies;
            else families = SecondaryFamilies;

            var (reqFam, reqSize) = ParseFamily(requested);

            var ranked = _allProfiles
                .Where(p =>
                {
                    var (f, _) = ParseFamily(p);
                    return families.Contains(f);
                })
                .OrderBy(p =>
                {
                    var (f, s) = ParseFamily(p);
                    if (f == reqFam) return Math.Abs(s - reqSize);
                    return 10000 + s;
                });

            foreach (var p in ranked.Take(15))
                yield return p;
        }
    }
}
