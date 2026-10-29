//! Template environment: filters, globals, and loading from the embedded tree.

use minijinja::Environment;
use minijinja::value::{Kwargs, Value, ValueKind};

use super::assets;

/// Register the dashboard env on a fresh `Environment` (templates load separately).
pub fn configure(env: &mut Environment<'static>) {
    env.add_filter("filesize", filter_filesize);
    env.add_filter("epoch", filter_epoch);
    env.add_filter("isots", filter_isots);
    env.add_filter("duration", filter_duration);
    env.add_filter("tokens", filter_tokens);
    env.add_filter("span", filter_span);
    env.add_filter("offset", filter_offset);
    env.add_filter("mean", filter_mean);
    env.add_filter("merge", filter_merge);
    // Jinja2's default tojson policy sorts keys (json.dumps_kwargs); match it
    // so embedded JSON blobs (palette-data, graph-data) byte-match.
    env.add_filter("tojson", filter_tojson_sorted);
    env.add_function("url_for", global_url_for);
    env.add_global("asset_version", Value::from(assets::asset_version()));
    env.set_unknown_method_callback(|_state, value, method, args| {
        if value.kind() == ValueKind::Map {
            match method {
                "update" => {
                    let updates = args.first().cloned().unwrap_or(Value::from(()));
                    return Ok(filter_merge(value.clone(), updates));
                }
                "get" => {
                    // dict.get(key, default=None): a missing key reads None,
                    // so `is not none` checks behave like Jinja2's.
                    let key = args.first().map(Value::to_string).unwrap_or_default();
                    let default = args
                        .get(1)
                        .cloned()
                        .unwrap_or_else(|| Value::from(Option::<Value>::None));
                    return Ok(match value.get_attr(&key) {
                        Ok(found) if !found.is_undefined() => found,
                        _ => default,
                    });
                }
                "items" | "keys" | "values" => {
                    return Ok(dict_method(value.clone(), method));
                }
                _ => {}
            }
        }
        Err(minijinja::Error::new(
            minijinja::ErrorKind::UnknownMethod,
            format!("object has no method named {method}"),
        ))
    });
}

/// Load every embedded template into the env.
pub fn load_templates(env: &mut Environment<'static>) {
    for (name, body) in assets::ASSETS {
        let Some(rel) = name.strip_prefix("templates/") else {
            continue;
        };
        let Ok(source) = std::str::from_utf8(body) else {
            continue;
        };
        env.add_template(rel, source)
            .expect("embedded template compiles");
    }
}

/// One shared configured environment (templates are static; env is Send+Sync).
pub fn environment() -> Environment<'static> {
    let mut env = Environment::new();
    configure(&mut env);
    load_templates(&mut env);
    env
}

// --- Human-formatting filters (dashboard.app helpers port) ---

fn filter_filesize(value: Value) -> String {
    let mut size = f64_of(&value).unwrap_or(0.0).max(0.0);
    for unit in ["B", "KiB", "MiB", "GiB"] {
        if size < 1024.0 {
            return if unit == "B" {
                format!("{} {unit}", size as i64)
            } else {
                format!("{size:.1} {unit}")
            };
        }
        size /= 1024.0;
    }
    format!("{size:.1} TiB")
}

fn filter_epoch(value: Value) -> String {
    let Some(secs) = f64_of(&value) else {
        return "\u{2014}".into();
    };
    match human_epoch(secs as i64) {
        Some(text) => text,
        None => "\u{2014}".into(),
    }
}

fn filter_isots(value: Value) -> String {
    let Some(text) = value.as_str() else {
        return "\u{2014}".into();
    };
    match parse_iso_utc(text) {
        Some(secs) => human_epoch(secs).unwrap_or_else(|| "\u{2014}".into()),
        None => "\u{2014}".into(),
    }
}

fn filter_duration(value: Value) -> String {
    let Some(ms) = f64_of(&value) else {
        return "\u{2014}".into();
    };
    if ms < 1000.0 {
        format!("{ms:.0} ms")
    } else {
        format!("{:.1} s", ms / 1000.0)
    }
}

