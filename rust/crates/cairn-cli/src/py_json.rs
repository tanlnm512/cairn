//! `json.dumps(obj, indent=2)` byte parity (ensure_ascii + key insertion order).

use serde_json::Value;
use yaml_rust2::Yaml;

/// YAML value -> JSON with Python `yaml.safe_load`'s scalar typing and
/// insertion-ordered hash keys (serde_json's preserve_order map).
pub fn yaml_to_value(v: &Yaml) -> Value {
    match v {
        Yaml::Array(items) => Value::Array(items.iter().map(yaml_to_value).collect()),
        Yaml::Hash(h) => {
            let mut map = serde_json::Map::new();
            for (key, val) in h {
                let key = match key {
                    Yaml::String(s) => s.clone(),
                    Yaml::Integer(i) => i.to_string(),
                    Yaml::Real(s) => s.clone(),
                    Yaml::Boolean(b) => b.to_string(),
                    _ => String::new(),
                };
                map.insert(key, yaml_to_value(val));
            }
            Value::Object(map)
        }
        Yaml::Boolean(b) => Value::Bool(*b),
        Yaml::Integer(i) => Value::Number((*i).into()),
        Yaml::Real(s) => {
            let f = s.parse::<f64>().unwrap_or(f64::NAN);
            serde_json::Number::from_f64(f).map_or(Value::Null, Value::Number)
        }
        Yaml::String(s) => Value::String(s.clone()),
        Yaml::Null | Yaml::BadValue => Value::Null,
        _ => Value::Null,
    }
}

/// Serialize like python `json.dumps(value, indent=2)`: two-space pretty
/// printing, insertion-ordered keys, and `ensure_ascii` string escaping.
pub fn dumps(value: &Value) -> String {
    let mut out = String::new();
    write_value(&mut out, value, Some(2), 0);
    out
}

/// Serialize like python `json.dumps(value)` (compact, `", "` / `": "` separators).
pub fn dumps_compact(value: &Value) -> String {
    let mut out = String::new();
    write_value(&mut out, value, None, 0);
    out
}

/// Serialize like python `json.dumps(value, indent=2, sort_keys=True)`.
pub fn dumps_sorted(value: &Value) -> String {
    let mut out = String::new();
    write_sorted(&mut out, value, 0);
    out
}

fn write_sorted(out: &mut String, value: &Value, level: usize) {
    match value {
        Value::Object(map) => {
            if map.is_empty() {
                out.push_str("{}");
                return;
            }
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            out.push('{');
            for (i, key) in keys.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                out.push('\n');
                out.push_str(&"  ".repeat(level + 1));
                write_string(out, key);
                out.push_str(": ");
                write_sorted(out, &map[*key], level + 1);
            }
            out.push('\n');
            out.push_str(&"  ".repeat(level));
            out.push('}');
        }
        Value::Array(items) => {
            if items.is_empty() {
                out.push_str("[]");
                return;
            }
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                out.push('\n');
                out.push_str(&"  ".repeat(level + 1));
                write_sorted(out, item, level + 1);
            }
            out.push('\n');
            out.push_str(&"  ".repeat(level));
            out.push(']');
        }
        Value::String(s) => write_string(out, s),
        other => out.push_str(&serde_json::to_string(other).unwrap_or_else(|_| "null".into())),
    }
}

fn write_value(out: &mut String, value: &Value, indent: Option<usize>, level: usize) {
    match value {
        Value::Object(map) => {
            if map.is_empty() {
                out.push_str("{}");
                return;
            }
            out.push('{');
            for (i, (key, item)) in map.iter().enumerate() {
                if i > 0 {
                    if indent.is_none() {
                        out.push_str(", ");
                    } else {
                        out.push(',');
                    }
                }
                newline_indent(out, indent, level + 1);
                write_string(out, key);
                out.push_str(": ");
                write_value(out, item, indent, level + 1);
            }
            newline_indent(out, indent, level);
            out.push('}');
        }
        Value::Array(items) => {
            if items.is_empty() {
                out.push_str("[]");
                return;
            }
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    if indent.is_none() {
                        out.push_str(", ");
                    } else {
                        out.push(',');
                    }
                }
                newline_indent(out, indent, level + 1);
                write_value(out, item, indent, level + 1);
            }
            newline_indent(out, indent, level);
            out.push(']');
        }
        Value::String(s) => write_string(out, s),
        other => out.push_str(&serde_json::to_string(other).unwrap_or_else(|_| "null".into())),
    }
}

fn newline_indent(out: &mut String, indent: Option<usize>, level: usize) {
    if let Some(width) = indent {
        out.push('\n');
        out.push_str(&" ".repeat(width * level));
    }
}

/// Python `ensure_ascii` string literal: control chars short-escaped, everything
/// non-ASCII as `\uXXXX` with surrogate pairs past the BMP.
fn write_string(out: &mut String, text: &str) {
    out.push('"');
    for unit in text.encode_utf16() {
        match unit {
            0x22 => out.push_str("\\\""),
            0x5C => out.push_str("\\\\"),
            0x08 => out.push_str("\\b"),
            0x0C => out.push_str("\\f"),
            0x0A => out.push_str("\\n"),
            0x0D => out.push_str("\\r"),
            0x09 => out.push_str("\\t"),
            0x20..=0x7E => out.push(char::from_u32(unit as u32).expect("printable")),
            _ => out.push_str(&format!("\\u{unit:04x}")),
        }
    }
    out.push('"');
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn matches_python_indent_two_shapes() {
        let value = json!({
            "b": 1,
            "a": [1, "x"],
            "nested": {},
            "empty_list": [],
            "none": Value::Null,
        });
        assert_eq!(
            dumps(&value),
            "{\n  \"b\": 1,\n  \"a\": [\n    1,\n    \"x\"\n  ],\n  \"nested\": {},\n  \"empty_list\": [],\n  \"none\": null\n}"
        );
    }

    #[test]
    fn escapes_non_ascii_like_python() {
        // python: json.dumps({"k": "é☃\U0001F600"}, indent=2)
        assert_eq!(
            dumps(&json!({"k": "é\u{2603}\u{1F600}"})),
            "{\n  \"k\": \"\\u00e9\\u2603\\ud83d\\ude00\"\n}"
        );
    }

    #[test]
    fn yaml_scalars_type_like_safe_load() {
        let docs = yaml_rust2::YamlLoader::load_from_str(
            "active: true\ncount: 20\nscore: 0.578\nname: x\nempty:\n",
        )
        .expect("yaml");
        let value = yaml_to_value(&docs[0]);
        assert_eq!(
            value,
            serde_json::json!({
                "active": true,
                "count": 20,
                "score": 0.578,
                "name": "x",
                "empty": Value::Null,
            })
        );
        assert_eq!(dumps(value.get("score").expect("score")), "0.578");
    }
}
