/*
 * drnet.dll: lockstep input exchange for DeltaruneOnline (GameMaker extension, Windows / Proton).
 *
 * Star topology over UDP: clients talk only to the host, the host relays every player's input to
 * everyone, so only the host needs an open port. Every INPUT packet carries the sender's last
 * WINDOW frames, so lost packets are covered by the next one. A background thread receives packets
 * the moment they arrive (exact ping measurement, immediate relaying) and sends delayed test packets.
 *
 * Input delay is per player and automatic by default: each machine measures its ping and sends its
 * input just as many frames ahead as the network needs, so the game never has to wait. The other
 * machines do not need to know a player's delay: every frame of every player is filled exactly once
 * (a sample is repeated when the delay grows and merged into the next one when it shrinks), and the
 * frames themselves are what everybody agrees on. Frames filled ahead of time carry LAG_BIT, so the
 * game knows the local player sees their own SOUL ahead of the shared simulation (see scr_dronline.gml).
 *
 * Goodbye: a game that closes (window closed, Esc held, chapter change) sends BYE with its last inputs, through
 * drnet_bye (Game End and chapter change, called by scr_dronline.gml) or drnet_stop. The others then stop
 * waiting for it at once instead of after the 10 s timeout, but only once they run out of its input, so a
 * partner that is a frame behind still reaches the same last frame (both sides of a chapter change leave in the
 * same frame). It must be sent while the game still runs: sent from DllMain at process exit it never arrived.
 *
 * GameMaker calling convention: cdecl, numbers are double, strings are char*.
 *
 *   drnet_option(name, value)                                     -> 1 (before drnet_start: "lag", "jitter",
 *                                                                    "maxdelay", "slack"; see below)
 *   drnet_set_host(host_ip)                                       -> 1 (client: address of the host)
 *   drnet_start(is_host, port, players, my_player, delay)         -> 1 ok, 0 error (delay -1 = automatic)
 *   (GameMaker refuses DLL functions with more than 4 arguments unless all are numbers.)
 *   drnet_connect(timeout_ms)                                     -> 1 all players connected, 0 timeout
 *                                                                    (the host welcomes clients once all are in)
 *   drnet_set_aux(value)                                          -> 1 (event sent with the next input, e.g. a hit)
 *   drnet_exchange(frame, my_bits)                                -> bits of all players for `frame`
 *                                                                    (p1 | p2 << 16 | p3 << 32), -1 on timeout,
 *                                                                    -2 when a player whose input is missing left
 *   drnet_aux(player)                                             -> that player's event for the last exchanged frame
 *   drnet_pending_count()                                         -> own inputs already sent for later frames
 *   drnet_pending(i)                                              -> bits of the i-th of them (oldest first)
 *   drnet_delay()                                                 -> current own input delay in frames
 *   drnet_ping()                                                  -> round trip in ms (worst peer), -1 unknown
 *   drnet_wait()                                                  -> average ms per frame spent waiting (last 30 frames)
 *   drnet_hash(frame, hash)                                       -> 1 (sends a state fingerprint)
 *   drnet_desync()                                                -> first frame whose fingerprints differ, -1 none
 *   drnet_status()                                                -> short text for the screen
 *   drnet_bye(reason)                                             -> 1 sent, 0 not connected (goodbye, socket stays open)
 *   drnet_stop()                                                  -> 1 (says goodbye to the others first)
 *   drnet_log(text)                                               -> 1 (writes to drnet.log)
 *
 * Options: lag = extra one-way latency in ms added to every sent packet (testing), jitter = random extra
 * 0..jitter ms (testing), maxdelay = upper limit of the automatic delay (default 6), slack = ms of one-way
 * latency that fit into a frame without waiting (default 16).
 */
#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <ws2tcpip.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdarg.h>
#include <stdlib.h>
#include <math.h>

#define EXPORT __declspec(dllexport)
#define MAXP 3
#define RING 512
#define WINDOW 16
#define HRING 64
#define MAGIC 0x44524F4E /* "DRON" */
#define PROTO 3   /* 3: BYE */
#define LAG_BIT 0x0800
#define FRAME_MS (1000.0 / 30.0)
#define QSIZE 1024

enum { T_HELLO = 1, T_WELCOME = 2, T_INPUT = 3, T_HASH = 4, T_BYE = 5 };   /* BYE: an input packet that also says goodbye */

