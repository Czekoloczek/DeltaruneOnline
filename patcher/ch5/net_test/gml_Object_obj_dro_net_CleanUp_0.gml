// Clean Up: the game is closing or restarting. Say goodbye and free the sockets.
if (dn_sock >= 0)
{
    if (dn_state == 4)
        dro_net_write_summary("game closed");
    if (dn_state >= 3)
    {
        dro_net_send_begin(5);
        dro_net_send_end();
    }
    network_destroy(dn_sock);
    dn_sock = -1;
}
if (dn_server >= 0)
{
    network_destroy(dn_server);
    dn_server = -1;
}
if (buffer_exists(dn_buf))
    buffer_delete(dn_buf);
