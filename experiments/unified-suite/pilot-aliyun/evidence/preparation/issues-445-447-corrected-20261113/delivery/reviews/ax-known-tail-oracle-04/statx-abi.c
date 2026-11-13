#define _GNU_SOURCE
#include <fcntl.h>
#include <stddef.h>
#include <stdio.h>
#include <sys/stat.h>

#define FIELD(name) printf("\"%s\":%zu,", #name, offsetof(struct statx, name))

int main(void) {
    printf("{\"size\":%zu,\"timestamp_size\":%zu,", sizeof(struct statx), sizeof(struct statx_timestamp));
    printf("\"flags\":%u,\"mask\":%u,\"offsets\":{", AT_EMPTY_PATH | AT_STATX_FORCE_SYNC, STATX_BASIC_STATS);
    FIELD(stx_mask); FIELD(stx_blksize); FIELD(stx_attributes);
    FIELD(stx_nlink); FIELD(stx_uid); FIELD(stx_gid); FIELD(stx_mode);
    FIELD(stx_ino); FIELD(stx_size); FIELD(stx_blocks); FIELD(stx_attributes_mask);
    FIELD(stx_atime); FIELD(stx_btime); FIELD(stx_ctime); FIELD(stx_mtime);
    FIELD(stx_rdev_major); FIELD(stx_rdev_minor); FIELD(stx_dev_major); FIELD(stx_dev_minor);
    FIELD(stx_mnt_id); FIELD(stx_dio_mem_align);
    printf("\"stx_dio_offset_align\":%zu}}\n", offsetof(struct statx, stx_dio_offset_align));
    return 0;
}
