#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

typedef int (*real_linkat_fn)(int, const char *, int, const char *, int);

static uint64_t g_fallback_count = 0;
static uint64_t g_copied_bytes = 0;
static uint64_t g_copy_nanos = 0;

static uint64_t monotonic_ns(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) {
        return 0;
    }
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

static real_linkat_fn resolve_real_linkat(void) {
    static real_linkat_fn cached = NULL;
    real_linkat_fn fn = __atomic_load_n(&cached, __ATOMIC_ACQUIRE);
    if (!fn) {
        fn = (real_linkat_fn)dlsym(RTLD_NEXT, "linkat");
        __atomic_store_n(&cached, fn, __ATOMIC_RELEASE);
    }
    return fn;
}

static int emulate_linkat_by_copy(int olddirfd, const char *oldpath, int newdirfd, const char *newpath, int flags) {
    uint64_t t0 = monotonic_ns();
    struct stat st;
    int stat_flags = (flags & AT_SYMLINK_FOLLOW) ? 0 : AT_SYMLINK_NOFOLLOW;
    if (fstatat(olddirfd, oldpath, &st, stat_flags) != 0) {
        return -1;
    }

    if (S_ISLNK(st.st_mode)) {
        char link_target[PATH_MAX + 1];
        ssize_t n = readlinkat(olddirfd, oldpath, link_target, PATH_MAX);
        if (n < 0) {
            return -1;
        }
        link_target[n] = '\0';
        if (symlinkat(link_target, newdirfd, newpath) != 0) {
            return -1;
        }
        uint64_t dt = monotonic_ns() - t0;
        __atomic_fetch_add(&g_fallback_count, 1, __ATOMIC_RELAXED);
        __atomic_fetch_add(&g_copy_nanos, dt, __ATOMIC_RELAXED);
        errno = 0;
        return 0;
    }

    if (!S_ISREG(st.st_mode)) {
        errno = EPERM;
        return -1;
    }

    int src_fd = openat(olddirfd, oldpath, O_RDONLY | O_CLOEXEC);
    if (src_fd < 0) {
        return -1;
    }

    int dst_fd = openat(newdirfd, newpath, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, st.st_mode & 07777);
    if (dst_fd < 0) {
        int saved = errno;
        close(src_fd);
        errno = saved;
        return -1;
    }

    unsigned char buf[65536];
    uint64_t copied = 0;
    int failed_errno = 0;

    for (;;) {
        ssize_t r = read(src_fd, buf, sizeof(buf));
        if (r < 0) {
            if (errno == EINTR) {
                continue;
            }
            failed_errno = errno;
            break;
        }
        if (r == 0) {
            break;
        }
        size_t off = 0;
        while (off < (size_t)r) {
            ssize_t w = write(dst_fd, buf + off, (size_t)r - off);
            if (w < 0) {
                if (errno == EINTR) {
                    continue;
                }
                failed_errno = errno;
                break;
            }
            off += (size_t)w;
        }
        if (failed_errno != 0) {
            break;
        }
        copied += (uint64_t)r;
    }

    if (close(dst_fd) != 0 && failed_errno == 0) {
        failed_errno = errno;
    }
    close(src_fd);

    if (failed_errno != 0) {
        unlinkat(newdirfd, newpath, 0);
        errno = failed_errno;
        return -1;
    }

    uint64_t dt = monotonic_ns() - t0;
    __atomic_fetch_add(&g_fallback_count, 1, __ATOMIC_RELAXED);
    __atomic_fetch_add(&g_copied_bytes, copied, __ATOMIC_RELAXED);
    __atomic_fetch_add(&g_copy_nanos, dt, __ATOMIC_RELAXED);
    errno = 0;
    return 0;
}

int linkat(int olddirfd, const char *oldpath, int newdirfd, const char *newpath, int flags) {
    real_linkat_fn real_fn = resolve_real_linkat();
    if (!real_fn) {
        errno = ENOSYS;
        return -1;
    }
    int rc = real_fn(olddirfd, oldpath, newdirfd, newpath, flags);
    if (rc == 0) {
        return 0;
    }
    if (errno == EOPNOTSUPP || errno == ENOTSUP || errno == ENOSYS) {
        return emulate_linkat_by_copy(olddirfd, oldpath, newdirfd, newpath, flags);
    }
    return -1;
}

int link(const char *oldpath, const char *newpath) {
    return linkat(AT_FDCWD, oldpath, AT_FDCWD, newpath, 0);
}

__attribute__((destructor)) static void write_hardlink_copy_stats(void) {
    const char *stats_path = getenv("AX_HARDLINK_COPY_STATS_PATH");
    if (!stats_path || stats_path[0] == '\0') {
        return;
    }
    uint64_t count = __atomic_load_n(&g_fallback_count, __ATOMIC_RELAXED);
    uint64_t bytes = __atomic_load_n(&g_copied_bytes, __ATOMIC_RELAXED);
    uint64_t nanos = __atomic_load_n(&g_copy_nanos, __ATOMIC_RELAXED);
    double secs = (double)nanos / 1e9;

    char json_buf[512];
    int len = snprintf(
        json_buf,
        sizeof(json_buf),
        "{\"enabled\": true, \"fallback_count\": %" PRIu64 ", \"copied_bytes\": %" PRIu64 ", \"copy_seconds\": %.6f}\n",
        count,
        bytes,
        secs
    );
    if (len <= 0 || (size_t)len >= sizeof(json_buf)) {
        return;
    }
    int fd = open(stats_path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd < 0) {
        return;
    }
    size_t off = 0;
    while (off < (size_t)len) {
        ssize_t w = write(fd, json_buf + off, (size_t)len - off);
        if (w <= 0) {
            if (w < 0 && errno == EINTR) {
                continue;
            }
            break;
        }
        off += (size_t)w;
    }
    close(fd);
}