#pragma pack(push, 1)
typedef struct { uint32_t magic; uint8_t type; uint8_t player; uint8_t players; uint8_t proto; } hdr_t;
typedef struct {
    hdr_t h;
    uint32_t ts;        /* sender's clock in microseconds */
    uint32_t echo;      /* last ts the sender got from the receiver, 0 = none yet */
    uint32_t echo_age;  /* microseconds between receiving `echo` and sending this packet */
    uint16_t relay_ms;  /* host -> client: one-way latency of the slowest other client (3 players) */
    uint32_t first;
    uint8_t count;
    uint16_t bits[WINDOW];
    uint32_t aux[WINDOW];
} input_pkt;
typedef struct { hdr_t h; uint32_t frame; uint32_t hash; } hash_pkt;
#pragma pack(pop)

typedef struct { uint64_t due; struct sockaddr_in to; int len; char data[sizeof(input_pkt)]; } qitem;

static SOCKET sock = INVALID_SOCKET;
static CRITICAL_SECTION cs;
static int cs_ready;
static HANDLE ev_input, thr;
static volatile LONG running;
static int is_host, players, me, connected;
static struct sockaddr_in peer[MAXP + 1];   /* host: client addresses; client: peer[1] = host */
static int have_peer[MAXP + 1];
static int32_t frame_of[MAXP + 1][RING];    /* which frame the slot currently holds */
static uint16_t bits_of[MAXP + 1][RING];
static uint32_t aux_of[MAXP + 1][RING];
static int32_t next_target;                 /* next frame my input goes to */
static uint16_t held_bits;                  /* sampled but not yet assigned (after the delay shrank) */
static uint32_t held_aux, next_aux;
static int32_t last_frame = -1;             /* last frame returned by drnet_exchange */
static int delay, delay_auto, max_delay = 6;
static double slack_ms = 16, lag_ms, jitter_ms;
static double srtt[MAXP + 1], rttvar[MAXP + 1];   /* ms, 0 = not measured yet */
static uint32_t rx_ts[MAXP + 1];
static uint64_t rx_at[MAXP + 1];
static uint16_t relay_ms;                   /* client: from the host */
static uint64_t below_since;
static double wait_hist[30];
static int wait_pos;
static int32_t hash_frame[MAXP + 1][HRING];
static uint32_t hash_val[MAXP + 1][HRING];
static int32_t desync_at = -1;
static int gone[MAXP + 1];                  /* player said goodbye */
static int32_t bye_last[MAXP + 1];          /* last frame in that goodbye */
static char status[256];
static FILE *log_file;
static char host_addr[64] = "127.0.0.1";
static qitem queue[QSIZE];
static int qcount;
static uint64_t qpf;

static uint64_t now_us(void)
{
    LARGE_INTEGER c;
    if (!qpf) { LARGE_INTEGER f; QueryPerformanceFrequency(&f); qpf = (uint64_t)f.QuadPart; }
    QueryPerformanceCounter(&c);
    return (uint64_t)c.QuadPart / qpf * 1000000 + (uint64_t)c.QuadPart % qpf * 1000000 / qpf;
}

/* DRONLINE_ROLE: letters and digits, at most 16 (the same rule as dronline_init in scr_dronline.gml). */
static int valid_role(const char *r)
{
    int i;
    if (!r || !*r) return 0;
    for (i = 0; r[i]; i++)
        if (i >= 16 || !((r[i] >= 'a' && r[i] <= 'z') || (r[i] >= 'A' && r[i] <= 'Z') || (r[i] >= '0' && r[i] <= '9'))) return 0;
    return 1;
}

/* Log to %LOCALAPPDATA%\\DELTARUNE\\drnet.log (the game's save folder), one file per Wine prefix / PC.
 * Two copies on one PC share that folder: a copy started with DRONLINE_ROLE=<name> that reads its own
 * dronline_<name>.ini (scr_dronline.gml) logs to drnet_<name>.log instead. */
static void logm(const char *fmt, ...)
{
    va_list ap;
    if (!log_file) {
        char path[600]; const char *la = getenv("LOCALAPPDATA"), *role = getenv("DRONLINE_ROLE");
        if (!la) la = ".";
        snprintf(path, sizeof(path), "%s\\DELTARUNE\\dronline_%s.ini", la, valid_role(role) ? role : "");
        if (valid_role(role) && GetFileAttributesA(path) != INVALID_FILE_ATTRIBUTES)
            snprintf(path, sizeof(path), "%s\\DELTARUNE\\drnet_%s.log", la, role);
        else
            snprintf(path, sizeof(path), "%s\\DELTARUNE\\drnet.log", la);
        log_file = fopen(path, "a");
        if (!log_file) return;
    }
    fprintf(log_file, "[%lu] ", (unsigned long)GetTickCount());
    va_start(ap, fmt); vfprintf(log_file, fmt, ap); va_end(ap);
    fputc('\n', log_file); fflush(log_file);
}

