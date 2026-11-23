/*
 * Standalone test for drnet.dll (no game needed).
 *   test_drnet host 2 1 <frames> [lag_ms] [delay]           (host, 2 players, player 1)
 *   test_drnet client 2 2 <frames> [lag_ms] [delay] [ip]    (client, player 2)
 *   ... [ip] [quit_at]: at that frame say goodbye with drnet_bye and exit without drnet_stop, as the game does
 *   when its window is closed; the other side must then report "left" at once instead of the 10 s timeout.
 * delay -1 (default) = automatic. lag_ms adds test latency to every packet this side sends.
 *
 * Each side feeds pseudo-random bits and events, folds every frame's result (all players' bits and
 * events) into a running checksum and prints it: lockstep works if all sides print the same checksums.
 * It also prints the delay, ping, average wait and frame time, so a slowdown shows up as frame time > 33 ms.
 */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef double (*f_start)(double, double, double, double, double);
typedef double (*f_str)(const char *);
typedef double (*f_sd)(const char *, double);
typedef double (*f_d)(double);
typedef double (*f_dd)(double, double);
typedef double (*f_v)(void);
typedef const char *(*f_s)(void);

static unsigned bits_for(int player, int frame) { return (unsigned)((frame * 2654435761u + player * 40503u) >> 7) & 0x7FF; }

int main(int argc, char **argv)
{
    if (argc < 5) { printf("usage: %s host|client players me frames [lag_ms] [delay] [ip] [quit_at]\n", argv[0]); return 2; }
    const char *role = argv[1]; int players = atoi(argv[2]), me = atoi(argv[3]), frames = atoi(argv[4]);
    double lag = argc > 5 ? atof(argv[5]) : 0, dly = argc > 6 ? atof(argv[6]) : -1;
    const char *ip = argc > 7 ? argv[7] : "127.0.0.1";
    int quit_at = argc > 8 ? atoi(argv[8]) : -1;
    int f, p;
    unsigned long long sum = 1469598103934665603ull;
    /* Windows rounds Sleep() up to its 15.6 ms timer, so a 33 ms frame took 46.8 ms (3 ticks) on native Windows
       (online test 6, 2026-11-23). Ask for a 1 ms timer; winmm is loaded at run time so
       the build command needs no extra library. */
    HMODULE mm = LoadLibraryA("winmm.dll");
    if (mm) { typedef UINT (WINAPI *f_tbp)(UINT); f_tbp tbp = (f_tbp)GetProcAddress(mm, "timeBeginPeriod"); if (tbp) tbp(1); }
    HMODULE h = LoadLibraryA("drnet.dll");
    if (!h) { printf("cannot load drnet.dll\n"); return 1; }
    f_start start = (f_start)GetProcAddress(h, "drnet_start");
    f_str set_host = (f_str)GetProcAddress(h, "drnet_set_host");
    f_sd option = (f_sd)GetProcAddress(h, "drnet_option");
    f_d connect = (f_d)GetProcAddress(h, "drnet_connect");
    f_dd exchange = (f_dd)GetProcAddress(h, "drnet_exchange");
    f_d set_aux = (f_d)GetProcAddress(h, "drnet_set_aux");
    f_d aux = (f_d)GetProcAddress(h, "drnet_aux");
    f_dd hash = (f_dd)GetProcAddress(h, "drnet_hash");
    f_v desync = (f_v)GetProcAddress(h, "drnet_desync");
    f_v delay = (f_v)GetProcAddress(h, "drnet_delay");
    f_v ping = (f_v)GetProcAddress(h, "drnet_ping");
    f_v waitms = (f_v)GetProcAddress(h, "drnet_wait");
    f_v pending = (f_v)GetProcAddress(h, "drnet_pending_count");
    f_s status = (f_s)GetProcAddress(h, "drnet_status");
    f_v stop = (f_v)GetProcAddress(h, "drnet_stop");
    f_str bye = (f_str)GetProcAddress(h, "drnet_bye");
    set_host(ip);
    if (lag > 0) option("lag", lag);
    if (!start(strcmp(role, "host") == 0, 7845, players, me, dly)) { printf("start failed: %s\n", status()); return 1; }
    printf("%s: connecting...\n", status());
    if (!connect(30000)) { printf("connect timeout\n"); return 1; }
    DWORD t0 = GetTickCount(), tblock = t0;
    for (f = 0; f < frames; f++) {
        DWORD tf = GetTickCount();
        if (f == quit_at) { printf("%s quits at frame %d (drnet_bye, no drnet_stop)\n", role, f); fflush(stdout); bye("test quit"); ExitProcess(0); }
        set_aux(f % 37 == 0 ? 100000 + f * 3 + me : 0);
        double all = exchange(f, bits_for(me, f));
        /* like the game (dronline_step): stop the network, which says goodbye to whoever is still there */
        if (all == -2) { printf("%s: %s at frame %d, noticed after %lu ms\n", role, status(), f, GetTickCount() - tf); stop(); return 0; }
        if (all < 0) { printf("exchange timeout at frame %d\n", f); stop(); return 1; }
        unsigned long long v = (unsigned long long)all;
        for (p = 1; p <= players; p++) v = v * 1099511628211ull + (unsigned long long)aux(p);
        sum = (sum ^ v) * 1099511628211ull;
        if (f % 30 == 0) hash(f, (double)(f * 7 + 1));
        if (f % 150 == 149) {
            printf("%s frame %4d sum %016llx delay %g pending %g ping %.1f wait %.1f ms frame %.1f ms\n", role, f + 1, sum,
                   delay(), pending(), ping(), waitms(), (GetTickCount() - tblock) / 150.0);
            fflush(stdout);
            tblock = GetTickCount();
        }
        DWORD used = GetTickCount() - tf, pace = me == 1 ? 33 : 32;   /* a game frame: work + sleep to 30 fps */
        Sleep(5);
        used = GetTickCount() - tf;
        if (used < pace) Sleep(pace - used);
    }
    printf("%s done: %d frames in %lu ms, final sum %016llx, desync %g\n", role, frames, GetTickCount() - t0, sum, desync());
    stop();
    return 0;
}
