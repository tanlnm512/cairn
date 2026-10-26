//! Minimal `.tar.gz` writer (ustar + stored-block gzip): no tar/flate2 crates
//! in the tree, and the export contract only needs a valid, readable tarball.

use std::io::Write;
use std::path::{Path, PathBuf};

/// ustar mode bits of a file: the Unix permission bits, 0o644 elsewhere.
fn file_mode(meta: &std::fs::Metadata) -> u32 {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt as _;
        meta.permissions().mode() & 0o777
    }
    #[cfg(not(unix))]
    {
        let _ = meta;
        0o644
    }
}

const BLOCK: usize = 512;
const NAME_MAX: usize = 100;
const PREFIX_MAX: usize = 155;

fn octal_field(out: &mut [u8], value: u64) {
    let text = format!("{value:011o}");
    let bytes = text.as_bytes();
    let n = bytes.len().min(out.len());
    out[..n].copy_from_slice(&bytes[..n]);
}

/// Where a member name lives in the ustar header.
enum MemberName {
    /// Fits the 100-byte `name` field.
    Short(String),
    /// Split across the 155-byte `prefix` field and `name`.
    Split { prefix: String, name: String },
    /// Needs a GNU long-name entry; `name` carries a 100-byte fallback.
    Long { name: String },
}

/// Python tarfile never loses a member name; neither may we.
fn member_name(path: &str) -> MemberName {
    if path.len() <= NAME_MAX {
        return MemberName::Short(path.to_string());
    }
    // Split at the last '/' that leaves <=100 name bytes and <=155 prefix bytes.
    let mut split = None;
    for (i, b) in path.bytes().enumerate() {
        if b != b'/' || i == 0 || path.len() - i - 1 > NAME_MAX || i > PREFIX_MAX {
            continue;
        }
        split = Some(i);
    }
    match split {
        Some(i) => MemberName::Split {
            prefix: path[..i].to_string(),
            name: path[i + 1..].to_string(),
        },
        None => MemberName::Long {
            name: {
                let mut end = NAME_MAX;
                while !path.is_char_boundary(end) {
                    end -= 1;
                }
                path[..end].to_string()
            },
        },
    }
}

fn header(path: &str, size: u64, mtime: u64, mode: u32, is_dir: bool) -> [u8; BLOCK] {
    header_with_typeflag(path, size, mtime, mode, if is_dir { b'5' } else { b'0' })
}

fn header_with_typeflag(path: &str, size: u64, mtime: u64, mode: u32, typeflag: u8) -> [u8; BLOCK] {
    let mut h = [0u8; BLOCK];
    match member_name(path) {
        MemberName::Short(name) | MemberName::Long { name, .. } => {
            let bytes = name.as_bytes();
            h[..bytes.len()].copy_from_slice(bytes);
        }
        MemberName::Split { prefix, name } => {
            let bytes = name.as_bytes();
            h[..bytes.len()].copy_from_slice(bytes);
            let prefix = prefix.as_bytes();
            h[345..345 + prefix.len()].copy_from_slice(prefix);
        }
    }
    let mode_text = format!("{:07o}", mode & 0o7777);
    h[100..100 + mode_text.len()].copy_from_slice(mode_text.as_bytes());
    h[108..115].copy_from_slice(b"0000000"); // uid
    h[116..123].copy_from_slice(b"0000000"); // gid
    octal_field(&mut h[124..136], size);
    octal_field(&mut h[136..148], mtime);
    h[148..156].copy_from_slice(b"        "); // checksum placeholder
    h[156] = typeflag;
    h[257..263].copy_from_slice(b"ustar\0"); // magic
    h[263..265].copy_from_slice(b"00"); // version
    let sum: u32 = h.iter().map(|b| *b as u32).sum();
    let checksum = format!("{sum:06o}\0 ");
    h[148..156].copy_from_slice(checksum.as_bytes());
    h
}

/// A GNU long-name entry (`././@LongLink`) carrying `name` for the next header.
fn longname_entry(name: &str) -> Vec<u8> {
    let data = format!("{name}\0").into_bytes();
    let mut out = Vec::with_capacity(BLOCK + data.len() + BLOCK);
    let h = header_with_typeflag("././@LongLink", data.len() as u64, 0, 0o644, b'L');
    out.extend_from_slice(&h);
    out.extend_from_slice(&data);
    let pad = (BLOCK - (data.len() % BLOCK)) % BLOCK;
    out.extend(std::iter::repeat_n(0u8, pad));
    out
}

