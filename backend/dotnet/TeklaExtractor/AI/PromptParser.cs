using System;
using System.Collections.Generic;
using System.Linq;
using TeklaExtractor.Models;

namespace TeklaExtractor.AI
{
    public static class PromptParser
    {
        private static readonly Dictionary<int, string[]> StructureKeywords = new Dictionary<int, string[]>
        {
            { 1, new[] { "pipe support", "pipe rack", "simple support", "single support" } },
            { 2, new[] { "x brac", "cross brac", "x-brac", "diagonal brac" } },
            { 3, new[] { "double level", "two level", "double deck", "2 level", "dual rack" } },
            { 4, new[] { "four column", "4 column", "quad column", "wide rack", "4-column" } },
            { 5, new[] { "portal frame", "portal", "gable frame", "shed frame" } },
        };

        private static readonly Dictionary<string, string> ProfileHints = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            { "heavy",  "HEA300" }, { "light", "HEA160" }, { "medium", "HEA200" },
            { "hea100", "HEA100" }, { "hea160", "HEA160" }, { "hea200", "HEA200" },
            { "hea240", "HEA240" }, { "hea300", "HEA300" },
            { "ipe100", "IPE100" }, { "ipe160", "IPE160" }, { "ipe200", "IPE200" },
        };

        public static StructureRequest Parse(string prompt)
        {
            if (string.IsNullOrWhiteSpace(prompt))
                throw new ArgumentException("Prompt empty hai!", nameof(prompt));

            string lower = prompt.ToLowerInvariant();
            var request  = new StructureRequest();

            request.StructureType = DetectStructureType(lower);
            request.StructureName = GetStructureName(request.StructureType);
            request.Bracing       = lower.Contains("brac");
            request.DoubleDeck    = lower.Contains("double") || lower.Contains("two level") || lower.Contains("dual");

            int cols;
            if (TryExtractNumber(lower, new[] { "column", "col", "pillar" }, out cols))
                request.Columns = Math.Max(2, cols);

            request.Height = ExtractDimension(lower, "height", "h", request.Height);
            request.Width  = ExtractDimension(lower, "width",  "w", request.Width);
            request.Depth  = ExtractDimension(lower, "depth",  "d", request.Depth);

            string profile = DetectProfile(lower);
            if (profile != null) request.ColumnProfile = profile;

            if (request.StructureType == 1)
            {
                int pipes;
                request.Pipes = TryExtractNumber(lower, new[] { "pipe" }, out pipes) ? pipes : 1;
            }

            Console.WriteLine("[PromptParser] Parsed → " + request);
            return request;
        }

        private static int DetectStructureType(string lower)
        {
            foreach (var kvp in StructureKeywords)
                foreach (var kw in kvp.Value)
                    if (lower.Contains(kw))
                        return kvp.Key;

            return lower.Contains("brac") ? 2 : 1;
        }

        private static string GetStructureName(int type)
        {
            switch (type)
            {
                case 1: return "Simple Pipe Support";
                case 2: return "X-Bracing Support";
                case 3: return "Double Level Rack";
                case 4: return "Four Column Rack";
                case 5: return "Portal Frame";
                default: return "Unknown Structure";
            }
        }

        private static bool TryExtractNumber(string lower, string[] keywords, out int result)
        {
            result = 0;
            foreach (var kw in keywords)
            {
                int idx = lower.IndexOf(kw, StringComparison.Ordinal);
                if (idx < 0) continue;

                // Check digit BEFORE keyword e.g. "4 column"
                string before = lower.Substring(0, idx).TrimEnd();
                string[] parts = before.Split(' ');
                string last = parts.Length > 0 ? parts[parts.Length - 1] : "";
                if (int.TryParse(last, out result)) return true;

                // Check digit AFTER keyword e.g. "columns: 4"
                string after = lower.Substring(idx + kw.Length).TrimStart(':', ' ');
                string token = "";
                foreach (char c in after)
                {
                    if (char.IsDigit(c)) token += c;
                    else break;
                }
                if (int.TryParse(token, out result)) return true;
            }
            return false;
        }

        private static double ExtractDimension(string lower, string keyword, string shortKey, double defaultVal)
        {
            foreach (var kw in new[] { keyword, shortKey })
            {
                int idx = lower.IndexOf(kw, StringComparison.Ordinal);
                if (idx < 0) continue;

                string after = lower.Substring(idx + kw.Length).TrimStart(':', '=', ' ');
                string numStr = "";
                foreach (char c in after)
                {
                    if (char.IsDigit(c) || c == '.') numStr += c;
                    else break;
                }
                double val;
                if (!double.TryParse(numStr, out val)) continue;

                string unit = after.Substring(numStr.Length).TrimStart().ToLower();
                if (unit.StartsWith("mm")) return val;
                if (unit.StartsWith("cm")) return val * 10;
                if (unit.StartsWith("m"))  return val * 1000;

                return val < 100 ? val * 1000 : val;
            }
            return defaultVal;
        }

        private static string DetectProfile(string lower)
        {
            foreach (var kvp in ProfileHints)
                if (lower.Contains(kvp.Key.ToLower()))
                    return kvp.Value;
            return null;
        }
    }
}