fn filter_tokens(row: Value) -> String {
    let is_blank = |v: &Value| v.is_undefined() || v.is_none();
    let req = row.get_attr("est_req_tokens").unwrap_or(Value::UNDEFINED);
    let resp = row.get_attr("est_resp_tokens").unwrap_or(Value::UNDEFINED);
    if is_blank(&req) && is_blank(&resp) {
        return "unknown".into();
    }
    let part = |v: &Value| {
        if is_blank(v) {
            "\u{2014}".to_string()
        } else {
            v.to_string()
        }
    };
    format!("~{} / ~{}", part(&req), part(&resp))
}

fn filter_span(value: Value) -> String {
    human_span(f64_of(&value).unwrap_or(0.0).max(0.0) as i64)
}

fn filter_offset(ts: Value, base: Value) -> String {
    let (Some(ts), Some(base)) = (f64_of(&ts), f64_of(&base)) else {
        return "\u{2014}".into();
    };
    format!("+{}", human_span((ts - base) as i64))
}

fn filter_mean(value: Value) -> String {
    let num = f64_of(&value).unwrap_or(0.0);
    if num == num.trunc() {
        format!("{}", num as i64)
    } else {
        format!("{num:.1}")
    }
}

/// Minijinja Value -> serde_json::Value without the serde trait bridge.
fn value_to_json(value: &Value) -> serde_json::Value {
    match value.kind() {
        ValueKind::None | ValueKind::Undefined => serde_json::Value::Null,
        ValueKind::Bool => serde_json::Value::Bool(value.is_true()),
        ValueKind::Number => {
            if let Some(int) = value.as_i64() {
                serde_json::Value::from(int)
            } else if let Ok(float) = value.to_string().trim().parse::<f64>() {
                serde_json::Value::from(float)
            } else {
                serde_json::Value::Null
            }
        }
        ValueKind::String => serde_json::Value::from(value.to_string()),
        ValueKind::Bytes => serde_json::Value::Null,
        ValueKind::Seq => {
            let items: Vec<serde_json::Value> = value
                .try_iter()
                .map(|iter| iter.map(|item| value_to_json(&item)).collect())
                .unwrap_or_default();
            serde_json::Value::Array(items)
        }
        ValueKind::Map => {
            let mut map = serde_json::Map::new();
            if let Ok(iter) = value.try_iter() {
                for key in iter {
                    let key_text = key.to_string();
                    let item = value.get_attr(&key_text).unwrap_or(Value::UNDEFINED);
                    map.insert(key_text, value_to_json(&item));
                }
            }
            serde_json::Value::Object(map)
        }
        _ => serde_json::Value::from(value.to_string()),
    }
}

/// Jinja2-policy tojson: sorted keys, HTML-safe escapes, Markup-safe output.
fn filter_tojson_sorted(value: Value) -> Value {
    fn sort_keys(value: serde_json::Value) -> serde_json::Value {
        match value {
            serde_json::Value::Object(map) => {
                let sorted: std::collections::BTreeMap<String, serde_json::Value> = map
                    .into_iter()
                    .map(|(key, inner)| (key, sort_keys(inner)))
                    .collect();
                serde_json::Value::Object(sorted.into_iter().collect())
            }
            serde_json::Value::Array(items) => {
                serde_json::Value::Array(items.into_iter().map(sort_keys).collect())
            }
            other => other,
        }
    }
    let sorted = sort_keys(value_to_json(&value));
    let mut out = String::new();
    write_json(&sorted, &mut out);
    // htmlsafe_json_dumps neutralizes </script>-breaking characters.
    Value::from_safe_string(
        out.replace('<', "\\u003c")
            .replace('>', "\\u003e")
            .replace('&', "\\u0026")
            .replace('\'', "\\u0027"),
    )
}

