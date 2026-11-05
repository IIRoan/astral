// SPDX-License-Identifier: AGPL-3.0-or-later
// Per-device SDR white-level correction through the compositor's gamma API.
#include <errno.h>
#include <getopt.h>
#include <glob.h>
#include <math.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>
#include <wayland-client.h>
#include "wlr-gamma-control-unstable-v1-client-protocol.h"

struct state;
struct output {
    struct state *state;
    struct output *next;
    struct wl_output *proxy;
    struct zwlr_gamma_control_v1 *control;
    uint32_t registry_name;
    char *name;
    time_t retry_after;
    bool ready, warned;
};

struct state {
    struct wl_display *display;
    struct zwlr_gamma_control_manager_v1 *manager;
    uint32_t manager_name;
    struct output *outputs;
    const char *vendor, *model, *serial, *sysfs;
    double gain;
    bool apply;
};

static volatile sig_atomic_t running = 1;
static void stop(int signal_number) { (void)signal_number; running = 0; }

static bool descriptor_matches(const uint8_t edid[128], uint8_t tag, const char *wanted) {
    for (size_t offset = 54; offset < 126; offset += 18) {
        if (edid[offset] || edid[offset + 1] || edid[offset + 2] ||
            edid[offset + 3] != tag || edid[offset + 4]) continue;
        char text[14];
        memcpy(text, edid + offset + 5, 13);
        text[13] = '\0';
        size_t end = strcspn(text, "\n\r");
        while (end && (text[end - 1] == ' ' || text[end - 1] == '\0')) --end;
        text[end] = '\0';
        if (!strcmp(text, wanted)) return true;
    }
    return false;
}

static bool edid_matches(const uint8_t edid[128], const struct state *state) {
    const uint8_t header[] = {0, 255, 255, 255, 255, 255, 255, 0};
    if (memcmp(edid, header, sizeof(header))) return false;
    unsigned checksum = 0;
    for (size_t i = 0; i < 128; ++i) checksum += edid[i];
    if (checksum % 256) return false;
    unsigned id = ((unsigned)edid[8] << 8) | edid[9];
    char vendor[] = {(char)('A' - 1 + ((id >> 10) & 31)),
                     (char)('A' - 1 + ((id >> 5) & 31)),
                     (char)('A' - 1 + (id & 31)), 0};
    return !strcmp(vendor, state->vendor) &&
           descriptor_matches(edid, 0xfc, state->model) &&
           descriptor_matches(edid, 0xff, state->serial);
}

static bool matches_output(const struct output *output) {
    if (!output->name || !*output->name) return false;
    // Connector names are literal path components, never glob expressions.
    for (const char *p = output->name; *p; ++p)
        if (!((*p >= 'A' && *p <= 'Z') || (*p >= 'a' && *p <= 'z') ||
              (*p >= '0' && *p <= '9') || *p == '-' || *p == '_')) return false;
    char *pattern = NULL;
    if (asprintf(&pattern, "%s/card*-%s/edid", output->state->sysfs, output->name) < 0)
        return false;
    glob_t paths = {0};
    bool matched = false;
    size_t candidates = 0;
    if (glob(pattern, 0, NULL, &paths) == 0) {
        for (size_t i = 0; i < paths.gl_pathc; ++i) {
            FILE *file = fopen(paths.gl_pathv[i], "rb");
            if (!file) continue;
            uint8_t edid[128];
            size_t count = fread(edid, 1, sizeof(edid), file);
            fclose(file);
            if (count == sizeof(edid)) {
                ++candidates;
                if (edid_matches(edid, output->state)) matched = true;
            }
        }
    }
    globfree(&paths);
    free(pattern);
    // Never guess between identically named connectors on different GPUs.
    return matched && candidates == 1;
}

static void fill_ramp(uint16_t *ramp, uint32_t size, double gain) {
    for (uint32_t i = 0; i < size; ++i) {
        uint16_t value = (uint16_t)lround((double)i / (size - 1) * 65535.0 * gain);
        ramp[i] = ramp[size + i] = ramp[2 * size + i] = value;
    }
}

static void release_control(struct output *output) {
    if (output->control) zwlr_gamma_control_v1_destroy(output->control);
    output->control = NULL;
}

static void gamma_failed(void *data, struct zwlr_gamma_control_v1 *control) {
    (void)control;
    struct output *output = data;
    if (!output->warned)
        fprintf(stderr, "%s: gamma control unavailable (unsupported or owned by another client); retrying\n",
                output->name);
    output->warned = true;
    release_control(output);
    output->retry_after = time(NULL) + 10;
}