static int fail(const char *what)
{
    snprintf(status, sizeof(status), "%s failed (WSA %d)", what, WSAGetLastError());
    logm("%s", status);
    return 0;
}

static void lock(void) { EnterCriticalSection(&cs); }
static void unlock(void) { LeaveCriticalSection(&cs); }

static void put_input(int p, int32_t f, uint16_t b, uint32_t a)
{
    if (p < 1 || p > MAXP || f < 0) return;
    frame_of[p][f % RING] = f;
    bits_of[p][f % RING] = b;
    aux_of[p][f % RING] = a;
}

static int has_input(int p, int32_t f) { return f >= 0 && frame_of[p][f % RING] == f; }

static int sends_logged;
static void raw_send(const struct sockaddr_in *a, const void *buf, int len)
{
    int r = sendto(sock, (const char *)buf, len, 0, (const struct sockaddr *)a, sizeof(*a));
    if (sends_logged < 5) { sends_logged++; logm("sendto %s:%d len %d -> %d (WSA %d)", inet_ntoa(a->sin_addr), ntohs(a->sin_port), len, r, r < 0 ? WSAGetLastError() : 0); }
}

/* Sends now, or after the configured test latency (the network thread sends queued packets). */
static void send_to(const struct sockaddr_in *a, const void *buf, int len)
{
    qitem *q;
    if (lag_ms <= 0 && jitter_ms <= 0) { raw_send(a, buf, len); return; }
    if (qcount >= QSIZE || len > (int)sizeof(q->data)) return;           /* full: drop, like a real network */
    q = &queue[qcount++];
    q->due = now_us() + (uint64_t)((lag_ms + (jitter_ms > 0 ? jitter_ms * (rand() % 1000) / 1000.0 : 0)) * 1000);
    q->to = *a; q->len = len; memcpy(q->data, buf, len);
}

static void flush_queue(uint64_t now)
{
    int i = 0;
    while (i < qcount) {
        if (queue[i].due <= now) { raw_send(&queue[i].to, queue[i].data, queue[i].len); queue[i] = queue[--qcount]; }
        else i++;
    }
}

static void fill_hdr(hdr_t *h, int type, int player)
{
    h->magic = MAGIC; h->type = (uint8_t)type; h->player = (uint8_t)player;
    h->players = (uint8_t)players; h->proto = PROTO;
}

/* One-way latency estimate to peer q in ms (mean + 2 deviations, halved), -1 unknown. */
static double one_way(int q) { return srtt[q] > 0 ? (srtt[q] + 2 * rttvar[q]) / 2 : -1; }

/* Worst one-way latency my input needs to reach every other player, -1 unknown. */
static double my_path_ms(void)
{
    double worst = -1; int q;
    if (is_host) {
        for (q = 2; q <= players; q++) if (one_way(q) > worst) worst = one_way(q);
        return worst;
    }
    if (one_way(1) < 0) return -1;
    return one_way(1) + (players > 2 ? relay_ms : 0);   /* to the host, then relayed to the other client */
}

static void stamp(input_pkt *k, int dest)
{
    uint64_t now = now_us();
    k->ts = (uint32_t)now; if (!k->ts) k->ts = 1;
    k->echo = rx_ts[dest];
    k->echo_age = rx_ts[dest] ? (uint32_t)(now - rx_at[dest]) : 0;
    k->relay_ms = 0;
    if (is_host && players > 2) {
        double worst = 0; int q;
        for (q = 2; q <= players; q++) if (q != dest && one_way(q) > worst) worst = one_way(q);
        k->relay_ms = (uint16_t)(worst + 0.5);
    }
}

