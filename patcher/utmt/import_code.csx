// Compiles every UTMT_IN/<name>.gml into the loaded data (replacing or creating entries).
using System.IO;

EnsureDataLoaded();
string inDir = Environment.GetEnvironmentVariable("UTMT_IN");
if (string.IsNullOrEmpty(inDir)) { ScriptError("UTMT_IN not set"); return; }
// Sorted: the import order decides where new strings, functions and variables land in data.win. Directory.GetFiles
// returns the file system's own order (alphabetical on NTFS, different on ext4, btrfs and tmpfs), so without sorting
// the same sources gave a different data.win on each machine (2026-11-23: 3ea8870e... vs 64eeeb3f..., same UTMT).
string[] files = Directory.GetFiles(inDir, "*.gml");
Array.Sort(files, StringComparer.Ordinal);
var group = new UndertaleModLib.Compiler.CodeImportGroup(Data) { AutoCreateAssets = true };
foreach (string f in files)
    group.QueueReplace(Path.GetFileNameWithoutExtension(f), File.ReadAllText(f));
group.Import();   // throws with every compile error listed
Console.WriteLine("IMPORT_OK " + files.Length + " entries compiled");
