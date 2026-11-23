// Registers drnet.dll as a GameMaker extension so GML can call drnet_* (used by dronline_patch.py).
// Must run before import_code.csx, so the compiler knows the functions.
using System.Linq;
using System.Collections.Generic;

EnsureDataLoaded();
if (Data.Extensions.Any(e => e.Name?.Content == "drnet"))
{
    Console.WriteLine("EXT_OK drnet already present");
    return;
}
uint nextId = Data.Extensions.SelectMany(e => e.Files).SelectMany(f => f.Functions).Select(fn => fn.ID).DefaultIfEmpty(0u).Max() + 1;
var S = UndertaleExtensionVarType.String;
var D = UndertaleExtensionVarType.Double;
var file = new UndertaleExtensionFile
{
    Filename = Data.Strings.MakeString("drnet.dll"),
    CleanupScript = Data.Strings.MakeString(""),
    InitScript = Data.Strings.MakeString(""),
    Kind = UndertaleExtensionKind.Dll,
};
void Fn(string name, UndertaleExtensionVarType ret, params UndertaleExtensionVarType[] args)
{
    var f = new UndertaleExtensionFunction
    {
        Name = Data.Strings.MakeString(name),
        ExtName = Data.Strings.MakeString(name),
        ID = nextId++,
        Kind = 11,            // same calling convention as the game's own DLL extensions (cdecl)
        RetType = ret,
    };
    foreach (var a in args)
        f.Arguments.Add(new UndertaleExtensionFunctionArg { Type = a });
    file.Functions.Add(f);
    // The compiler only emits a real call for names in the FUNC table; otherwise it compiles a
    // variable call and the game fails with "Variable drnet_... not set before reading it".
    if (Data.Functions.ByName(name) == null)
        Data.Functions.Add(new UndertaleFunction { Name = Data.Strings.MakeString(name) });
}
// GameMaker refuses DLL functions with more than 4 arguments unless all of them are numbers.
Fn("drnet_set_host", D, S);
Fn("drnet_start", D, D, D, D, D, D);
Fn("drnet_connect", D, D);
Fn("drnet_exchange", D, D, D);
Fn("drnet_hash", D, D, D);
Fn("drnet_desync", D);
Fn("drnet_status", S);
Fn("drnet_stop", D);
Fn("drnet_log", D, S);
Fn("drnet_option", D, S, D);
Fn("drnet_set_aux", D, D);
Fn("drnet_aux", D, D);
Fn("drnet_pending_count", D);
Fn("drnet_pending", D, D);
Fn("drnet_delay", D);
Fn("drnet_ping", D);
Fn("drnet_wait", D);
var ext = new UndertaleExtension
{
    Name = Data.Strings.MakeString("drnet"),
    ClassName = Data.Strings.MakeString(""),
    FolderName = Data.Strings.MakeString(""),
    Version = Data.Strings.MakeString("1.0.0"),
};
ext.Files.Add(file);
Data.Extensions.Add(ext);
// GameMaker 2+ keeps one 16-byte product id per extension; the counts must match.
var ids = Data.FORM.EXTN.productIdData;
if (ids != null && ids.Count == Data.Extensions.Count - 1)
    ids.Add(new byte[16]);
Console.WriteLine("EXT_OK drnet added with " + file.Functions.Count + " functions, ids " + (nextId - file.Functions.Count) + ".." + (nextId - 1));
