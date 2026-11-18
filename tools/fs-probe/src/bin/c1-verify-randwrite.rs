use std::fs::{self, File};
use std::io::{self, BufReader, Read};
use std::path::Path;

const BLOCK: usize = 4096;
const FILE_BYTES: usize = 256 * 1024 * 1024;
const WRITES: usize = 2048;
const JOBS: usize = 8;

fn invalid(message: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message.into())
}

/// Reconstruct a block produced by c1-io-helper init-files.
fn base_page(job: usize, page: usize, out: &mut [u8; BLOCK]) {
    let mut state = ((job as u64) << 48) ^ page as u64 ^ 0x4331_4649_4f33_3247;
    for word in out.chunks_exact_mut(8) {
        state = state.wrapping_add(0x9e37_79b9_7f4a_7c15);
        let mut z = state;
        z = (z ^ (z >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
        word.copy_from_slice(&(z ^ (z >> 31)).to_le_bytes());
    }
}

fn positions(folder: &Path, job: usize) -> io::Result<Vec<usize>> {
    let log = fs::read_to_string(folder.join(format!("writes-{job}.iolog")))?;
    let name = format!("job-{job}.dat");
    let mut lines = log.lines();
    if lines.next() != Some("fio version 3 iolog") {
        return Err(invalid("expected fio version 3 iolog"));
    }
    let mut pages = Vec::with_capacity(WRITES);
    let mut phase = 0;
    for line in lines {
        let cols: Vec<_> = line.split_ascii_whitespace().collect();
        if cols.len() < 3 || cols[1].rsplit('/').next() != Some(&name) {
            return Err(invalid(format!(
                "unexpected iolog record for {name}: {line}"
            )));
        }
        match (phase, cols[2]) {
            (0, "add") => phase = 1,
            (1, "open") => phase = 2,
            (2, "write") if cols.len() == 5 && cols[4] == "4096" => {
                let offset: usize = cols[3].parse().map_err(|_| invalid("invalid fio offset"))?;
                if offset >= FILE_BYTES || !offset.is_multiple_of(BLOCK) {
                    return Err(invalid("fio offset outside the aligned working set"));
                }
                pages.push(offset / BLOCK);
            }
            (2, "sync") if pages.len() == WRITES => phase = 3,
            (3, "close") => phase = 4,
            _ => return Err(invalid(format!("unexpected fio operation: {line}"))),
        }
    }
    if phase != 4 {
        return Err(invalid(format!("incomplete iolog for {name}")));
    }
    Ok(pages)
}

/// Obtain byte order from a separate 4 KiB file written by the same fio binary
/// with buffer_pattern=0x5a3c96e1 on a local filesystem before measurement.
fn load_pattern(path: &Path) -> io::Result<[u8; BLOCK]> {
    let data = fs::read(path)?;
    let word = 0x5a3c96e1u32;
    if data.len() != BLOCK
        || ![word.to_le_bytes(), word.to_be_bytes()]
            .iter()
            .any(|bytes| data.chunks_exact(4).all(|part| part == bytes))
    {
        return Err(invalid("fio pattern control differs"));
    }
    Ok(data.try_into().expect("validated control length"))
}

fn main() -> io::Result<()> {
    let argv: Vec<_> = std::env::args_os().skip(1).collect();
    let [mount, original_logs, actual_logs, calibration] = argv.as_slice() else {
        return Err(invalid(
            "usage: verify_image MOUNT ORIGINAL_IOLOGS ACTUAL_IOLOGS FIO_PATTERN_FILE",
        ));
    };
    let pattern = load_pattern(Path::new(calibration))?;
    let mut buf = [0; BLOCK];
    let mut base = [0; BLOCK];
    let mut written = 0;
    let mut unchanged = 0;
    for job in 0..JOBS {
        let original = positions(Path::new(original_logs), job)?;
        let actual = positions(Path::new(actual_logs), job)?;
        if original != actual {
            return Err(invalid(format!("offset sequence differs for job {job}")));
        }
        let mut changed = vec![false; FILE_BYTES / BLOCK];
        for page in actual {
            if std::mem::replace(&mut changed[page], true) {
                return Err(invalid(format!(
                    "duplicate write in job {job} at page {page}"
                )));
            }
        }
        let file = File::open(Path::new(mount).join(format!("job-{job}.dat")))?;
        if file.metadata()?.len() != FILE_BYTES as u64 {
            return Err(invalid(format!("file size differs for job {job}")));
        }
        let mut file = BufReader::with_capacity(1024 * 1024, file);
        for (page, changed) in changed.into_iter().enumerate() {
            file.read_exact(&mut buf)?;
            let expected = if changed {
                written += 1;
                &pattern
            } else {
                unchanged += 1;
                base_page(job, page, &mut base);
                &base
            };
            if let Some(i) = buf.iter().zip(expected).position(|(a, b)| a != b) {
                return Err(invalid(format!(
                    "job {job} offset {}: expected {:02x}, read {:02x} (written={changed})",
                    page * BLOCK + i,
                    expected[i],
                    buf[i]
                )));
            }
        }
    }
    println!(
        "{{\"written_pages\":{written},\"unchanged_pages\":{unchanged},\"bytes_compared\":{}}}",
        JOBS * FILE_BYTES
    );
    Ok(())
}
