// Begin Step: start of a frame.
dn_phase = 1;
dn_game_frame += 1;
if (dn_state == 4)
{
    var _ms = delta_time / 1000;
    if (dn_link_frame > 30 && _ms > dn_slow_frame)
        dn_slow_frame = _ms;
    // How many partner frames arrived since the previous Begin Step?
    dn_hist_per_frame[min(dn_arrivals, 3)] += 1;
    if (dn_arrivals == 0)
    {
        dn_quiet += 1;
        if (dn_quiet > dn_quiet_max)
            dn_quiet_max = dn_quiet;
    }
    else
    {
        dn_quiet = 0;
    }
    dn_arrivals = 0;
}
if (dn_freeze_check > 0)
{
    dn_freeze_check = 0;
    dro_net_freeze_compare();
}