static void gamma_size(void *data, struct zwlr_gamma_control_v1 *control, uint32_t size) {
    struct output *output = data;
    if (size < 2 || size > 65536 || !matches_output(output)) {
        gamma_failed(data, control);
        return;
    }
    size_t bytes = (size_t)size * 3 * sizeof(uint16_t);
    uint16_t *ramp = malloc(bytes);
    int fd = memfd_create("astral-display-color", MFD_CLOEXEC);
    if (!ramp || fd < 0) {
        free(ramp);
        if (fd >= 0) close(fd);
        gamma_failed(data, control);
        return;
    }
    fill_ramp(ramp, size, output->state->gain);
    size_t written = 0;
    while (written < bytes) {
        ssize_t count = write(fd, (char *)ramp + written, bytes - written);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) break;
        written += (size_t)count;
    }
    free(ramp);
    if (written != bytes || lseek(fd, 0, SEEK_SET) < 0) {
        close(fd);
        gamma_failed(data, control);
        return;
    }
    zwlr_gamma_control_v1_set_gamma(control, fd);
    close(fd);
    output->warned = false;
    fprintf(stderr, "%s: applied RGB gain %.3f to %s %s (%s)\n", output->name,
            output->state->gain, output->state->vendor, output->state->model, output->state->serial);
}

static const struct zwlr_gamma_control_v1_listener gamma_listener = {
    .gamma_size = gamma_size, .failed = gamma_failed,
};

static void consider_output(struct output *output) {
    struct state *state = output->state;
    if (output->control) {
        if (!matches_output(output)) release_control(output);
        return;
    }
    if (!state->apply || state->gain == 1.0 || !state->manager || !output->ready ||
        time(NULL) < output->retry_after || !matches_output(output)) return;
    output->control = zwlr_gamma_control_manager_v1_get_gamma_control(state->manager, output->proxy);
    zwlr_gamma_control_v1_add_listener(output->control, &gamma_listener, output);
}

static void output_name(void *data, struct wl_output *proxy, const char *name) {
    (void)proxy;
    struct output *output = data;
    free(output->name);
    output->name = strdup(name);
}
static void output_done(void *data, struct wl_output *proxy) {
    (void)proxy;
    struct output *output = data;
    output->ready = true;
    consider_output(output);
}
static void geometry(void *d, struct wl_output *o, int32_t x, int32_t y, int32_t w,
                     int32_t h, int32_t sub, const char *make, const char *model, int32_t transform) {
    (void)d; (void)o; (void)x; (void)y; (void)w; (void)h; (void)sub; (void)make; (void)model; (void)transform;
}
static void mode(void *d, struct wl_output *o, uint32_t flags, int32_t w, int32_t h, int32_t refresh) {
    (void)d; (void)o; (void)flags; (void)w; (void)h; (void)refresh;
}
static void scale(void *d, struct wl_output *o, int32_t factor) { (void)d; (void)o; (void)factor; }
static void description(void *d, struct wl_output *o, const char *text) { (void)d; (void)o; (void)text; }
static const struct wl_output_listener output_listener = {
    .geometry = geometry, .mode = mode, .done = output_done, .scale = scale,
    .name = output_name, .description = description,
};

static void global(void *data, struct wl_registry *registry, uint32_t name,
                   const char *interface, uint32_t version) {
    struct state *state = data;
    if (!strcmp(interface, zwlr_gamma_control_manager_v1_interface.name) && !state->manager) {
        state->manager = wl_registry_bind(registry, name, &zwlr_gamma_control_manager_v1_interface, 1);
        state->manager_name = name;
    } else if (!strcmp(interface, wl_output_interface.name) && version >= 4) {
        struct output *output = calloc(1, sizeof(*output));
        if (!output) { running = 0; return; }
        output->state = state;
        output->registry_name = name;
        output->proxy = wl_registry_bind(registry, name, &wl_output_interface, 4);
        output->next = state->outputs;
        state->outputs = output;
        wl_output_add_listener(output->proxy, &output_listener, output);
    }
}

