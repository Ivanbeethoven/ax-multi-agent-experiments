//! Optional directory-FD reuse for path-based replay of inode-level callbacks.
//! Every namespace/permission mutation invalidates before and after the operation.
use std::{collections::HashMap,fs::{self,File,OpenOptions},io,
    os::unix::{fs::OpenOptionsExt,io::AsRawFd},path::{Path,PathBuf}};

const CAPACITY:usize=256;

pub struct ParentPaths {
    directories:HashMap<PathBuf,File>,dirty:bool,
    pub hits:u64,pub misses:u64,pub invalidations:u64,pub peak:usize,
}

impl ParentPaths {
    pub fn new()->Self { Self{directories:HashMap::new(),dirty:false,hits:0,misses:0,invalidations:0,peak:0} }
    pub fn before(&mut self,op:&str) {
        let mutation=matches!(op,"create"|"mkdir"|"rmdir"|"unlink"|"rename"|"symlink"|"link"|"chmod"|"chown");
        // An operation resolves at most two paths. Never close an FD between
        // resolving the source and destination of the same rename/link.
        if self.dirty || mutation || self.directories.len()+2>CAPACITY {
            self.directories.clear();self.invalidations+=1;
        }
        self.dirty=mutation;
    }
    pub fn resolve(&mut self,root:&Path,path:&Path,follow:bool,trusted:bool)->io::Result<PathBuf> {
        if path==root{return Ok(path.to_owned())}
        let parent=path.parent().ok_or_else(||io::Error::other("missing parent"))?;
        if !self.directories.contains_key(parent) {
            let directory=if trusted {parent.to_owned()} else {parent.canonicalize()?};
            if !trusted && !directory.starts_with(root){return Err(io::Error::other("path escapes root"))}
            let file=OpenOptions::new().read(true).custom_flags(libc::O_PATH|libc::O_DIRECTORY|libc::O_CLOEXEC).open(directory)?;
            self.directories.insert(parent.to_owned(),file);self.misses+=1;
            self.peak=self.peak.max(self.directories.len());
        } else {self.hits+=1}
        let fd=self.directories[parent].as_raw_fd();
        let leaf=PathBuf::from(format!("/proc/self/fd/{fd}")).join(path.file_name().unwrap());
        if !trusted && follow {
            match fs::symlink_metadata(&leaf) {
                Ok(m) if m.is_symlink()=>{
                    let target=leaf.canonicalize()?;
                    if !target.starts_with(root){return Err(io::Error::other("path escapes root"))}
                },
                Err(e) if e.kind()!=io::ErrorKind::NotFound=>return Err(e),
                _=>{},
            }
        }
        Ok(leaf)
    }
    pub fn capacity()->usize {CAPACITY}
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64,Ordering};
    static NEXT:AtomicU64=AtomicU64::new(0);
    struct Tree(PathBuf);
    impl Tree {fn new()->Self {
        let root=std::env::temp_dir().join(format!("ax-parent-fd-{}-{}",std::process::id(),NEXT.fetch_add(1,Ordering::Relaxed)));
        fs::create_dir(&root).unwrap();Self(root)
    }}
    impl Drop for Tree {fn drop(&mut self){fs::remove_dir_all(&self.0).unwrap()}}
    #[test] fn reuses_directory_and_releases_on_mutation() {
        let t=Tree::new();fs::write(t.0.join("unique-release-probe"),b"right").unwrap();let mut c=ParentPaths::new();
        c.before("getattr");let first=c.resolve(&t.0,&t.0.join("unique-release-probe"),false,false).unwrap();
        c.before("getattr");assert_eq!(fs::read(c.resolve(&t.0,&t.0.join("unique-release-probe"),false,false).unwrap()).unwrap(),b"right");
        assert_eq!((c.hits,c.misses),(1,1));c.before("rename");assert!(c.directories.is_empty() && fs::metadata(first).is_err());
    }
    #[test] fn replacement_parent_does_not_keep_old_directory() {
        let t=Tree::new();let mut c=ParentPaths::new();fs::create_dir(t.0.join("dir")).unwrap();fs::write(t.0.join("dir/file"),b"old").unwrap();
        c.before("getattr");c.resolve(&t.0,&t.0.join("dir/file"),false,false).unwrap();
        c.before("rename");fs::rename(t.0.join("dir"),t.0.join("moved")).unwrap();
        fs::create_dir(t.0.join("dir")).unwrap();fs::write(t.0.join("dir/file"),b"new").unwrap();
        c.before("getattr");assert_eq!(fs::read(c.resolve(&t.0,&t.0.join("dir/file"),false,false).unwrap()).unwrap(),b"new");
    }
    #[test] fn strict_follow_rejects_outside_link_but_lstat_can_inspect_it() {
        let t=Tree::new();let outside=Tree::new();fs::write(outside.0.join("file"),b"outside").unwrap();
        std::os::unix::fs::symlink(outside.0.join("file"),t.0.join("link")).unwrap();let mut c=ParentPaths::new();
        c.before("getattr");assert!(fs::symlink_metadata(c.resolve(&t.0,&t.0.join("link"),false,false).unwrap()).unwrap().is_symlink());
        assert!(c.resolve(&t.0,&t.0.join("link"),true,false).is_err());
        std::os::unix::fs::symlink(&outside.0,t.0.join("escape")).unwrap();
        assert!(c.resolve(&t.0,&t.0.join("escape/file"),false,false).is_err());
    }
    #[test] fn descriptors_are_bounded() {
        let t=Tree::new();let mut c=ParentPaths::new();
        for i in 0..CAPACITY*2 {let d=t.0.join(i.to_string());fs::create_dir(&d).unwrap();c.before("getattr");c.resolve(&t.0,&d.join("file"),false,false).unwrap();}
        assert!(c.peak<=CAPACITY);assert!(c.invalidations>0);
    }
}
