def generate_cs_file(query: str, context: dict, path: str):

    query = query.lower()

    # =========================
    # PRIMARY MEMBERS (BEAM + COLUMN)
    # =========================
    if "frame" in query or "primary" in query or "beam" in query:

        code = f"""
using Tekla.Structures.Model;
using Tekla.Structures.Geometry3d;

public class Program
{{
    public static void Main()
    {{
        Model model = new Model();

        // PRIMARY MEMBER - BEAM
        Beam beam = new Beam();
        beam.StartPoint = new Point(0, 0, 0);
        beam.EndPoint = new Point(6000, 0, 0);
        beam.Profile.ProfileString = "HEA300";
        beam.Insert();

        // PRIMARY MEMBER - COLUMN
        Beam column = new Beam();
        column.StartPoint = new Point(0, 0, 0);
        column.EndPoint = new Point(0, 0, 4000);
        column.Profile.ProfileString = "HEA200";
        column.Insert();

        model.CommitChanges();
    }}
}}
"""
        with open(path, "w") as f:
            f.write(code)

        return code


    # =========================
    # SECONDARY MEMBERS (BRACING / PURLIN)
    # =========================
    if "secondary" in query or "bracing" in query or "purlin" in query:

        code = f"""
using Tekla.Structures.Model;
using Tekla.Structures.Geometry3d;

public class Program
{{
    public static void Main()
    {{
        Model model = new Model();

        // SECONDARY MEMBER - BRACING
        Beam brace = new Beam();
        brace.StartPoint = new Point(0, 0, 4000);
        brace.EndPoint = new Point(6000, 0, 4000);
        brace.Profile.ProfileString = "L50*50*5";
        brace.Insert();

        model.CommitChanges();
    }}
}}
"""
        with open(path, "w") as f:
            f.write(code)

        return code


    # DEFAULT
    code = """
using Tekla.Structures.Model;

public class Program
{
    public static void Main()
    {
        Model model = new Model();
        model.CommitChanges();
    }
}
"""
    with open(path, "w") as f:
        f.write(code)

    return code