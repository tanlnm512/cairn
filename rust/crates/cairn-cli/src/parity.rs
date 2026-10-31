//! Shared embedding-parity numerics: stored-blob decode and cosine for doctor
//! and the dashboard settings view.

/// Little-endian f32 vector from a stored embedding blob.
pub(crate) fn decode_f32(blob: &[u8]) -> Vec<f32> {
    blob.as_chunks::<4>()
        .0
        .iter()
        .map(|c| f32::from_le_bytes(*c))
        .collect()
}

/// Cosine similarity accumulated in f64.
pub(crate) fn cosine(left: &[f32], right: &[f32]) -> f64 {
    let mut dot = 0.0f64;
    let mut nl = 0.0f64;
    let mut nr = 0.0f64;
    for (a, b) in left.iter().zip(right) {
        dot += f64::from(*a) * f64::from(*b);
        nl += f64::from(*a) * f64::from(*a);
        nr += f64::from(*b) * f64::from(*b);
    }
    if nl == 0.0 || nr == 0.0 {
        return 0.0;
    }
    dot / (nl.sqrt() * nr.sqrt())
}

/// Mean stored-vs-served cosine over zipped samples; Err carries the
/// dim-mismatch reason both parity surfaces report.
pub(crate) fn mean_cosine(stored: &[Vec<f32>], served: &[Vec<f32>]) -> Result<f64, String> {
    let mut cosines = Vec::with_capacity(stored.len());
    for (stored, served) in stored.iter().zip(served) {
        if served.len() != stored.len() {
            return Err(format!(
                "dim_mismatch stored={} server={}",
                stored.len(),
                served.len()
            ));
        }
        cosines.push(cosine(stored, served));
    }
    Ok(cosines.iter().sum::<f64>() / cosines.len() as f64)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cosine_is_one_for_identical_unit_vectors_and_zero_for_orthogonal() {
        let v = vec![0.5f32, 0.5, 0.5, 0.5];
        assert!((cosine(&v, &v) - 1.0).abs() < 1e-9);
        let o = vec![1.0f32, 0.0, 0.0, 0.0];
        let p = vec![0.0f32, 1.0, 0.0, 0.0];
        assert!(cosine(&o, &p).abs() < 1e-9);
        assert_eq!(cosine(&o, &[0.0, 0.0, 0.0, 0.0]), 0.0);
    }

    #[test]
    fn mean_cosine_surfaces_dimension_mismatches() {
        let err = mean_cosine(&[vec![1.0, 2.0]], &[vec![1.0]]).expect_err("dim mismatch");
        assert_eq!(err, "dim_mismatch stored=2 server=1");
    }
}