/* Send player p's last WINDOW known frames ending at `last` to everyone who should get them. */
static void send_window(int p, int32_t last)
{
    input_pkt k; int32_t first = last - WINDOW + 1; int i;
    if (last < 0) return;
    if (first < 0) first = 0;
    memset(&k, 0, sizeof(k));
    fill_hdr(&k.h, gone[p] ? T_BYE : T_INPUT, p);   /* the host keeps relaying a player who left as BYE */
    k.first = (uint32_t)first; k.count = (uint8_t)(last - first + 1);
    for (i = 0; i < k.count; i++) { k.bits[i] = bits_of[p][(first + i) % RING]; k.aux[i] = aux_of[p][(first + i) % RING]; }
    if (is_host) {
        for (i = 2; i <= players; i++)
            if (have_peer[i] && i != p) { stamp(&k, i); send_to(&peer[i], &k, sizeof(k)); }
    } else if (have_peer[1]) {
        stamp(&k, 1); send_to(&peer[1], &k, sizeof(k));
    }
}

static int32_t last_known(int p)
{
    int32_t f = next_target + 64;             /* search a little ahead of us */
    for (; f >= 0 && f > next_target - RING + 64; f--) if (frame_of[p][f % RING] == f) return f;
    return -1;
}

static void measure(int q, const input_pkt *k, uint64_t now)
{
    rx_ts[q] = k->ts; rx_at[q] = now;
    if (k->echo) {
        int32_t us = (int32_t)((uint32_t)now - k->echo - k->echo_age);
        if (us >= 0 && us < 5000000) {
            double ms = us / 1000.0;
            if (srtt[q] <= 0) { srtt[q] = ms; rttvar[q] = ms / 4; }
            else { rttvar[q] = 0.75 * rttvar[q] + 0.25 * fabs(ms - srtt[q]); srtt[q] = 0.875 * srtt[q] + 0.125 * ms; }
        }
    }
}

/* Status text once player p's goodbye made drnet_exchange give up (lock held). */
static void left_status(int p)
{
    if (players == 2) snprintf(status, sizeof(status), "the other player left the game");
    else snprintf(status, sizeof(status), "player %d left the game", p);
}

/* Runs on the network thread, with the lock held. */
static void handle(const char *buf, int len, const struct sockaddr_in *from, uint64_t now)
{
    const hdr_t *h = (const hdr_t *)buf;
    if (len < (int)sizeof(hdr_t) || h->magic != MAGIC) return;
    /* a client repeats HELLO until it is welcomed: log the first one from each address */
    if (h->type == T_WELCOME || (h->type == T_HELLO && h->player >= 1 && h->player <= MAXP &&
        (!have_peer[h->player] || peer[h->player].sin_addr.s_addr != from->sin_addr.s_addr || peer[h->player].sin_port != from->sin_port)))
        logm("got %s from %s:%d player %d", h->type == T_HELLO ? "HELLO" : "WELCOME", inet_ntoa(from->sin_addr), ntohs(from->sin_port), h->player);
    if (h->proto != PROTO) {
        static int warned;
        if (!warned++) { snprintf(status, sizeof(status), "other player runs a different DeltaruneOnline version"); logm("%s (protocol %d, mine %d)", status, h->proto, PROTO); }
        return;
    }
    if (h->type == T_HELLO && is_host && h->player >= 2 && h->player <= players) {
        hdr_t w; int q, all = 1;
        peer[h->player] = *from; have_peer[h->player] = 1;
        /* WELCOME only once every player is in: a welcomed client starts playing, and with 3 players its first
           exchange would wait for a host that still waits for the third, and give up after 10 s. Clients repeat
           HELLO every 200 ms until they are welcomed, so each one gets its WELCOME then. */
        for (q = 2; q <= players; q++) if (!have_peer[q]) all = 0;
        if (all) { fill_hdr(&w, T_WELCOME, 1); send_to(from, &w, sizeof(w)); }
    } else if (h->type == T_WELCOME && !is_host) {
        connected = 1;
    } else if ((h->type == T_INPUT || h->type == T_BYE) && len >= (int)sizeof(input_pkt)) {
        const input_pkt *k = (const input_pkt *)buf; int i;
        if (k->h.player < 1 || k->h.player > players || k->h.player == me) return;
        if (is_host) measure(k->h.player, k, now);          /* every packet at the host comes straight from its client */
        else if (k->h.player == 1) { measure(1, k, now); relay_ms = k->relay_ms; }   /* others are relayed */
        for (i = 0; i < k->count && i < WINDOW; i++) put_input(k->h.player, (int32_t)k->first + i, k->bits[i], k->aux[i]);
        if (is_host) {                        /* relay to the other clients right away */
            int q; input_pkt r = *k;
            for (q = 2; q <= players; q++)
                if (have_peer[q] && q != k->h.player) { stamp(&r, q); send_to(&peer[q], &r, sizeof(r)); }
        }
        /* Back after a goodbye only with input newer than the goodbye. An older input packet can still arrive after
           the goodbye (sent just before it, reordered, or held in a queue), and must not undo it (test 4b, 2026-11-23). */
        if (h->type == T_INPUT && gone[k->h.player] && k->count && (int32_t)(k->first + k->count - 1) > bye_last[k->h.player]) {
            gone[k->h.player] = 0;
            logm("player %d sends input again", k->h.player);
        }
        if (h->type == T_BYE && !gone[k->h.player]) {
            gone[k->h.player] = 1;
            bye_last[k->h.player] = k->count ? (int32_t)(k->first + k->count - 1) : -1;
            logm("player %d said goodbye (last frame %d)", k->h.player, bye_last[k->h.player]);
        }
        SetEvent(ev_input);
    } else if (h->type == T_HASH && len >= (int)sizeof(hash_pkt)) {
        const hash_pkt *x = (const hash_pkt *)buf; int s = x->frame % HRING;
        if (x->h.player < 1 || x->h.player > players) return;
        hash_frame[x->h.player][s] = (int32_t)x->frame; hash_val[x->h.player][s] = x->hash;
        if (hash_frame[me][s] == (int32_t)x->frame && hash_val[me][s] != x->hash && desync_at < 0)
            { desync_at = (int32_t)x->frame; logm("DESYNC at frame %d", desync_at); }
        if (is_host) {
            int q;
            for (q = 2; q <= players; q++)
                if (have_peer[q] && q != x->h.player) send_to(&peer[q], x, sizeof(*x));
        }
    }
}