/// json.dumps defaults: `, ` and `: ` separators, compact containers.
fn write_json(value: &serde_json::Value, out: &mut String) {
    match value {
        serde_json::Value::Null => out.push_str("null"),
        serde_json::Value::Bool(flag) => out.push_str(if *flag { "true" } else { "false" }),
        serde_json::Value::Number(number) => out.push_str(&number.to_string()),
        serde_json::Value::String(text) => {
            out.push_str(&serde_json::to_string(text).unwrap_or_else(|_| "\"\"".into()));
        }
        serde_json::Value::Array(items) => {
            out.push('[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push_str(", ");
                }
                write_json(item, out);
            }
            out.push(']');
        }
        serde_json::Value::Object(map) => {
            out.push('{');
            for (index, (key, item)) in map.iter().enumerate() {
                if index > 0 {
                    out.push_str(", ");
                }
                out.push_str(&serde_json::to_string(key).unwrap_or_else(|_| "\"\"".into()));
                out.push_str(": ");
                write_json(item, out);
            }
            out.push('}');
        }
    }
}

/// Jinja2's `dict.items()`/`.keys()`/`.values()` as plain sequences.
fn dict_method(map: Value, method: &str) -> Value {
    let mut items: Vec<Value> = Vec::new();
    if let Ok(iter) = map.try_iter() {
        for key in iter {
            let val = map.get_attr(&key.to_string()).unwrap_or(Value::UNDEFINED);
            match method {
                "items" => items.push(Value::from(vec![key, val])),
                "keys" => items.push(key),
                _ => items.push(val),
            }
        }
    }
    Value::from(items)
}

/// Map merge for the minijinja-compat `q | merge({...})` / `q.update({...}`) idiom.
fn filter_merge(map: Value, updates: Value) -> Value {
    let mut out: Vec<(Value, Value)> = Vec::new();
    let collect = |source: &Value, out: &mut Vec<(Value, Value)>| {
        if let Ok(iter) = source.try_iter() {
            for key in iter {
                if let Ok(val) = source.get_attr(&key.to_string()) {
                    out.push((key, val));
                }
            }
        }
    };
    collect(&map, &mut out);
    collect(&updates, &mut out);
    Value::from(
        out.into_iter()
            .map(|(k, v)| (k.to_string(), v))
            .collect::<std::collections::BTreeMap<String, Value>>(),
    )
}

/// Starlette's `url_for('static', path=...)`; the only route templates name.
fn global_url_for(endpoint: String, kwargs: Kwargs) -> Value {
    if endpoint == "static"
        && let Ok(path) = kwargs.get::<String>("path")
    {
        return Value::from_safe_string(format!("/static{path}"));
    }
    Value::from_safe_string("/".to_string())
}

// --- shared formatting helpers ---

fn f64_of(value: &Value) -> Option<f64> {
    if value.is_none() || value.is_undefined() {
        return None;
    }
    if let Some(text) = value.as_str() {
        return text.trim().parse::<f64>().ok();
    }
    value.to_string().trim().parse::<f64>().ok()
}

/// Epoch seconds as a `%Y-%m-%d %H:%M:%S UTC` wall clock.
pub fn human_epoch(secs: i64) -> Option<String> {
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400);
    let (year, month, day) = civil_from_days(days)?;
    let (hour, minute, second) = (rem / 3600, (rem % 3600) / 60, rem % 60);
    Some(format!(
        "{year:04}-{month:02}-{day:02} {hour:02}:{minute:02}:{second:02} UTC"
    ))
}

/// Days since 1970-01-01 to (y, m, d) — Howard Hinnant's civil algorithm.
fn civil_from_days(days: i64) -> Option<(i64, u32, u32)> {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = if m <= 2 { y + 1 } else { y };
    Some((year, m as u32, d as u32))
}

/// `Ns` / `Nm` / `Nh Mm` span text (chain spans).
pub fn human_span(secs: i64) -> String {
    if secs < 60 {
        return format!("{secs}s");
    }
    if secs < 3600 {
        return format!("{}m", secs / 60);
    }
    format!("{}h {}m", secs / 3600, secs % 3600 / 60)
}

