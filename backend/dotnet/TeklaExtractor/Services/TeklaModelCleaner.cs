using System;
using System.Collections.Generic;
using Tekla.Structures.Model;

namespace TeklaExtractor.Services
{
    internal static class TeklaModelCleaner
    {
        public static int ClearAllBeams(Model model)
        {
            var toDelete = new List<Beam>();

            try
            {
                var selector = model.GetModelObjectSelector()
                                    .GetAllObjectsWithType(ModelObject.ModelObjectEnum.BEAM);
                while (selector.MoveNext())
                {
                    if (selector.Current is Beam beam)
                        toDelete.Add(beam);
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[Cleaner] Scan error: {ex.Message}");
                return 0;
            }

            int deleted = 0;
            foreach (var beam in toDelete)
            {
                try
                {
                    if (beam.Delete()) deleted++;
                }
                catch { /* skip */ }
            }

            if (deleted > 0)
                model.CommitChanges();

            Console.WriteLine($"[Cleaner] Deleted {deleted} member(s) from Tekla model.");
            return deleted;
        }
    }
}