static DWORD WINAPI net_thread(LPVOID unused)
{
    char buf[1024]; struct sockaddr_in from; int fl, n;
    (void)unused;
    while (running) {
        fd_set rs; struct timeval tv;
        FD_ZERO(&rs); FD_SET(sock, &rs);
        tv.tv_sec = 0; tv.tv_usec = 1000;
        n = select(0, &rs, NULL, NULL, &tv);
        lock();
        if (n > 0) {
            for (;;) {
                fl = sizeof(from);
                n = recvfrom(sock, buf, sizeof(buf), 0, (struct sockaddr *)&from, &fl);
                if (n <= 0) break;
                handle(buf, n, &from, now_us());
            }
        } else if (n < 0) {
            static int sel_err;
            if (sel_err++ < 3) logm("select error WSA %d", WSAGetLastError());
            unlock(); Sleep(5); lock();
        }
        flush_queue(now_us());
        unlock();
    }
    return 0;
}

/* Tell the system the game is alive while we block (avoids the "not responding" state).
 * Only peek: dispatching would run GameMaker's window procedure in the middle of a frame,
 * which deadlocked both games in testing. */
static void pump_window_messages(void)
{
    MSG m;
    PeekMessageA(&m, NULL, 0, 0, PM_NOREMOVE);
}

static void update_status(void)
{
    double ms = srtt[is_host ? 2 : 1];
    int q;
    if (is_host) for (q = 3; q <= players; q++) if (srtt[q] > ms) ms = srtt[q];
    snprintf(status, sizeof(status), "%s, player %d of %d, ping %s%d ms, delay %d%s", is_host ? "host" : "client", me, players,
             ms > 0 ? "" : "? ", (int)(ms + 0.5), delay, delay_auto ? " (auto)" : "");
}

/* Automatic delay: as many frames as my input needs to reach everyone, minus what fits into a frame.
 * Grows at once (a waiting game would stutter), shrinks only after 3 s of clearly better ping. */
static void update_delay(uint64_t now)
{
    double path = my_path_ms();
    int need, need_tight;
    if (!delay_auto || path < 0) return;
    need = (int)ceil((path - slack_ms) / FRAME_MS);
    need_tight = (int)ceil((path + 5 - slack_ms) / FRAME_MS);
    if (need < 0) need = 0;
    if (need_tight < 0) need_tight = 0;
    if (need > max_delay) need = max_delay;
    if (need > delay) {
        logm("delay %d -> %d (one-way %.1f ms)", delay, need, path);
        delay = need; below_since = 0;
    } else if (need_tight < delay) {
        if (!below_since) below_since = now;
        else if (now - below_since > 3000000) {
            logm("delay %d -> %d (one-way %.1f ms)", delay, delay - 1, path);
            delay--; below_since = 0;
        }
    } else {
        below_since = 0;
    }
}