fn dir_entries(root: &Path) -> std::io::Result<Vec<(bool, std::fs::DirEntry)>> {
    let mut out = Vec::new();
    for entry in std::fs::read_dir(root)? {
        let entry = entry?;
        out.push((entry.file_type()?.is_dir(), entry));
    }
    Ok(out)
}

fn walk_sorted(
    root: &Path,
    arc_prefix: &str,
    out: &mut Vec<(String, u64, u32, u64, bool, PathBuf)>,
) {
    let Ok(mut entries) = dir_entries(root) else {
        return;
    };
    entries.sort_by_key(|(_, e)| e.file_name());
    for (is_dir, entry) in entries {
        let name = entry.file_name().to_string_lossy().to_string();
        let arcname = if arc_prefix.is_empty() {
            name
        } else {
            format!("{arc_prefix}/{name}")
        };
        let path = entry.path();
        if is_dir {
            out.push((
                arcname.clone(),
                0,
                0o755,
                mtime_of(&path),
                true,
                path.clone(),
            ));
            walk_sorted(&path, &arcname, out);
        } else {
            let Ok(meta) = std::fs::metadata(&path) else {
                continue;
            };
            let mode = file_mode(&meta);
            out.push((arcname, meta.len(), mode, mtime_of(&path), false, path));
        }
    }
}

