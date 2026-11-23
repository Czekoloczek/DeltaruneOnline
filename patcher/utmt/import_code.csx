// Compiles every UTMT_IN/<name>.gml into the loaded data (replacing or creating entries).
using System.IO;

EnsureDataLoaded();
string inDir = Environment.GetEnvironmentVariable("UTMT_IN");
if (string.IsNullOrEmpty(inDir)) { ScriptError("UTMT_IN not set"); return; }
string[] files = Directory.GetFiles(inDir, "*.gml");
var group = new UndertaleModLib.Compiler.CodeImportGroup(Data) { AutoCreateAssets = true };
foreach (string f in files)
    group.QueueReplace(Path.GetFileNameWithoutExtension(f), File.ReadAllText(f));
group.Import();   // throws with every compile error listed
Console.WriteLine("IMPORT_OK " + files.Length + " entries compiled");