/* Goodbye: my last WINDOW frames as a BYE packet, so a partner still gets the input it needs to reach my last
 * frame. Sent three times, because UDP may lose one, and straight out (not through the test latency queue).
 * Lock held. */
static void send_bye(void)
{
    input_pkt k; int32_t last = next_target - 1, first = last - WINDOW + 1; int i, r, n = 0;
    if (sock == INVALID_SOCKET || !connected) return;
    if (first < 0) first = 0;
    memset(&k, 0, sizeof(k));
    fill_hdr(&k.h, T_BYE, me);
    if (last >= 0) {
        k.first = (uint32_t)first; k.count = (uint8_t)(last - first + 1);
        for (i = 0; i < k.count; i++) { k.bits[i] = bits_of[me][(first + i) % RING]; k.aux[i] = aux_of[me][(first + i) % RING]; }
    }
    for (r = 0; r < 3; r++) {
        if (is_host) {
            for (i = 2; i <= players; i++)
                if (have_peer[i] && !gone[i]) { stamp(&k, i); raw_send(&peer[i], &k, sizeof(k)); n++; }
        } else if (have_peer[1] && !gone[1]) {
            stamp(&k, 1); raw_send(&peer[1], &k, sizeof(k)); n++;
        }
    }
    if (n) logm("sent goodbye (last frame %d)", last);
}

EXPORT double drnet_option(const char *name, double value)
{
    if (!name) return 0;
    if (!strcmp(name, "lag")) lag_ms = value;
    else if (!strcmp(name, "jitter")) jitter_ms = value;
    else if (!strcmp(name, "maxdelay")) max_delay = value < 0 ? 0 : (value > 30 ? 30 : (int)value);
    else if (!strcmp(name, "slack")) slack_ms = value;
    else return 0;
    logm("option %s = %g", name, value);
    return 1;
}

EXPORT double drnet_set_host(const char *host_ip)
{
    snprintf(host_addr, sizeof(host_addr), "%s", host_ip ? host_ip : "");
    return 1;
}

EXPORT double drnet_start(double host_flag, double port, double nplayers, double my_player, double dly)
{
    const char *host_ip = host_addr;
    WSADATA w; struct sockaddr_in a; u_long nb = 1; int p, i;
    logm("start %s host=%s port=%g players=%g me=%g delay=%g (protocol %d)", host_flag ? "host" : "client", host_ip, port, nplayers, my_player, dly, PROTO);
    if (sock != INVALID_SOCKET) return 1;
    if (!cs_ready) { InitializeCriticalSection(&cs); ev_input = CreateEventA(NULL, FALSE, FALSE, NULL); cs_ready = 1; }
    if (WSAStartup(MAKEWORD(2, 2), &w) != 0) return fail("WSAStartup");
    is_host = host_flag != 0;
    players = (int)nplayers; me = (int)my_player;
    delay_auto = dly < 0; delay = delay_auto ? 0 : (int)dly;
    if (players < 2 || players > MAXP || me < 1 || me > players || delay > 30) { snprintf(status, sizeof(status), "bad settings"); logm("bad settings"); WSACleanup(); return 0; }
    if (is_host && me != 1) { snprintf(status, sizeof(status), "host must be player 1"); logm("%s", status); WSACleanup(); return 0; }
    /* DELTARUNE's chapter change (game_change) starts a new game process that inherits open handles, and the old
       process exits without Clean Up. An inherited socket keeps the port bound inside the new process, so hosting
       again fails until that process ends (Phase 1 test, 2026-11-23). So the socket is created non-inheritable;
       SetHandleInformation alone can fail with layered Winsock providers (antivirus, VPN), and that is logged.
       WSA_FLAG_OVERLAPPED keeps it the same kind of socket that socket() makes. */
    sock = WSASocketW(AF_INET, SOCK_DGRAM, IPPROTO_UDP, NULL, 0, WSA_FLAG_OVERLAPPED | WSA_FLAG_NO_HANDLE_INHERIT);
    if (sock == INVALID_SOCKET) {               /* Windows older than 7 SP1 does not know the flag */
        logm("WSASocketW failed (WSA %d), using socket()", WSAGetLastError());
        sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
        if (sock == INVALID_SOCKET) { int r = fail("socket"); WSACleanup(); return r; }
        if (!SetHandleInformation((HANDLE)sock, HANDLE_FLAG_INHERIT, 0))
            logm("SetHandleInformation failed (%lu): a chapter change may keep port %g busy", GetLastError(), port);
    }
    ioctlsocket(sock, FIONBIO, &nb);
    memset(&a, 0, sizeof(a)); a.sin_family = AF_INET;
    a.sin_port = htons(is_host ? (u_short)port : 0); a.sin_addr.s_addr = INADDR_ANY;
    if (bind(sock, (struct sockaddr *)&a, sizeof(a)) != 0) { int r = fail("bind"); closesocket(sock); sock = INVALID_SOCKET; WSACleanup(); return r; }
    for (p = 0; p <= MAXP; p++) {
        have_peer[p] = 0; gone[p] = 0; bye_last[p] = -1; srtt[p] = rttvar[p] = 0; rx_ts[p] = 0;
        for (i = 0; i < RING; i++) frame_of[p][i] = -1;
        for (i = 0; i < HRING; i++) hash_frame[p][i] = -1;
    }
    if (!is_host) {
        memset(&peer[1], 0, sizeof(peer[1])); peer[1].sin_family = AF_INET;
        peer[1].sin_port = htons((u_short)port);
        if (inet_pton(AF_INET, host_ip, &peer[1].sin_addr) != 1) { snprintf(status, sizeof(status), "bad host address %s", host_ip); logm("%s", status); closesocket(sock); sock = INVALID_SOCKET; WSACleanup(); return 0; }
        have_peer[1] = 1;
    }
    connected = 0; next_target = 0; held_bits = 0; held_aux = next_aux = 0; last_frame = -1; desync_at = -1; qcount = 0;
    srand((unsigned)now_us());
    running = 1;
    thr = CreateThread(NULL, 0, net_thread, NULL, 0, NULL);
    if (!thr) { int r = fail("CreateThread"); running = 0; closesocket(sock); sock = INVALID_SOCKET; WSACleanup(); return r; }
    update_status();
    logm("socket ready: %s", status);
    return 1;
}

