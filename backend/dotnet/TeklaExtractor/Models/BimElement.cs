using System.Collections.Generic;
using System.Linq;

namespace TeklaExtractor.Models
{
    public class BimElement
    {
        public string Type     { get; set; } = "BEAM";
        public string Profile  { get; set; } = "IPE300";
        public string Material { get; set; } = "S275";
        public string Name     { get; set; } = "";
        public string Class    { get; set; } = "1";

        public double StartX { get; set; }
        public double StartY { get; set; }
        public double StartZ { get; set; }
        public double EndX   { get; set; }
        public double EndY   { get; set; }
        public double EndZ   { get; set; }
    }

    public class BimModel
    {
        public string           Name     { get; set; } = "";
        public List<BimElement> Elements { get; set; } = new List<BimElement>();

        // Computed properties expected by UniversalCreator
        public int TotalCount  => Elements?.Count ?? 0;
        public int ColumnCount => Elements?.Count(e => e.Type?.ToUpper() == "COLUMN") ?? 0;
        public int BeamCount   => Elements?.Count(e => e.Type?.ToUpper() == "BEAM")   ?? 0;
        public int BraceCount  => Elements?.Count(e => e.Type?.ToUpper() == "BRACE")  ?? 0;
    }
}