static void free_output(struct output *output) {
    release_control(output);
    wl_output_release(output->proxy);
    free(output->name);
    free(output);
}
static void global_remove(void *data, struct wl_registry *registry, uint32_t name) {
    (void)registry;
    struct state *state = data;
    for (struct output **p = &state->outputs; *p; p = &(*p)->next) {
        if ((*p)->registry_name != name) continue;
        struct output *output = *p;
        *p = output->next;
        free_output(output);
        break;
    }
    if (state->manager && state->manager_name == name) {
        for (struct output *output = state->outputs; output; output = output->next)
            release_control(output);
        zwlr_gamma_control_manager_v1_destroy(state->manager);
        state->manager = NULL;
    }
}
static const struct wl_registry_listener registry_listener = {.global = global, .global_remove = global_remove};

static void usage(FILE *file) {
    fprintf(file, "Usage: display-color [--apply] --vendor ID --model MODEL --serial SERIAL --gain 0.5..1\n"
                  "Default: report matching outputs without changing gamma.\n"
                  "--sysfs-root DIR overrides /sys/class/drm for fixture-based tests.\n");
}

int main(int argc, char **argv) {
    struct state state = {.sysfs = "/sys/class/drm", .gain = 1.0};
    const struct option options[] = {
        {"apply", no_argument, NULL, 'a'}, {"vendor", required_argument, NULL, 'v'},
        {"model", required_argument, NULL, 'm'}, {"serial", required_argument, NULL, 's'},
        {"gain", required_argument, NULL, 'g'}, {"sysfs-root", required_argument, NULL, 'r'},
        {"help", no_argument, NULL, 'h'}, {NULL, 0, NULL, 0},
    };
    int option;
    while ((option = getopt_long(argc, argv, "", options, NULL)) != -1) {
        switch (option) {
        case 'a': state.apply = true; break;
        case 'v': state.vendor = optarg; break;
        case 'm': state.model = optarg; break;
        case 's': state.serial = optarg; break;
        case 'r': state.sysfs = optarg; break;
        case 'g': {
            char *end;
            state.gain = strtod(optarg, &end);
            if (!*optarg || *end || !isfinite(state.gain) || state.gain < 0.5 || state.gain > 1.0) {
                usage(stderr); return 2;
            }
            break;
        }
        case 'h': usage(stdout); return 0;
        default: usage(stderr); return 2;
        }
    }
    if (!state.vendor || strlen(state.vendor) != 3 || !state.model || !*state.model ||
        !state.serial || !*state.serial || optind != argc) { usage(stderr); return 2; }
    state.display = wl_display_connect(NULL);
    if (!state.display) { perror("Wayland connection"); return 1; }
    struct wl_registry *registry = wl_display_get_registry(state.display);
    wl_registry_add_listener(registry, &registry_listener, &state);
    int result = 0;
    if (wl_display_roundtrip(state.display) < 0 || wl_display_roundtrip(state.display) < 0) result = 1;
    if (!state.apply) {
        for (struct output *output = state.outputs; output; output = output->next)
            printf("%s: %s (RGB gain %.3f)\n", output->name ? output->name : "unknown",
                   matches_output(output) ? "matched" : "unchanged", state.gain);
    } else if (!state.manager) {
        fprintf(stderr, "Compositor does not support wlr-gamma-control\n"); result = 1;
    } else if (!result) {
        struct sigaction action = {.sa_handler = stop};
        sigemptyset(&action.sa_mask);
        sigaction(SIGTERM, &action, NULL);
        sigaction(SIGINT, &action, NULL);
        while (running) {
            for (struct output *output = state.outputs; output; output = output->next) consider_output(output);
            if (wl_display_dispatch_pending(state.display) < 0) { result = 1; break; }
            if (wl_display_flush(state.display) < 0 && errno != EAGAIN) { result = 1; break; }
            struct pollfd pollfd = {.fd = wl_display_get_fd(state.display), .events = POLLIN};
            int status = poll(&pollfd, 1, 1000);
            if (status < 0 && errno == EINTR) continue;
            if (status < 0 || (pollfd.revents & (POLLERR | POLLHUP | POLLNVAL))) { result = 1; break; }
            if (status && wl_display_dispatch(state.display) < 0) { result = 1; break; }
        }
    }
    while (state.outputs) {
        struct output *output = state.outputs;
        state.outputs = output->next;
        free_output(output);
    }
    if (state.manager) zwlr_gamma_control_manager_v1_destroy(state.manager);
    wl_registry_destroy(registry);
    wl_display_flush(state.display);
    wl_display_disconnect(state.display);
    return result;
}
