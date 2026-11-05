// SPDX-License-Identifier: AGPL-3.0-or-later
// Minimal protocol fixture: no GPU, real gamma requests and descriptor passing.
#include <assert.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include <wayland-server.h>
#include "wlr-gamma-control-unstable-v1-server-protocol.h"

struct server;
struct output { struct server *server; const char *name; struct wl_global *global; };
struct control { struct output *output; bool valid; };
struct server {
    struct wl_display *display;
    struct output target, other, replugged;
    bool hotplug, reject_first;
    unsigned claims;
    struct wl_event_source *timer;
};

static void record(const char *event, const char *output) {
    printf("{\"event\":\"%s\",\"output\":\"%s\"}\n", event, output);
    fflush(stdout);
}
static void destroy_request(struct wl_client *client, struct wl_resource *resource) {
    (void)client; wl_resource_destroy(resource);
}
static const struct wl_output_interface output_impl = {.release = destroy_request};
static void bind_output(struct wl_client *client, void *data, uint32_t version, uint32_t id) {
    struct output *output = data;
    struct wl_resource *resource = wl_resource_create(client, &wl_output_interface, version, id);
    wl_resource_set_implementation(resource, &output_impl, output, NULL);
    wl_output_send_geometry(resource, 0, 0, 540, 300, WL_OUTPUT_SUBPIXEL_UNKNOWN,
                            "Acer Technologies", "XB253Q", WL_OUTPUT_TRANSFORM_NORMAL);
    wl_output_send_mode(resource, WL_OUTPUT_MODE_CURRENT, 1920, 1080, 239760);
    wl_output_send_scale(resource, 1);
    if (version >= 4) {
        wl_output_send_name(resource, output->name);
        wl_output_send_description(resource, "Acer Technologies - XB253Q");
    }
    wl_output_send_done(resource);
}
static int replug(void *data) {
    struct server *server = data;
    wl_global_destroy(server->target.global);
    server->target.global = NULL;
    server->replugged.global = wl_global_create(server->display, &wl_output_interface, 4,
                                               &server->replugged, bind_output);
    return 0;
}
static void control_destroyed(struct wl_resource *resource) {
    struct control *control = wl_resource_get_user_data(resource);
    record(control->valid ? "restored" : "rejected-control-destroyed", control->output->name);
    free(control);
}
static void set_gamma(struct wl_client *client, struct wl_resource *resource, int32_t fd) {
    (void)client;
    struct control *control = wl_resource_get_user_data(resource);
    assert(control->valid);
    uint16_t ramps[3 * 32];
    struct stat statbuf;
    assert(!fstat(fd, &statbuf) && statbuf.st_size == sizeof(ramps));
    assert(read(fd, ramps, sizeof(ramps)) == sizeof(ramps));
    close(fd);
    for (size_t i = 0; i < 32; ++i) {
        uint16_t expected = (uint16_t)lround((double)i / 31 * 65535 * 0.9);
        assert(ramps[i] == expected && ramps[32 + i] == expected && ramps[64 + i] == expected);
    }
    record("ramp-verified", control->output->name);
    struct server *server = control->output->server;
    if (server->hotplug && control->output == &server->target) {
        server->timer = wl_event_loop_add_timer(wl_display_get_event_loop(server->display), replug, server);
        wl_event_source_timer_update(server->timer, 30);
    }
}
static const struct zwlr_gamma_control_v1_interface control_impl = {
    .set_gamma = set_gamma, .destroy = destroy_request,
};
static void get_control(struct wl_client *client, struct wl_resource *manager,
                        uint32_t id, struct wl_resource *output_resource) {
    struct server *server = wl_resource_get_user_data(manager);
    struct output *output = wl_resource_get_user_data(output_resource);
    struct control *control = calloc(1, sizeof(*control));
    assert(control);
    control->output = output;
    control->valid = !(server->reject_first && server->claims++ == 0);
    struct wl_resource *resource = wl_resource_create(client, &zwlr_gamma_control_v1_interface, 1, id);
    wl_resource_set_implementation(resource, &control_impl, control, control_destroyed);
    record("claimed", output->name);
    if (control->valid) zwlr_gamma_control_v1_send_gamma_size(resource, 32);
    else zwlr_gamma_control_v1_send_failed(resource);
}
static const struct zwlr_gamma_control_manager_v1_interface manager_impl = {
    .get_gamma_control = get_control, .destroy = destroy_request,
};
static void bind_manager(struct wl_client *client, void *data, uint32_t version, uint32_t id) {
    struct wl_resource *resource = wl_resource_create(client, &zwlr_gamma_control_manager_v1_interface, version, id);
    wl_resource_set_implementation(resource, &manager_impl, data, NULL);
}
int main(int argc, char **argv) {
    struct server server = {.hotplug = argc > 1 && !strcmp(argv[1], "--hotplug"),
                            .reject_first = argc > 1 && !strcmp(argv[1], "--reject-first")};
    server.display = wl_display_create();
    assert(server.display);
    assert(!wl_display_add_socket(server.display, "display-color-test"));
    server.target = (struct output){.server = &server, .name = "DP-5"};
    server.other = (struct output){.server = &server, .name = "DP-6"};
    server.replugged = (struct output){.server = &server, .name = "DP-3"};
    server.target.global = wl_global_create(server.display, &wl_output_interface, 4, &server.target, bind_output);
    server.other.global = wl_global_create(server.display, &wl_output_interface, 4, &server.other, bind_output);
    wl_global_create(server.display, &zwlr_gamma_control_manager_v1_interface, 1, &server, bind_manager);
    wl_display_run(server.display);
    wl_display_destroy_clients(server.display);
    wl_display_destroy(server.display);
    return 0;
}
