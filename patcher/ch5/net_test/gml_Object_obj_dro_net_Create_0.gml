// DeltaruneOnline, Phase 1 network test object.
// Persistent; one instance is created at the end of obj_time's Create event.
dn_version = "DRO phase1 test 1";
dn_magic = 1146244913; // "DRO1"
dn_proto = 1;
ini_open("deltarune_online.ini");
dn_port = ini_read_real("online", "port", 7777);
dn_last_ip = ini_read_string("online", "last_ip", "127.0.0.1");
ini_write_real("online", "port", dn_port);
ini_write_string("online", "last_ip", dn_last_ip);
ini_close();
dn_state = 0; // 0 off, 1 hosting, 2 connecting, 3 checking version, 4 linked
dn_role = 0; // 0 none, 1 host, 2 join
dn_server = -1;
dn_sock = -1;
dn_remote_ip = "";
dn_remote_port = 0;
dn_partner_version = "?";
dn_hello_sent = false;
dn_timer = 0;
dn_status = "F5 = host, F6 = join";
dn_status_color = c_white;
dn_show = 1; // 0 hidden, 1 short, 2 full
dn_prompt = false;
dn_prompt_text = "";
dn_phase = 0; // last event of this object that ran: 1 BeginStep, 2 Step, 3 EndStep, 4 DrawGUIEnd
dn_game_frame = 0;
dn_freeze_request = false;
dn_freeze_timer = 0;
dn_freeze_check = 0;
dn_freeze_before = [[], [], []];
dn_freeze_now = [[], [], []];
dn_freeze_result = "";
dn_buf = buffer_create(64, buffer_grow, 1);
network_set_config(network_config_use_non_blocking_socket, 1);
network_set_config(network_config_connect_timeout, 5000);
dro_net_reset_stats();