fn mtime_of(path: &Path) -> u64 {
    std::fs::metadata(path)
        .and_then(|m| m.modified())
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn crc32(data: &[u8]) -> u32 {
    let mut table = [0u32; 256];
    for (i, slot) in table.iter_mut().enumerate() {
        let mut c = i as u32;
        for _ in 0..8 {
            c = if c & 1 != 0 {
                0xEDB8_8320 ^ (c >> 1)
            } else {
                c >> 1
            };
        }
        *slot = c;
    }
    let mut crc = 0xFFFF_FFFFu32;
    for byte in data {
        crc = table[((crc ^ *byte as u32) & 0xFF) as usize] ^ (crc >> 8);
    }
    crc ^ 0xFFFF_FFFF
}

/// Write `root` recursively as a ustar archive under `arcname`.
pub fn write_tar(root: &Path, arcname: &str, out_path: &Path) -> std::io::Result<()> {
    std::fs::write(out_path, tar_bytes(root, arcname)?)
}

/// Write `root` recursively as a gzip'd ustar archive under `arcname`.
pub fn write_tar_gz(root: &Path, arcname: &str, out_path: &Path) -> std::io::Result<()> {
    let tar = tar_bytes(root, arcname)?;
    let file = std::fs::File::create(out_path)?;
    let mut gz = std::io::BufWriter::new(file);
    // gzip header: deflate, no flags, mtime 0, unknown OS.
    gz.write_all(&[0x1f, 0x8b, 0x08, 0x00, 0, 0, 0, 0, 0x00, 0xff])?;
    // deflate stored blocks: valid, deterministic, no compressor dependency.
    let mut offset = 0usize;
    while offset < tar.len() {
        let chunk = &tar[offset..(offset + 65_535).min(tar.len())];
        let last = offset + chunk.len() >= tar.len();
        let len = chunk.len() as u16;
        gz.write_all(&[if last { 1 } else { 0 }])?;
        gz.write_all(&len.to_le_bytes())?;
        gz.write_all(&(!len).to_le_bytes())?;
        gz.write_all(chunk)?;
        offset += chunk.len();
    }
    gz.write_all(&crc32(&tar).to_le_bytes())?;
    gz.write_all(&(tar.len() as u32).to_le_bytes())?;
    gz.flush()
}

fn tar_bytes(root: &Path, arcname: &str) -> std::io::Result<Vec<u8>> {
    let mut tar: Vec<u8> = Vec::new();
    let mtime = mtime_of(root);
    tar.extend_from_slice(&header(arcname, 0, mtime, 0o755, true));
    let mut entries = Vec::new();
    walk_sorted(root, arcname, &mut entries);
    for (name, size, mode, mtime, is_dir, path) in &entries {
        if matches!(member_name(name), MemberName::Long { .. }) {
            tar.extend(longname_entry(name));
        }
        tar.extend_from_slice(&header(name, *size, *mtime, *mode, *is_dir));
        if !*is_dir && *size > 0 {
            let data = std::fs::read(path)?;
            tar.extend_from_slice(&data);
            let pad = (BLOCK - (data.len() % BLOCK)) % BLOCK;
            tar.extend(std::iter::repeat_n(0u8, pad));
        }
    }
    tar.extend(std::iter::repeat_n(0u8, BLOCK * 2));
    Ok(tar)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn crc32_matches_the_reference_vector() {
        assert_eq!(crc32(b"123456789"), 0xCBF4_3926);
        assert_eq!(crc32(b""), 0);
    }

    #[test]
    fn tarball_round_trips_headers_and_bytes() {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("cairn-targz-{}-{nanos}", std::process::id()));
        let root = dir.join(".knowledge");
        std::fs::create_dir_all(root.join("sub")).unwrap();
        std::fs::write(root.join("a.md"), b"alpha").unwrap();
        std::fs::write(root.join("sub").join("b.md"), b"beta!").unwrap();

        let out = dir.join("bundle.tar.gz");
        write_tar_gz(&root, ".knowledge", &out).unwrap();
        let raw = std::fs::read(&out).unwrap();

        // gzip envelope
        assert_eq!(&raw[..3], &[0x1f, 0x8b, 0x08]);
        let crc = u32::from_le_bytes(raw[raw.len() - 8..raw.len() - 4].try_into().unwrap());
        let isize_ = u32::from_le_bytes(raw[raw.len() - 4..].try_into().unwrap());
        assert_eq!(isize_ as usize, gunzip_stored(&raw).len());
        assert_eq!(crc32(&gunzip_stored(&raw)), crc);

        // tar structure
        let tar = gunzip_stored(&raw);
        assert_eq!(&tar[257..263], b"ustar\0");
        assert_eq!(&tar[0..11], b".knowledge\0");
        assert_eq!(&tar[156..157], b"5"); // dir entry
        let members = member_names(&tar);
        assert_eq!(
            members.iter().map(|(n, _)| n.as_str()).collect::<Vec<_>>(),
            vec![
                ".knowledge",
                ".knowledge/a.md",
                ".knowledge/sub",
                ".knowledge/sub/b.md"
            ]
        );
        let a = find_entry(&tar, ".knowledge/a.md");
        assert_eq!(&tar[a..a + 5], b"alpha");
        let b = find_entry(&tar, ".knowledge/sub/b.md");
        assert_eq!(&tar[b..b + 5], b"beta!");
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn long_member_names_survive_prefix_and_longlink_paths() {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir =
            std::env::temp_dir().join(format!("cairn-targz-long-{}-{nanos}", std::process::id()));
        let root = dir.join(".knowledge");
        // 106-byte member name: needs the ustar prefix field.
        let prefix_name = format!(
            ".knowledge/memory/raw/2026-10-16-cairn-doctor-exits-1-on-fail-environment-zcode-{}-f7cbd0.md",
            "x".repeat(16)
        );
        assert!(prefix_name.len() > NAME_MAX && prefix_name.len() <= NAME_MAX + PREFIX_MAX + 1);
        std::fs::create_dir_all(root.join("memory/raw")).unwrap();
        std::fs::write(root.join(&prefix_name[".knowledge/".len()..]), b"doc").unwrap();
        // 256+ byte member name: needs a GNU long-name entry.
        let deep = vec!["dddddddd"; 30].join("/");
        let long_name = format!(".knowledge/{deep}/n.md");
        assert!(long_name.len() > NAME_MAX + PREFIX_MAX + 1);
        std::fs::create_dir_all(root.join(&deep)).unwrap();
        std::fs::write(root.join(&deep).join("n.md"), b"deep").unwrap();

        let out = dir.join("bundle.tar.gz");
        write_tar_gz(&root, ".knowledge", &out).unwrap();
        let tar = gunzip_stored(&std::fs::read(&out).unwrap());
        let members = member_names(&tar);
        let names: Vec<&str> = members.iter().map(|(n, _)| n.as_str()).collect();
        assert!(
            names.contains(&prefix_name.as_str()),
            "prefix name lost: {names:?}"
        );
        assert!(
            names.contains(&long_name.as_str()),
            "long name lost: {names:?}"
        );
        // The long-name carrier entry itself is metadata, not a member.
        assert!(!names.iter().any(|n| n.contains("@LongLink")));
        assert_eq!(
            &tar[find_entry(&tar, &prefix_name)..find_entry(&tar, &prefix_name) + 3],
            b"doc"
        );
        assert_eq!(
            &tar[find_entry(&tar, &long_name)..find_entry(&tar, &long_name) + 4],
            b"deep"
        );
        std::fs::remove_dir_all(&dir).ok();
    }

    fn gunzip_stored(raw: &[u8]) -> Vec<u8> {
        let mut out = Vec::new();
        let mut offset = 10;
        while offset < raw.len() - 8 {
            let bfinal = raw[offset] & 1;
            assert_eq!(raw[offset] >> 1, 0, "stored blocks only");
            let len = u16::from_le_bytes(raw[offset + 1..offset + 3].try_into().unwrap()) as usize;
            out.extend_from_slice(&raw[offset + 5..offset + 5 + len]);
            offset += 5 + len;
            if bfinal == 1 {
                break;
            }
        }
        out
    }

    fn header_size(tar: &[u8], offset: usize) -> u64 {
        let size_text = String::from_utf8_lossy(&tar[offset + 124..offset + 136]);
        u64::from_str_radix(size_text.trim_end_matches(['\0', ' ']).trim(), 8).unwrap_or(0)
    }

    /// (member name, data offset) pairs; resolves prefix fields and GNU
    /// long-name entries the way tar and Python tarfile do — including
    /// rejecting headers whose checksum does not match, like both real readers.
    fn member_names(tar: &[u8]) -> Vec<(String, usize)> {
        let mut out = Vec::new();
        let mut offset = 0;
        let mut pending_long: Option<String> = None;
        while offset + BLOCK <= tar.len() {
            if tar[offset..offset + BLOCK].iter().all(|b| *b == 0) {
                break;
            }
            let stored = &tar[offset..offset + BLOCK];
            let stored_sum =
                u64::from_str_radix(String::from_utf8_lossy(&stored[148..154]).trim(), 8)
                    .unwrap_or(u64::MAX);
            let computed: u32 = stored
                .iter()
                .enumerate()
                .map(|(i, b)| {
                    if (148..156).contains(&i) {
                        b' ' as u32
                    } else {
                        *b as u32
                    }
                })
                .sum();
            assert_eq!(
                stored_sum, computed as u64,
                "bad checksum at header offset {offset}: stored 0o{stored_sum:o} computed 0o{computed:o}"
            );
            let size = header_size(tar, offset);
            let data_at = offset + BLOCK;
            let field = |from: usize, to: usize| -> String {
                let raw = &tar[offset + from..offset + to];
                let end = raw.iter().position(|b| *b == 0).unwrap_or(raw.len());
                String::from_utf8_lossy(&raw[..end]).to_string()
            };
            let typeflag = tar[offset + 156];
            let padded = BLOCK
                + (size as usize / BLOCK) * BLOCK
                + usize::from(!(size as usize).is_multiple_of(BLOCK)) * BLOCK;
            match typeflag {
                b'L' => {
                    let end = tar[data_at..]
                        .iter()
                        .position(|b| *b == 0)
                        .unwrap_or(size as usize);
                    pending_long =
                        Some(String::from_utf8_lossy(&tar[data_at..data_at + end]).to_string());
                }
                _ => {
                    let name = match pending_long.take() {
                        Some(long) => long,
                        None => {
                            let prefix = field(345, 500);
                            let name = field(0, 100);
                            if prefix.is_empty() {
                                name
                            } else {
                                format!("{prefix}/{name}")
                            }
                        }
                    };
                    out.push((name, data_at));
                }
            }
            offset += padded;
        }
        out
    }

    fn find_entry(tar: &[u8], name: &str) -> usize {
        member_names(tar)
            .into_iter()
            .find(|(n, _)| n == name)
            .map(|(_, at)| at)
            .unwrap_or_else(|| panic!("entry {name} not found"))
    }
}