/// ISO-8601 timestamp to epoch seconds UTC; naive input reads as UTC.
pub fn parse_iso_utc(text: &str) -> Option<i64> {
    let text = text.trim();
    let bytes = text.as_bytes();
    if bytes.len() < 10 {
        return None;
    }
    let year: i64 = text.get(0..4)?.parse().ok()?;
    let month: i64 = text.get(5..7)?.parse().ok()?;
    let day: i64 = text.get(8..10)?.parse().ok()?;
    let days = days_from_civil(year, month, day)?;
    let mut secs = days * 86_400;
    let (rest, offset_secs) = split_offset(&text[10..])?;
    let time = rest.trim_start_matches(['T', ' ']);
    let (hms, frac_and_rest) = match time.split_once('.') {
        Some((hms, tail)) => (hms, tail),
        None => match time.split_once(['+', '-']) {
            Some((hms, _)) => (hms, ""),
            None => (time, ""),
        },
    };
    let mut parts = hms.split(':');
    let hour: i64 = parts.next().unwrap_or("0").parse().unwrap_or(0);
    let minute: i64 = parts.next().unwrap_or("0").parse().unwrap_or(0);
    let second: i64 = parts.next().unwrap_or("0").parse().unwrap_or(0);
    if !(0..=23).contains(&hour) || !(0..=59).contains(&minute) || !(0..=60).contains(&second) {
        return None;
    }
    secs += hour * 3600 + minute * 60 + second;
    let _ = frac_and_rest;
    Some(secs - offset_secs)
}

fn days_from_civil(year: i64, month: i64, day: i64) -> Option<i64> {
    if !(1..=12).contains(&month) || !(1..=31).contains(&day) {
        return None;
    }
    let y = if month <= 2 { year - 1 } else { year };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let mp = if month > 2 { month - 3 } else { month + 9 };
    let doy = (153 * mp + 2) / 5 + day - 1;
    Some(era * 146_097 + yoe * 365 + yoe / 4 - yoe / 100 + doy - 719_468)
}