EXPORT double drnet_connect(double timeout_ms)
{
    DWORD start = GetTickCount(), last_hello = 0, last_note = GetTickCount();
    logm("connect: begin (timeout %g ms, socket %s)", timeout_ms, sock == INVALID_SOCKET ? "INVALID" : "ok");
    if (sock == INVALID_SOCKET) return 0;
    while (GetTickCount() - start < (DWORD)timeout_ms) {
        int done = 0;
        lock();
        if (is_host) {
            int p, all = 1;
            for (p = 2; p <= players; p++) if (!have_peer[p]) all = 0;
            if (all) { connected = 1; done = 1; logm("all players connected"); }
        } else {
            if (connected) { done = 1; logm("connected to host"); }
            else if (GetTickCount() - last_hello > 200) {
                hdr_t h; fill_hdr(&h, T_HELLO, me);
                send_to(&peer[1], &h, sizeof(h)); last_hello = GetTickCount();
            }
        }
        unlock();
        if (done) return 1;
        Sleep(10);
        pump_window_messages();
        if (GetTickCount() - last_note > 5000) { last_note = GetTickCount(); logm("connect: still waiting"); }
    }
    snprintf(status, sizeof(status), "%s: nobody answered in %g s", is_host ? "host" : "client", timeout_ms / 1000);
    logm("%s", status);
    return 0;
}

EXPORT double drnet_set_aux(double value)
{
    next_aux = value > 0 ? (uint32_t)value : 0;
    return 1;
}

/* Puts the local sample of frame f into every frame it now covers (none, one, or several). */
static void assign(int32_t f, uint16_t bits)
{
    int first = 1;
    held_bits |= bits & ~LAG_BIT;
    if (next_aux && !held_aux) held_aux = next_aux;
    next_aux = 0;
    while (next_target <= f + delay) {
        put_input(me, next_target, (uint16_t)(held_bits | (next_target > f ? LAG_BIT : 0)), first ? held_aux : 0);
        next_target++;
        first = 0;
    }
    if (!first) { held_bits = 0; held_aux = 0; }
}

