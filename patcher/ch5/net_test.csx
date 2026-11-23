// DeltaruneOnline: adds the Phase 1 network test to Chapter 5 (+ Local Multiplayer v19).
// Run with the UndertaleModTool CLI on the already modded data.win:
//   UndertaleModCli load data.win -s patcher/ch5/net_test.csx -o out/data.win
//
// What it does:
//   1. checks that this really is DELTARUNE Chapter 5 with the local mod, and not patched yet,
//   2. adds a new persistent object obj_dro_net plus helper functions (code in net_test/*.gml),
//   3. adds one anchored line block to obj_time's Create event that creates obj_dro_net.
// If any check fails, the script stops and nothing is saved.

using System;
using System.IO;
using System.Linq;
using UndertaleModLib.Compiler;
using UndertaleModLib.Decompiler;
using Underanalyzer.Decompiler;

EnsureDataLoaded();

void Fail(string message)
{
    throw new Exception("DeltaruneOnline patch stopped: " + message);
}

string codeDir = Path.Combine(Path.GetDirectoryName(Path.GetFullPath(ScriptPath)), "net_test");
if (!Directory.Exists(codeDir))
    Fail("code folder not found: " + codeDir);

// 1. Checks
if (Data.GeneralInfo?.DisplayName?.Content != "DELTARUNE Chapter 5")
    Fail("this is not DELTARUNE Chapter 5 (found \"" + Data.GeneralInfo?.DisplayName?.Content + "\")");
if (Data.GameObjects.ByName("obj_dro_net") is not null)
    Fail("this data.win already contains the DeltaruneOnline test. Start from the original file.");

var timeCreate = Data.Code.ByName("gml_Object_obj_time_Create_0");
if (timeCreate is null)
    Fail("obj_time Create event not found");

// Anchors: lines that must exist in the local mod's version of these events.
var globalDecompileContext = new GlobalDecompileContext(Data);
string Decompile(UndertaleCode code)
{
    return new DecompileContext(globalDecompileContext, code, Data.ToolInfo.DecompilerSettings).DecompileToString();
}
string timeCreateText = Decompile(timeCreate);
if (!timeCreateText.Contains("if (instance_number(obj_time) > 1)"))
    Fail("anchor missing in obj_time Create (game or mod version changed?)");
var timeBeginStep = Data.Code.ByName("gml_Object_obj_time_Step_1");
if (timeBeginStep is null)
    Fail("obj_time Begin Step not found");
string timeBeginStepText = Decompile(timeBeginStep);
if (!timeBeginStepText.Contains("global.input_held[i] = 1;") || !timeBeginStepText.Contains("for (var i = 200; i < 206; i += 1)"))
    Fail("the local mod's input code was not found in obj_time Begin Step. Apply Local Multiplayer v19 first.");

// 2. New object (persistent, so it survives room changes)
var netObj = new UndertaleGameObject()
{
    Name = Data.Strings.MakeString("obj_dro_net"),
    Persistent = true,
    Visible = true,
};
Data.GameObjects.Add(netObj);

var importGroup = new CodeImportGroup(Data);
var files = Directory.GetFiles(codeDir, "*.gml").OrderBy(f => f, StringComparer.Ordinal).ToArray();
if (files.Length == 0)
    Fail("no .gml files in " + codeDir);
foreach (string file in files)
{
    string entryName = Path.GetFileNameWithoutExtension(file);
    importGroup.QueueReplace(entryName, File.ReadAllText(file));
}

// 3. Hook: create the network object once obj_time exists.
importGroup.QueueAppend(timeCreate,
    "if (!instance_exists(obj_dro_net))\n" +
    "{\n" +
    "    instance_create_depth(0, 0, -15000, obj_dro_net);\n" +
    "}\n");

importGroup.Import();

ScriptMessage($"DeltaruneOnline network test added: {files.Length} code files, obj_dro_net, hook in obj_time Create.");
