// Decompiles every top-level code entry to UTMT_OUT/<name>.gml (used by dronline_patch.py).
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Collections.Generic;

EnsureDataLoaded();
string outDir = Environment.GetEnvironmentVariable("UTMT_OUT");
if (string.IsNullOrEmpty(outDir)) { ScriptError("UTMT_OUT not set"); return; }
Directory.CreateDirectory(outDir);
GlobalDecompileContext ctx = new(Data);
var settings = Data.ToolInfo.DecompilerSettings;
List<UndertaleCode> todo = Data.Code.Where(c => c != null && c.ParentEntry is null).ToList();
int failed = 0;
await Task.Run(() => Parallel.ForEach(todo, code =>
{
    try
    {
        string text = new Underanalyzer.Decompiler.DecompileContext(ctx, code, settings).DecompileToString();
        File.WriteAllText(Path.Combine(outDir, code.Name.Content + ".gml"), text);
    }
    catch (Exception e)
    {
        System.Threading.Interlocked.Increment(ref failed);
        Console.WriteLine("DECOMPILE_FAIL " + code.Name.Content + ": " + e.Message.Split('\n')[0]);
    }
}));
Console.WriteLine("EXPORT_OK " + todo.Count + " entries, failed " + failed);