EXPORT double drnet_exchange(double frame, double my_bits)
{
    int32_t f = (int32_t)frame;
    uint64_t start = now_us(), last_send;
    int p, ready, left;
    if (sock == INVALID_SOCKET || !connected) return -1;
    lock();
    update_delay(start);
    assign(f, (uint16_t)my_bits);
    send_window(me, next_target - 1);
    unlock();
    last_send = now_us();
    for (;;) {
        uint64_t now;
        lock();
        ready = 1; left = 0;
        for (p = 1; p <= players; p++) if (!has_input(p, f)) { ready = 0; if (gone[p]) left = p; }
        /* a client gets the other clients' input only through the host: once the host left, nothing missing arrives */
        if (!ready && !left && !is_host && gone[1]) left = 1;
        if (left) left_status(left);
        now = now_us();
        if (!ready && now - last_send > 16000) {             /* resend while waiting */
            send_window(me, next_target - 1);
            if (is_host) for (p = 2; p <= players; p++) send_window(p, last_known(p));
            last_send = now;
        }
        unlock();
        if (ready) break;
        if (left) { logm("exchange: player %d left before frame %d", left, f); return -2; }
        if (now - start > 10000000) { logm("exchange timeout at frame %d", f); return -1; }      /* peer gone */
        WaitForSingleObject(ev_input, 2);
        if (now - start > 100000) pump_window_messages();
    }
    lock();
    if (is_host) for (p = 2; p <= players; p++) send_window(p, last_known(p));
    last_frame = f;
    wait_hist[wait_pos] = (now_us() - start) / 1000.0; wait_pos = (wait_pos + 1) % 30;
    if (f % 15 == 0) update_status();
    {
        double out = 0, mul = 1;
        for (p = 1; p <= MAXP; p++) { out += (p <= players ? bits_of[p][f % RING] : 0) * mul; mul *= 65536.0; }
        unlock();
        return out;
    }
}

EXPORT double drnet_aux(double player)
{
    int p = (int)player;
    double r = 0;
    if (p < 1 || p > MAXP || last_frame < 0) return 0;
    lock();
    if (frame_of[p][last_frame % RING] == last_frame) r = aux_of[p][last_frame % RING];
    unlock();
    return r;
}

EXPORT double drnet_pending_count(void)
{
    return last_frame < 0 ? 0 : next_target - 1 - last_frame;
}

EXPORT double drnet_pending(double i)
{
    int32_t f = last_frame + 1 + (int32_t)i;
    if (last_frame < 0 || i < 0 || f >= next_target) return 0;
    return bits_of[me][f % RING];
}

EXPORT double drnet_delay(void) { return delay; }

EXPORT double drnet_ping(void)
{
    double ms = -1; int q;
    if (!cs_ready) return ms;
    lock();
    if (is_host) { for (q = 2; q <= players; q++) if (srtt[q] > 0 && srtt[q] > ms) ms = srtt[q]; }   /* 0 = not measured */
    else if (srtt[1] > 0) ms = srtt[1];
    unlock();
    return ms;
}

EXPORT double drnet_wait(void)
{
    double s = 0; int i;
    for (i = 0; i < 30; i++) s += wait_hist[i];
    return s / 30;
}

EXPORT double drnet_hash(double frame, double hash)
{
    hash_pkt x; int s = ((int32_t)frame) % HRING, p;
    if (sock == INVALID_SOCKET) return 0;
    lock();
    hash_frame[me][s] = (int32_t)frame; hash_val[me][s] = (uint32_t)(int64_t)hash;
    for (p = 1; p <= players; p++)
        if (p != me && hash_frame[p][s] == (int32_t)frame && hash_val[p][s] != hash_val[me][s] && desync_at < 0)
            { desync_at = (int32_t)frame; logm("DESYNC at frame %d", desync_at); }
    fill_hdr(&x.h, T_HASH, me); x.frame = (uint32_t)frame; x.hash = hash_val[me][s];
    if (is_host) { for (p = 2; p <= players; p++) if (have_peer[p]) send_to(&peer[p], &x, sizeof(x)); }
    else send_to(&peer[1], &x, sizeof(x));
    unlock();
    return 1;
}

EXPORT double drnet_desync(void) { return desync_at; }

EXPORT double drnet_log(const char *msg) { logm("gml: %s", msg ? msg : ""); return 1; }

EXPORT const char *drnet_status(void) { return status; }

EXPORT double drnet_bye(const char *reason)
{
    if (sock == INVALID_SOCKET || !connected) return 0;
    logm("goodbye: %s", reason ? reason : "");
    lock(); send_bye(); unlock();
    Sleep(20);   /* the game usually exits right after this: leave the network stack a moment to send */
    return 1;
}

EXPORT double drnet_stop(void)
{
    if (sock != INVALID_SOCKET) {
        lock(); send_bye(); unlock();
        running = 0;
        if (thr) { WaitForSingleObject(thr, 1000); CloseHandle(thr); thr = NULL; }
        closesocket(sock); sock = INVALID_SOCKET; WSACleanup();
    }
    connected = 0;
    return 1;
}