/// Split a trailing timezone offset (`Z`, `+05:30`, `-08:00`); returns the
/// remainder plus the offset in seconds east of UTC.
fn split_offset(rest: &str) -> Option<(&str, i64)> {
    if let Some(stripped) = rest.strip_suffix('Z') {
        return Some((stripped, 0));
    }
    if rest.is_empty() {
        return Some((rest, 0));
    }
    if let Some(position) = rest.rfind(['+', '-']) {
        let (body, offset) = rest.split_at(position);
        let sign = if offset.starts_with('-') { -1 } else { 1 };
        let digits: String = offset[1..].chars().filter(|c| c.is_ascii_digit()).collect();
        let (hours, minutes) = match digits.len() {
            4 => (
                digits[0..2].parse::<i64>().ok()?,
                digits[2..4].parse::<i64>().ok()?,
            ),
            2 => (digits.parse::<i64>().ok()?, 0),
            _ => return Some((body, 0)),
        };
        return Some((body, sign * (hours * 3600 + minutes * 60)));
    }
    Some((rest, 0))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ctx(value: serde_json::Value) -> Value {
        Value::from(minijinja::value::Serde(value))
    }

    #[test]
    fn filters_match_the_formats() {
        assert_eq!(filter_filesize(Value::from(0)), "0 B");
        assert_eq!(filter_filesize(Value::from(2048)), "2.0 KiB");
        assert_eq!(filter_filesize(Value::from(3 * 1024 * 1024)), "3.0 MiB");
        assert_eq!(
            filter_filesize(Value::from(5.5 * 1024_f64.powi(3))),
            "5.5 GiB"
        );
        assert_eq!(
            filter_filesize(Value::from(2.5 * 1024_f64.powi(4))),
            "2.5 TiB"
        );
        assert_eq!(filter_epoch(Value::from(0)), "1970-01-01 00:00:00 UTC");
        assert_eq!(
            filter_epoch(Value::from(1_760_000_000)),
            "2025-10-09 08:53:20 UTC"
        );
        assert_eq!(filter_epoch(Value::from("not-a-number")), "\u{2014}");
        assert_eq!(filter_duration(Value::from(850)), "850 ms");
        assert_eq!(filter_duration(Value::from(1250)), "1.2 s");
        assert_eq!(
            filter_duration(Value::from(Option::<i64>::None)),
            "\u{2014}"
        );
        assert_eq!(filter_span(Value::from(45)), "45s");
        assert_eq!(filter_span(Value::from(125)), "2m");
        assert_eq!(filter_span(Value::from(7265)), "2h 1m");
        assert_eq!(filter_mean(Value::from(12.0)), "12");
        assert_eq!(filter_mean(Value::from(12.34)), "12.3");
    }

    #[test]
    fn isots_normalizes_offsets_to_utc() {
        assert_eq!(
            filter_isots(Value::from("2026-10-19T12:34:56Z")),
            "2026-10-19 12:34:56 UTC"
        );
        assert_eq!(
            filter_isots(Value::from("2026-10-19T14:34:56+02:00")),
            "2026-10-19 12:34:56 UTC"
        );
        assert_eq!(
            filter_isots(Value::from("2026-10-19T07:04:56-06:00")),
            "2026-10-19 13:04:56 UTC"
        );
        assert_eq!(filter_isots(Value::from("garbage")), "\u{2014}");
        assert_eq!(filter_isots(Value::from(Option::<&str>::None)), "\u{2014}");
    }

    #[test]
    fn tokens_filter_renders_unknown_and_pairs() {
        let row = ctx(serde_json::json!({"est_req_tokens": 12, "est_resp_tokens": 34}));
        assert_eq!(filter_tokens(row), "~12 / ~34");
        let row = ctx(serde_json::json!({"est_req_tokens": null, "est_resp_tokens": null}));
        assert_eq!(filter_tokens(row), "unknown");
        let row = ctx(serde_json::json!({"est_req_tokens": null, "est_resp_tokens": 9}));
        assert_eq!(filter_tokens(row), "~\u{2014} / ~9");
    }

    #[test]
    fn url_for_resolves_the_static_mount() {
        let mut env = Environment::new();
        configure(&mut env);
        env.add_template("t.html", "{{ url_for('static', path='/app.css') }}")
            .unwrap();
        assert_eq!(
            env.get_template("t.html").unwrap().render(()).unwrap(),
            "/static/app.css"
        );
    }

    #[test]
    fn merge_filter_joins_maps_and_sets_leak_through_if() {
        let mut env = Environment::new();
        configure(&mut env);
        env.add_template(
            "t.html",
            "{% set q = {'a': '1'} %}{% set q = q | merge({'b': '2'}) %}{{ q.a }},{{ q.b }}",
        )
        .unwrap();
        assert_eq!(
            env.get_template("t.html").unwrap().render(()).unwrap(),
            "1,2"
        );
        // The window_control shape: merge assignment inside an if block.
        env.add_template(
            "u.html",
            "{% set q = {'a': '1'} %}{% if true %}{% set q = q | merge({'c': '3'}) %}{% endif %}{{ q.a }},{{ q.c }}",
        )
        .unwrap();
        assert_eq!(
            env.get_template("u.html").unwrap().render(()).unwrap(),
            "1,3"
        );
    }

    #[test]
    fn tojson_sorts_keys_and_escapes_html() {
        let mut env = Environment::new();
        configure(&mut env);
        env.add_template("t.html", "{{ value | tojson }}").unwrap();
        let value = serde_json::json!({"b": 1, "a": {"d": "<x>", "c": 2}});
        let out = env
            .get_template("t.html")
            .unwrap()
            .render(minijinja::value::Serde(
                &serde_json::json!({"value": value}),
            ))
            .unwrap();
        eprintln!("rendered: {out}");
        assert_eq!(out, r#"{"a": {"c": 2, "d": "\u003cx\u003e"}, "b": 1}"#);
    }

    #[test]
    fn every_embedded_template_compiles() {
        let environment = environment();
        let templates = assets::ASSETS
            .iter()
            .filter(|(name, _)| name.starts_with("templates/"))
            .count();
        assert_eq!(environment.templates().count(), templates);
    }
}
