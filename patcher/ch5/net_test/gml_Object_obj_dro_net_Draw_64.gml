// Draw GUI: the on-screen test panel (top left).
if (dn_show == 0 && !dn_prompt && dn_freeze_timer == 0)
    exit;
var _old_font = draw_get_font();
var _old_color = draw_get_color();
var _old_alpha = draw_get_alpha();
var _old_halign = draw_get_halign();
var _old_valign = draw_get_valign();
draw_set_font(fnt_main);
draw_set_halign(fa_left);
draw_set_valign(fa_top);
var _lines = [];
var _cols = [];
var _names = ["OFF", "HOSTING", "CONNECTING", "CHECKING VERSION", "LINKED"];
var _title = "ONLINE TEST: " + _names[dn_state];
if (dn_role == 1)
    _title += " (host, port " + string(dn_port) + ")";
else if (dn_role == 2)
    _title += " (join)";
array_push(_lines, _title);
array_push(_cols, (dn_state == 4) ? c_lime : c_white);
if (dn_freeze_timer > 0)
{
    array_push(_lines, "FREEZE TEST " + string(dn_fz_variant) + " (" + dro_net_fz_name(dn_fz_variant) + "): paused, " + string(dn_freeze_timer) + " frames left");
    array_push(_cols, c_aqua);
}
if (dn_prompt)
{
    var _cursor = " ";
    if (((current_time div 400) mod 2) == 0)
        _cursor = "_";
    array_push(_lines, "Host IP: " + dn_prompt_text + _cursor);
    array_push(_cols, c_yellow);
    array_push(_lines, "Type the IP, Enter = connect, F7 = cancel");
    array_push(_cols, c_white);
}
if (dn_status != "")
{
    array_push(_lines, dn_status);
    array_push(_cols, dn_status_color);
}
if (dn_state == 4 && (current_time - dn_last_msg) > 500)
{
    array_push(_lines, "Partner silent for " + string(current_time - dn_last_msg) + " ms");
    array_push(_cols, c_orange);
}
if (dn_show == 2)
{
    if (dn_state == 4)
    {
        var _stats = dro_net_stat_lines();
        for (var _i = 0; _i < array_length(_stats); _i++)
        {
            array_push(_lines, _stats[_i]);
            array_push(_cols, c_white);
        }
    }
    if (dn_freeze_result != "")
    {
        array_push(_lines, dn_freeze_result);
        array_push(_cols, c_aqua);
    }
    for (var _i = 0; _i < array_length(dn_fz_results); _i++)
    {
        array_push(_lines, dn_fz_results[_i]);
        array_push(_cols, c_aqua);
    }
}
if (dn_show >= 1)
{
    array_push(_lines, "F5 host  F6 join  F7 stop  F8 freeze test  F9 panel");
    array_push(_cols, c_gray);
}
if (dn_show == 2)
{
    array_push(_lines, dn_version);
    array_push(_cols, c_gray);
}
var _w = 0;
for (var _i = 0; _i < array_length(_lines); _i++)
    _w = max(_w, string_width(_lines[_i]));
var _lh = string_height("A");
var _gw = display_get_gui_width();
var _scale = 1;
if ((_w + 12) > _gw)
    _scale = (_gw - 12) / _w;
var _x = 4;
var _y = 4;
draw_set_alpha(0.75);
draw_set_color(c_black);
draw_rectangle(_x, _y, _x + (_w * _scale) + 8, _y + (array_length(_lines) * _lh * _scale) + 6, false);
draw_set_alpha(1);
for (var _i = 0; _i < array_length(_lines); _i++)
{
    draw_set_color(_cols[_i]);
    draw_text_transformed(_x + 4, _y + 3 + (_i * _lh * _scale), _lines[_i], _scale, _scale, 0);
}
draw_set_font(_old_font);
draw_set_color(_old_color);
draw_set_alpha(_old_alpha);
draw_set_halign(_old_halign);
draw_set_valign(_old_valign);
