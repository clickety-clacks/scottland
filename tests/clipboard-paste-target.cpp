#include <gtk/gtk.h>

#include <fstream>
#include <string>

struct Target {
    std::string ready_path;
    std::string result_path;
    std::string expected;
};

static void mark_ready(GtkWidget *, gpointer data) {
    auto *target = static_cast<Target *>(data);
    std::ofstream ready(target->ready_path, std::ios::binary | std::ios::trunc);
    ready << "ready\n";
}

static void text_changed(GtkEditable *editable, gpointer data) {
    auto *target = static_cast<Target *>(data);
    const char *text = gtk_editable_get_text(editable);
    if (target->expected != text) return;
    std::ofstream result(target->result_path, std::ios::binary | std::ios::trunc);
    result.write(text, static_cast<std::streamsize>(target->expected.size()));
}

static void activate(GtkApplication *application, gpointer data) {
    auto *target = static_cast<Target *>(data);
    GtkWidget *window = gtk_application_window_new(application);
    gtk_window_set_title(GTK_WINDOW(window), "Paste target");
    gtk_window_set_default_size(GTK_WINDOW(window), 480, 100);

    GtkWidget *entry = gtk_entry_new();
    gtk_editable_set_text(GTK_EDITABLE(entry), "");
    g_signal_connect(entry, "changed", G_CALLBACK(text_changed), target);
    g_signal_connect(window, "map", G_CALLBACK(mark_ready), target);
    gtk_window_set_child(GTK_WINDOW(window), entry);
    gtk_window_present(GTK_WINDOW(window));
    gtk_widget_grab_focus(entry);
}

int main(int argc, char **argv) {
    if (argc != 5) return 2;
    Target target{argv[2], argv[3], argv[4]};
    GtkApplication *application = gtk_application_new(argv[1], G_APPLICATION_NON_UNIQUE);
    g_signal_connect(application, "activate", G_CALLBACK(activate), &target);
    char *application_argv[] = {argv[0], nullptr};
    const int status = g_application_run(G_APPLICATION(application), 1, application_argv);
    g_object_unref(application);
    return status;
}
