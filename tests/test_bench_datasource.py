"""Tests for the datasource manifest helpers (src/cairn/bench/datasource.py).

Three layers:
1. tree_hash unit tests -- the load-bearing property is path-order
   independence (same files created in different orders under two roots
   hash identically), plus the frozen byte format, mode normalization, and
   the constant .git-marker rule.
2. Integration with generate_corpus -- the real substrate FR-001 pins.
3. Manifest load/save/validate -- round-trip, byte-stable saves, and the
   missing-required-key contract.
"""
from __future__ import annotations

import hashlib
import shutil

import pytest

from cairn.bench import generate_corpus
from cairn.bench.datasource import (
    MANIFEST_SCHEMA,
    MANIFEST_VERSION,
    REQUIRED_ENTRY_KEYS,
    REQUIRED_MANIFEST_KEYS,
    REQUIRED_T1_KEYS,
    REQUIRED_T3_ENTRY_KEYS,
    load_manifest,
    save_manifest,
    tree_hash,
    validate_manifest,
)


def _write_tree(root, files: dict[str, bytes]):
    """Write {relpath: content} under root (mkdir -p for parents)."""
    for relpath, content in files.items():
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return root


# --- tree_hash ------------------------------------------------------------

class TestTreeHash:
    def test_path_order_independence(self, tmp_path):
        """Same file set built under different creation orders -> same digest.

        This is THE property the CI regenerate-and-assert check exists on
        (FR-001/AC2): os.listdir order varies by filesystem, so the digest
        must be a function of the sorted manifest only.
        """
        files = {
            "module_0003.py": b"c = 3\n",
            "module_0001.py": b"a = 1\n",
            "pkg/module_0002.py": b"b = 2\n",
            "pkg/deep/module_0000.py": b"z = 0\n",
            "pkg/__init__.py": b"",
        }
        a = _write_tree(tmp_path / "a", files)
        b = _write_tree(tmp_path / "b", {k: files[k] for k in sorted(files, reverse=True)})
        assert tree_hash(a) == tree_hash(b)

    def test_empty_root_is_empty_sha256(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        # An empty corpus is a legitimate pinned state, not an error.
        assert tree_hash(empty) == hashlib.sha256(b"").hexdigest()

    def test_nested_paths_use_posix_relative_paths(self, tmp_path):
        root = _write_tree(tmp_path, {"a/b/c/d.py": b"x = 1\n"})
        only_deep = _write_tree(tmp_path / "flat", {"a-b-c-d.py": b"x = 1\n"})
        # Same content, different (nested vs flat) path -> different entry
        # -> different digest: the relpath is load-bearing, not decorative.
        assert tree_hash(root) != tree_hash(only_deep)

    def test_digest_byte_format_is_frozen(self, tmp_path):
        """Pin the exact entry bytes so the format can never drift silently.

        Mirrors the documented format: "<mode> <relpath>\0<sha256(content)>"
        entries, sorted, no separators, fed into one running sha256.
        """
        root = tmp_path / "tree"
        root.mkdir()
        (root / "a.py").write_bytes(b"alpha\n")
        deep = root / "sub" / "b.py"
        deep.parent.mkdir()
        deep.write_bytes(b"beta\n")
        deep.chmod(0o755)  # exec bit -> normalized mode 755
        # Content digests hoisted out of the f-strings: Python 3.11 forbids
        # backslashes inside f-string expressions (b"alpha\n" would be one).
        alpha_sha = hashlib.sha256(b"alpha\n").hexdigest()
        beta_sha = hashlib.sha256(b"beta\n").hexdigest()
        expected = hashlib.sha256()
        expected.update(f"644 a.py\0{alpha_sha}".encode("utf-8"))
        expected.update(f"755 sub/b.py\0{beta_sha}".encode("utf-8"))
        assert tree_hash(root) == expected.hexdigest()

    def test_content_change_changes_digest(self, tmp_path):
        a = _write_tree(tmp_path / "a", {"m.py": b"x = 1\n"})
        b = _write_tree(tmp_path / "b", {"m.py": b"x = 2\n"})
        assert tree_hash(a) != tree_hash(b)

    def test_rename_changes_digest(self, tmp_path):
        a = _write_tree(tmp_path / "a", {"one.py": b"x = 1\n"})
        b = _write_tree(tmp_path / "b", {"two.py": b"x = 1\n"})
        assert tree_hash(a) != tree_hash(b)

    def test_exec_bit_changes_digest(self, tmp_path):
        a = _write_tree(tmp_path / "a", {"run.py": b"print('hi')\n"})
        b = _write_tree(tmp_path / "b", {"run.py": b"print('hi')\n"})
        (b / "run.py").chmod(0o755)
        assert tree_hash(a) != tree_hash(b)

    def test_umask_noise_below_exec_bit_is_ignored(self, tmp_path):
        """0600 vs 0644 (no exec bit either way) -> identical digest.

        WHY the mode is git-normalized instead of raw st_mode & 0o777: a
        runner's umask decides 0600 vs 0644 for the *same* generated
        content; leaking that into the digest would break the
        byte-identical ubuntu/macOS equality the design exists for.
        """
        a = _write_tree(tmp_path / "a", {"m.py": b"x = 1\n"})
        b = _write_tree(tmp_path / "b", {"m.py": b"x = 1\n"})
        (a / "m.py").chmod(0o600)
        (b / "m.py").chmod(0o644)
        assert tree_hash(a) == tree_hash(b)

    def test_empty_git_marker_never_affects_digest(self, tmp_path):
        """corpus.py's scanner marker is an empty .git DIRECTORY; directories
        contribute no entries, so its presence is invisible -- the constant
        rule, under both values of include_git_dir_marker."""
        files = {"m.py": b"x = 1\n"}
        with_marker = _write_tree(tmp_path / "with", files)
        (with_marker / ".git").mkdir()  # exactly what generate_corpus does
        without = _write_tree(tmp_path / "without", files)
        assert tree_hash(with_marker) == tree_hash(without)
        assert tree_hash(with_marker, include_git_dir_marker=True) == tree_hash(without)

    def test_git_dir_contents_excluded_by_default(self, tmp_path):
        """A REAL .git dir (files inside) is machine noise: excluded by
        default, included only when explicitly asked."""
        files = {"code.py": b"x = 1\n"}
        checkout = _write_tree(tmp_path / "checkout", files)
        (checkout / ".git").mkdir()
        (checkout / ".git" / "index").write_bytes(b"git index: mtimes live here")
        clean = _write_tree(tmp_path / "clean", files)
        assert tree_hash(checkout) == tree_hash(clean)  # default: excluded
        assert tree_hash(checkout, include_git_dir_marker=True) != tree_hash(clean)

    def test_missing_root_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            tree_hash(tmp_path / "nope")


# --- integration with the real corpus generator ---------------------------

class TestGeneratedCorpus:
    def test_same_params_same_hash_across_roots(self, tmp_path):
        """The substrate FR-001 pins: two regenerations of the same corpus
        recipe hash identically -- what T003's CI assert will rely on."""
        a = generate_corpus(tmp_path / "a", 6, complexity="low")
        b = generate_corpus(tmp_path / "b", 6, complexity="low")
        assert tree_hash(a) == tree_hash(b)

    def test_removing_git_marker_leaves_digest_unchanged(self, tmp_path):
        """The constant marker rule against real generator output: the empty
        .git dir carries no bytes, so deleting it changes nothing."""
        repo = generate_corpus(tmp_path, 6, complexity="low")
        before = tree_hash(repo)
        shutil.rmtree(repo / ".git")
        assert tree_hash(repo) == before


# --- manifest load/save ----------------------------------------------------

def _valid_manifest() -> dict:
    """A minimal manifest satisfying every documented contract."""
    return {
        "schema": MANIFEST_SCHEMA,
        "version": MANIFEST_VERSION,
        "t1": {
            "generator_git_sha": "1" * 40,
            "seed": 0xC0DE,
            "sizes": [60],
            "complexity": "medium",
            "entries": {
                "60": {
                    "tree_hash": "b" * 64,
                    "counts": {"files": 61, "lines": 900, "bytes": 21000},
                }
            },
        },
    }


class TestManifestIO:
    def test_round_trip_save_load(self, tmp_path):
        path = tmp_path / "manifest.json"
        original = _valid_manifest()
        save_manifest(path, original)
        assert load_manifest(path) == original

    def test_save_is_byte_stable(self, tmp_path):
        """Sorted-key serialization: same dict saved twice (even built with
        different insertion orders) -> identical bytes. The manifest is a
        committed artifact CI regenerates and diffs, so serialization must
        be a pure function of the content."""
        first, second = tmp_path / "a.json", tmp_path / "b.json"
        m1 = _valid_manifest()
        m2 = {k: m1[k] for k in reversed(list(m1))}  # same data, new order
        m2["t1"] = {k: m1["t1"][k] for k in reversed(list(m1["t1"]))}
        save_manifest(first, m1)
        save_manifest(second, m2)
        assert first.read_bytes() == second.read_bytes()

    def test_load_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_manifest(tmp_path / "absent.json")

    def test_load_invalid_json_raises_value_error(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError, match="not valid JSON"):
            load_manifest(path)


# --- manifest validation ---------------------------------------------------

class TestValidateManifest:
    def test_valid_manifest_has_no_errors(self):
        assert validate_manifest(_valid_manifest()) == []

    @pytest.mark.parametrize("key", REQUIRED_MANIFEST_KEYS)
    def test_missing_top_level_key(self, key):
        manifest = _valid_manifest()
        del manifest[key]
        errors = validate_manifest(manifest)
        assert any(f"missing required key '{key}'" in e for e in errors)

    @pytest.mark.parametrize("key", REQUIRED_T1_KEYS)
    def test_missing_t1_key(self, key):
        manifest = _valid_manifest()
        del manifest["t1"][key]
        errors = validate_manifest(manifest)
        assert any(f"t1: missing required key '{key}'" in e for e in errors)

    @pytest.mark.parametrize("key", REQUIRED_ENTRY_KEYS)
    def test_missing_entry_key(self, key):
        manifest = _valid_manifest()
        del manifest["t1"]["entries"]["60"][key]
        errors = validate_manifest(manifest)
        assert any(f"t1.entries.60: missing required key '{key}'" in e for e in errors)

    def test_missing_count_key(self):
        manifest = _valid_manifest()
        del manifest["t1"]["entries"]["60"]["counts"]["bytes"]
        errors = validate_manifest(manifest)
        assert any("counts: missing required key 'bytes'" in e for e in errors)

    def test_declared_size_without_entry(self):
        manifest = _valid_manifest()
        manifest["t1"]["sizes"] = [60, 200]
        errors = validate_manifest(manifest)
        assert any("declared size 200 has no entry" in e for e in errors)

    def test_entry_without_declared_size(self):
        manifest = _valid_manifest()
        manifest["t1"]["entries"]["500"] = {
            "tree_hash": "c" * 64,
            "counts": {"files": 1, "lines": 1, "bytes": 1},
        }
        errors = validate_manifest(manifest)
        assert any("matches no declared size" in e for e in errors)

    def test_bad_complexity_rejected(self):
        manifest = _valid_manifest()
        manifest["t1"]["complexity"] = "extreme"
        assert any("t1.complexity" in e for e in validate_manifest(manifest))

    def test_bad_seed_type_rejected(self):
        manifest = _valid_manifest()
        manifest["t1"]["seed"] = "0xC0DE"  # string, not int
        assert any("t1.seed" in e for e in validate_manifest(manifest))

    def test_bad_generator_sha_rejected(self):
        manifest = _valid_manifest()
        manifest["t1"]["generator_git_sha"] = "not-hex"
        assert any("generator_git_sha" in e for e in validate_manifest(manifest))

    def test_bad_tree_hash_rejected(self):
        manifest = _valid_manifest()
        manifest["t1"]["entries"]["60"]["tree_hash"] = "X" * 64
        assert any("tree_hash" in e for e in validate_manifest(manifest))

    def test_wrong_schema_tag_rejected(self):
        manifest = _valid_manifest()
        manifest["schema"] = "something-else"
        assert any("schema" in e for e in validate_manifest(manifest))

    def test_non_dict_rejected(self):
        assert validate_manifest(["not", "a", "manifest"]) != []

    def test_unknown_sections_are_ignored(self):
        """Forward compatibility: a section the validator does not know (say a
        future ``t4``) extends the schema without invalidating manifests this
        validator accepted. (``t3`` itself became a known, validated section
        in T019 -- see TestValidateManifestT3.)"""
        manifest = _valid_manifest()
        manifest["t4"] = [{"name": "big", "url": "https://x", "commit": "0" * 40}]
        assert validate_manifest(manifest) == []


# --- manifest validation: optional t3 pin section (T019, FR-006/TC-029) ----


def _valid_t3() -> dict:
    """A t3 section satisfying the documented pin contract."""
    return {
        "entries": [
            {
                "name": "home-assistant/core",
                "url": "https://github.com/home-assistant/core",
                "commit": "0" * 40,
                "scale_hint": "~27k files, Python",
            }
        ]
    }


class TestValidateManifestT3:
    def test_valid_t3_section_has_no_errors(self):
        manifest = _valid_manifest()
        manifest["t3"] = _valid_t3()
        assert validate_manifest(manifest) == []

    def test_absent_t3_still_valid(self):
        """The section is optional by design: DS-v1 manifests predate it and
        a T3 addition must not invalidate DS-v1 (D-010)."""
        assert "t3" not in _valid_manifest()
        assert validate_manifest(_valid_manifest()) == []

    @pytest.mark.parametrize("key", REQUIRED_T3_ENTRY_KEYS)
    def test_missing_t3_entry_key_rejected(self, key):
        manifest = _valid_manifest()
        t3 = _valid_t3()
        del t3["entries"][0][key]
        manifest["t3"] = t3
        errors = validate_manifest(manifest)
        assert any(f"t3.entries[0]: missing required key '{key}'" in e for e in errors)

    def test_t3_must_be_an_object(self):
        manifest = _valid_manifest()
        manifest["t3"] = [{"name": "big"}]  # a bare list, not {"entries": [...]}
        assert any("t3: expected a JSON object" in e for e in validate_manifest(manifest))

    def test_t3_entries_must_be_a_non_empty_list(self):
        manifest = _valid_manifest()
        manifest["t3"] = {"entries": []}
        assert any("t3.entries: expected a non-empty list" in e for e in validate_manifest(manifest))

    def test_t3_missing_entries_key_rejected(self):
        manifest = _valid_manifest()
        manifest["t3"] = {"pins": []}
        assert any("t3: missing required key 'entries'" in e for e in validate_manifest(manifest))

    def test_bad_t3_commit_rejected(self):
        manifest = _valid_manifest()
        manifest["t3"] = _valid_t3()
        manifest["t3"]["entries"][0]["commit"] = "not-hex"
        assert any("t3.entries[0].commit" in e for e in validate_manifest(manifest))

    def test_non_string_t3_field_rejected(self):
        manifest = _valid_manifest()
        manifest["t3"] = _valid_t3()
        manifest["t3"]["entries"][0]["scale_hint"] = 27000
        errors = validate_manifest(manifest)
        assert any("t3.entries[0].scale_hint: expected a string" in e for e in errors)

    def test_second_entry_validated_too(self):
        """Every entry is checked, not just the first -- the manifest's real
        t3 section pins two scale points (TC-029)."""
        manifest = _valid_manifest()
        t3 = _valid_t3()
        t3["entries"].append(
            {
                "name": "torvalds/linux",
                "url": "https://github.com/torvalds/linux",
                "commit": "1" * 40,
                "scale_hint": "~70k files, C",
            }
        )
        manifest["t3"] = t3
        assert validate_manifest(manifest) == []
        del t3["entries"][1]["url"]
        assert any("t3.entries[1]: missing required key 'url'" in e
                   for e in validate_manifest(manifest))
