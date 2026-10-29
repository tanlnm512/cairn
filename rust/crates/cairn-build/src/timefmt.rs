//! Clock values: UTC ISO-8601 timestamps and `st_mtime` doubles.

use std::time::{SystemTime, UNIX_EPOCH};

/// `datetime.now(timezone.utc).isoformat()` shape: `YYYY-MM-DDTHH:MM:SS.ffffff+00:00`.
pub fn now_iso() -> String {
    let d = SystemTime::now().duration_since(UNIX_EPOCH).expect("clock");
    iso_from_epoch(d.as_secs() as i64, d.subsec_micros())
}

/// ISO-8601 UTC from an epoch split into whole seconds and microseconds.
pub fn iso_from_epoch(secs: i64, micros: u32) -> String {
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400);
    let (year, month, day) = civil_from_days(days);
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}.{micros:06}+00:00",
        rem / 3600,
        (rem % 3600) / 60,
        rem % 60,
    )
}

/// `st_mtime` as the `sec + nsec/1e9` double.
pub fn mtime_f64(metadata: &std::fs::Metadata) -> Option<f64> {
    let t = metadata.modified().ok()?.duration_since(UNIX_EPOCH).ok()?;
    Some(mtime_from_times(t.as_secs() as i64, t.subsec_nanos()))
}

/// The `sec + nsec/1e9` double for the given timestamp parts.
pub fn mtime_from_times(secs: i64, nanos: u32) -> f64 {
    secs as f64 + nanos as f64 / 1e9
}

/// Howard Hinnant's days-to-civil algorithm (proleptic Gregorian).
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if m <= 2 { y + 1 } else { y }, m, d)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn iso_matches_datetime_isoformat() {
        // Values generated with CPython 3.12:
        // datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().
        let cases: &[(i64, u32, &str)] = &[
            (0, 0, "1970-01-01T00:00:00.000000+00:00"),
            (1_760_000_000, 909_587, "2025-10-09T08:53:20.909587+00:00"),
            (951_782_400, 1, "2000-02-29T00:00:00.000001+00:00"),
            (1_760_849_999, 999_999, "2025-10-19T04:59:59.999999+00:00"),
        ];
        for (secs, micros, expected) in cases {
            assert_eq!(&iso_from_epoch(*secs, *micros), expected);
        }
    }

    #[test]
    fn mtime_double_matches_st_mtime_bits() {
        // CPython 3.12: float.hex(1760000000 + 123456789 / 1e9) — the same
        // sec + nsec/1e9 double os.stat computes for that stamp.
        let got = mtime_from_times(1_760_000_000, 123_456_789);
        let expected = f64::from_bits(0x41da_39de_0007_e6b7); // 0x1.a39de0007e6b7p+30
        assert_eq!(got.to_bits(), expected.to_bits());
    }
}
