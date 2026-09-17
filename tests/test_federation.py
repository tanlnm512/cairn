"""Federated search core over fixture multi-store registries.

The sandbox mirrors tests/conftest.py's ``_hermetic_env`` so the suite is
hermetic under runners that do not load pytest fixtures: paths.py's
import-time layout attributes are re-pointed for the duration of each test,
so registry writes and store opens land in the sandbox, never the real
``~/.cairn``.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path

ALPHA_SRC = (
    "class Authenticator:\n"
    "    def authenticate(self, token):\n"
    "        return True\n"
    "\n"
    "\n"
    "def login(username, password):\n"
    "    return Authenticator().authenticate(password)\n"
)

BETA_SRC = (
    "class SessionManager:\n"
    "    def authenticate(self, session):\n"
    "        return True\n"
    "\n"
    "\n"
    "def refresh_session(session_id):\n"
    "    return SessionManager().authenticate(session_id)\n"
)

GAMMA_SRC = (
    "class TokenValidator:\n"
    "    def authenticate(self, claims):\n"
    "        return True\n"
)


class FederationStoreCase(unittest.TestCase):
    """Sandbox and fixture-store helpers shared by the federation suites."""

    # paths.py's import-time stores-layout bindings, per the hermetic
    # fixture in tests/conftest.py.
    _SAVED_ATTRS = ("CAIRN_HOME", "REGISTRY_FILE", "CONFIG_FILE", "SHARED_LIB")
    _tmp: str
    _cairn_home: Path

    @classmethod
    def _enter_sandbox(cls):
        """Re-point env and paths.py's layout attributes at the sandbox."""
        saved_env = {
            k: v for k, v in os.environ.items() if k.startswith("CAIRN_")
        }
        for var in [v for v in os.environ if v.startswith("CAIRN_")]:
            del os.environ[var]
        os.environ["CAIRN_HOME"] = str(cls._cairn_home)
        os.environ["CAIRN_EMBED_BACKEND"] = "hash"
        # The CLI recorder's metrics flusher drains at process exit and
        # resolves a store when it does; the kill switch keeps it inert.
        os.environ["CAIRN_TELEMETRY"] = "off"
        from cairn import paths
        from cairn.graph import embeddings as emb

        saved_attrs = {name: getattr(paths, name) for name in cls._SAVED_ATTRS}
        paths.CAIRN_HOME = cls._cairn_home
        paths.REGISTRY_FILE = cls._cairn_home / "workspaces.json"
        paths.CONFIG_FILE = cls._cairn_home / "config.json"
        paths.SHARED_LIB = cls._cairn_home / "lib"
        paths.reset_config_cache()
        emb.reset_backend_cache()
        return (saved_env, saved_attrs)

    @classmethod
    def _exit_sandbox(cls, token):
        saved_env, saved_attrs = token
        from cairn import paths
        from cairn.graph import embeddings as emb

        for name, value in saved_attrs.items():
            setattr(paths, name, value)
        for var in [v for v in os.environ if v.startswith("CAIRN_")]:
            del os.environ[var]
        os.environ.update(saved_env)
        paths.reset_config_cache()
        emb.reset_backend_cache()

    @classmethod
    def _make_store(cls, repo_name, filename, source):
        from cairn import paths
        from cairn.graph import embeddings as emb
        from cairn.graph.builder import build_graph
        from cairn.graph.schema import get_db

        ws = Path(cls._tmp) / f"ws-{repo_name}" / repo_name
        (ws / ".git").mkdir(parents=True)
        (ws / filename).write_text(source)
        store = paths.register_workspace(ws)
        build_graph(workspace=str(ws), db_path=str(store.db))
        conn = get_db(str(store.db))
        emb.embed_all(conn)
        conn.close()
        return store

    @contextlib.contextmanager
    def _counted_embeds(self):
        """Count ``embeddings.embed_query`` calls inside the block."""
        from cairn.graph import embeddings as emb

        calls: list = []
        original = emb.embed_query

        def counting(text):
            calls.append(text)
            return original(text)

        emb.embed_query = counting
        try:
            yield calls
        finally:
            emb.embed_query = original

    @contextlib.contextmanager
    def _counted_registry_iters(self):
        """Count ``federation.iter_stores`` calls inside the block."""
        from cairn.graph import federation

        calls: list = []
        original = federation.iter_stores

        def counting(*args, **kwargs):
            calls.append(args)
            return original(*args, **kwargs)

        federation.iter_stores = counting
        try:
            yield calls
        finally:
            federation.iter_stores = original

    @contextlib.contextmanager
    def _silence_logging(self):
        """Silence all logging so CLI stdout stays parseable."""
        import logging

        logging.disable(logging.CRITICAL)
        try:
            yield
        finally:
            logging.disable(logging.NOTSET)


class FederatedSearchTests(FederationStoreCase):
    """Merged attributed results across the registered fixture stores."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="cairn-federation-")
        cls._cairn_home = Path(cls._tmp) / "_cairn_home"
        token = cls._enter_sandbox()
        try:
            cls._build_stores()
        finally:
            cls._exit_sandbox(token)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def setUp(self):
        self._token = self._enter_sandbox()

    def tearDown(self):
        self._exit_sandbox(self._token)

    @classmethod
    def _build_stores(cls):
        from cairn.graph.schema import get_db

        cls.store_a = cls._make_store("alpha", "auth.py", ALPHA_SRC)
        cls.store_b = cls._make_store("beta", "session.py", BETA_SRC)
        # beta stays lexical-only: no embedding rows for any model.
        conn = get_db(str(cls.store_b.db))
        conn.execute("DELETE FROM embeddings")
        conn.commit()
        conn.close()

    def test_merged_hits_carry_repo_attribution(self):
        from cairn.graph.federation import federated_search

        result = federated_search("authenticate", limit=10)

        self.assertTrue(result.hits, "merged ranking is empty")
        workspaces = {str(self.store_a.workspace), str(self.store_b.workspace)}
        for hit in result.hits:
            self.assertIn(hit["workspace"], workspaces)
            self.assertIsInstance(hit["score"], float)
        contributing = {hit["workspace"] for hit in result.hits}
        self.assertEqual(contributing, workspaces)
        scores = [hit["score"] for hit in result.hits]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(result.dropped, [])

    def test_lexical_only_store_still_contributes(self):
        from cairn.graph.federation import federated_search

        result = federated_search("authenticate", limit=10)

        self.assertEqual(result.dropped, [])
        beta_hits = [
            hit for hit in result.hits
            if hit["workspace"] == str(self.store_b.workspace)
        ]
        self.assertTrue(beta_hits, "lexical-only store contributed no hits")
        for hit in beta_hits:
            self.assertEqual(hit["provenance"], "bm25")
            self.assertEqual(hit["name"], "authenticate")

    def test_cli_json_output_carries_attribution_and_states(self):
        import json
        from click.testing import CliRunner

        from cairn.cli.main import main

        with self._silence_logging():
            cli = CliRunner().invoke(
                main,
                ["federated-search", "authenticate", "--limit", "10", "--json"],
                catch_exceptions=False,
            )
        self.assertEqual(cli.exit_code, 0, cli.output)
        payload = json.loads(cli.stdout)

        self.assertEqual(payload["query"], "authenticate")
        self.assertEqual(payload["dropped"], [])
        self.assertEqual(
            set(payload["states"]),
            {str(self.store_a.workspace), str(self.store_b.workspace)},
        )
        self.assertTrue(payload["hits"], "CLI JSON carries no hits")
        for hit in payload["hits"]:
            self.assertIn(
                hit["workspace"],
                {str(self.store_a.workspace), str(self.store_b.workspace)},
            )
            self.assertIsInstance(hit["score"], float)

    def test_tool_and_cli_surfaces_agree(self):
        import json
        from click.testing import CliRunner

        from cairn.cli.main import main
        from cairn.mcp_server.tools_federation import federated_search as tool

        with self._silence_logging():
            cli = CliRunner().invoke(
                main,
                ["federated-search", "authenticate", "--limit", "10", "--json"],
                catch_exceptions=False,
            )
        self.assertEqual(cli.exit_code, 0, cli.output)
        cli_out = json.loads(cli.stdout)
        self.assertTrue(cli_out["hits"], "CLI merged ranking is empty")

        # The tool renders one line per hit of the same merged ranking, in
        # order, each naming the hit and its workspace: "[prov score] kind
        # name  (file)  [repo]  @ workspace".
        rendered = []
        for line in tool("authenticate", limit=10).splitlines():
            if " @ " not in line:
                continue
            head, workspace = line.rsplit(" @ ", 1)
            if not head.lstrip().startswith("["):
                continue
            label = head.split("] ", 1)[1]
            name = label.split("  (", 1)[0].split(" ", 1)[1]
            rendered.append((name, workspace.strip()))
        expected = [
            (hit.get("qualified_name") or hit["name"], hit["workspace"])
            for hit in cli_out["hits"]
        ]
        self.assertEqual(rendered, expected)


class UnavailableStoreTests(FederationStoreCase):
    """Named dropped-store report: missing, locked, unindexed, no stores."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="cairn-federation-")
        cls._cairn_home = Path(cls._tmp) / "_cairn_home"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def setUp(self):
        self._token = self._enter_sandbox()
        try:
            # Fresh tree per method: each test damages its own.
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._build_stores()
        except BaseException:
            self._exit_sandbox(self._token)
            raise

    def tearDown(self):
        self._exit_sandbox(self._token)

    @classmethod
    def _build_stores(cls):
        cls.store_a = cls._make_store("alpha", "auth.py", ALPHA_SRC)
        cls.store_b = cls._make_store("beta", "session.py", BETA_SRC)
        cls.store_c = cls._make_store("gamma", "gamma.py", GAMMA_SRC)

    def _healthy_workspaces(self):
        return {str(self.store_a.workspace), str(self.store_b.workspace)}

    def test_missing_store_is_named(self):
        from cairn.graph.federation import federated_search

        shutil.rmtree(self.store_c.home)
        result = federated_search("authenticate", limit=10)

        self.assertEqual(result.states[str(self.store_c.workspace)], "missing")
        self.assertEqual(result.dropped, [str(self.store_c.workspace)])
        self.assertEqual(
            set(result.states),
            self._healthy_workspaces() | {str(self.store_c.workspace)},
        )
        self.assertEqual(
            {hit["workspace"] for hit in result.hits}, self._healthy_workspaces()
        )

    def test_unindexed_store_is_named(self):
        from cairn import paths
        from cairn.graph.federation import federated_search

        ws = Path(self._tmp) / "ws-delta" / "delta"
        (ws / ".git").mkdir(parents=True)
        (ws / "delta.py").write_text(GAMMA_SRC)
        store_d = paths.register_workspace(ws)  # registered, never indexed

        result = federated_search("authenticate", limit=10)

        self.assertEqual(result.states[str(store_d.workspace)], "unindexed")
        self.assertEqual(result.dropped, [str(store_d.workspace)])
        self.assertEqual(
            set(result.states),
            self._healthy_workspaces()
            | {str(self.store_c.workspace), str(store_d.workspace)},
        )
        self.assertEqual(
            {hit["workspace"] for hit in result.hits},
            self._healthy_workspaces() | {str(self.store_c.workspace)},
        )

    def test_locked_store_is_named(self):
        from cairn.graph.federation import federated_search
        from cairn.graph.schema import get_db

        # Rollback-journal mode plus an EXCLUSIVE hold: readers of the store
        # block and time out, which WAL mode would wave through.
        holder = get_db(str(self.store_c.db))
        holder.execute("PRAGMA journal_mode = DELETE")
        holder.execute("BEGIN EXCLUSIVE")
        try:
            result = federated_search("authenticate", limit=10)
        finally:
            holder.rollback()
            holder.close()

        self.assertEqual(result.states[str(self.store_c.workspace)], "locked")
        self.assertEqual(result.dropped, [str(self.store_c.workspace)])
        self.assertEqual(
            set(result.states),
            self._healthy_workspaces() | {str(self.store_c.workspace)},
        )
        self.assertEqual(
            {hit["workspace"] for hit in result.hits}, self._healthy_workspaces()
        )

    def test_empty_registry_reports_clearly(self):
        from cairn import paths
        from cairn.graph.federation import federated_search

        paths.REGISTRY_FILE.unlink()  # sandbox registry: zero stores
        result = federated_search("authenticate")

        self.assertEqual(result.states, {})
        self.assertEqual(result.hits, [])
        self.assertEqual(result.dropped, [])

    def test_cli_blank_query_rejected_before_any_store(self):
        from click.testing import CliRunner

        from cairn import paths
        from cairn.cli.main import main

        paths.REGISTRY_FILE.unlink()  # zero registered stores
        cli = CliRunner().invoke(main, ["federated-search", "   "])

        # UsageError's exit code: the input rejection, not the no-stores
        # path — validation fires before any registry or store access.
        self.assertEqual(cli.exit_code, 2)
        combined = cli.output + cli.stderr
        self.assertIn("empty", combined)
        self.assertNotIn("registered", combined)

    def test_cli_names_dropped_store(self):
        from click.testing import CliRunner

        from cairn.cli.main import main

        shutil.rmtree(self.store_c.home)
        cli = CliRunner().invoke(
            main,
            ["federated-search", "authenticate", "--limit", "10"],
            catch_exceptions=False,
        )
        self.assertEqual(cli.exit_code, 0, cli.output)
        self.assertIn("authenticate", cli.output)
        self.assertIn(str(self.store_a.workspace), cli.output)
        self.assertIn(str(self.store_c.workspace), cli.output)
        self.assertIn("missing", cli.output)


class EmbeddingBackendTests(FederationStoreCase):
    """--shared-embed serving over stamp-compatible stores (FR-002)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="cairn-federation-")
        cls._cairn_home = Path(cls._tmp) / "_cairn_home"
        token = cls._enter_sandbox()
        try:
            cls.store_a = cls._make_store("alpha", "auth.py", ALPHA_SRC)
            cls.store_b = cls._make_store("beta", "session.py", BETA_SRC)
        finally:
            cls._exit_sandbox(token)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def setUp(self):
        self._token = self._enter_sandbox()

    def tearDown(self):
        self._exit_sandbox(self._token)

    def test_shared_backend_option_returns_attributed_results(self):
        from cairn.graph.federation import federated_search

        with self._counted_embeds() as calls:
            result = federated_search(
                "authenticate", limit=10, shared_embed=True
            )

        workspaces = {str(self.store_a.workspace), str(self.store_b.workspace)}
        self.assertTrue(result.hits, "merged ranking is empty")
        self.assertEqual({hit["workspace"] for hit in result.hits}, workspaces)
        self.assertEqual(result.dropped, [])
        scores = [hit["score"] for hit in result.hits]
        self.assertEqual(scores, sorted(scores, reverse=True))
        # One shared backend serves both stores: the query embeds once.
        self.assertEqual(calls, ["authenticate"])

    def test_default_mode_embeds_per_store(self):
        from cairn.graph.federation import federated_search

        with self._counted_embeds() as calls:
            result = federated_search("authenticate", limit=10)

        workspaces = {str(self.store_a.workspace), str(self.store_b.workspace)}
        self.assertTrue(result.hits, "merged ranking is empty")
        self.assertEqual({hit["workspace"] for hit in result.hits}, workspaces)
        self.assertEqual(result.dropped, [])
        # Default is off: each store embeds the query with its own backend.
        self.assertEqual(calls, ["authenticate", "authenticate"])

    def test_cli_flag_shares_one_embed(self):
        import json
        from click.testing import CliRunner

        from cairn.cli.main import main

        with self._counted_embeds() as calls:
            cli = CliRunner().invoke(
                main,
                [
                    "federated-search", "authenticate", "--limit", "10",
                    "--shared-embed", "--json",
                ],
                catch_exceptions=False,
            )
        self.assertEqual(cli.exit_code, 0, cli.output)
        payload = json.loads(cli.stdout)

        workspaces = {str(self.store_a.workspace), str(self.store_b.workspace)}
        self.assertTrue(payload["hits"], "CLI merged ranking is empty")
        self.assertEqual({hit["workspace"] for hit in payload["hits"]}, workspaces)
        self.assertEqual(payload["dropped"], [])
        # The flag forwards to the core: one shared embed serves both stores.
        self.assertEqual(calls, ["authenticate"])

    def test_cli_default_embeds_per_store(self):
        from click.testing import CliRunner

        from cairn.cli.main import main

        with self._counted_embeds() as calls:
            cli = CliRunner().invoke(
                main,
                ["federated-search", "authenticate", "--limit", "10"],
                catch_exceptions=False,
            )
        self.assertEqual(cli.exit_code, 0, cli.output)
        # Flag off: the default per-store embedding path is preserved.
        self.assertEqual(calls, ["authenticate", "authenticate"])

    def test_tool_shared_embed_param_shares_one_embed(self):
        from cairn.mcp_server.tools_federation import federated_search as tool

        with self._counted_embeds() as calls:
            out = tool("authenticate", limit=10, shared_embed=True)

        workspaces = {str(self.store_a.workspace), str(self.store_b.workspace)}
        for ws in workspaces:
            self.assertIn(str(ws), out)
        # The keyword forwards to the core: one shared embed, both stores.
        self.assertEqual(calls, ["authenticate"])

    def test_tool_default_embeds_per_store(self):
        from cairn.mcp_server.tools_federation import federated_search as tool

        with self._counted_embeds() as calls:
            out = tool("authenticate", limit=10)

        self.assertIn("authenticate", out)
        # Keyword omitted: the default per-store embedding path is preserved.
        self.assertEqual(calls, ["authenticate", "authenticate"])


class MixedBackendTests(FederationStoreCase):
    """A foreign-stamped store keeps the federation on per-store serving."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="cairn-federation-")
        cls._cairn_home = Path(cls._tmp) / "_cairn_home"
        token = cls._enter_sandbox()
        try:
            from cairn.graph.schema import get_db

            cls.store_a = cls._make_store("alpha", "auth.py", ALPHA_SRC)
            cls.store_b = cls._make_store("beta", "session.py", BETA_SRC)
            cls.store_c = cls._make_store("gamma", "gamma.py", GAMMA_SRC)
            conn = get_db(str(cls.store_c.db))
            conn.execute("UPDATE embeddings SET model = 'legacy-model-v9'")
            conn.commit()
            conn.close()
        finally:
            cls._exit_sandbox(token)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def setUp(self):
        self._token = self._enter_sandbox()

    def tearDown(self):
        self._exit_sandbox(self._token)

    def test_mixed_stamps_fuse_rankings_per_store(self):
        from cairn.graph.federation import federated_search

        with self._counted_embeds() as calls:
            result = federated_search(
                "authenticate", limit=10, shared_embed=True
            )

        workspaces = {
            str(self.store_a.workspace),
            str(self.store_b.workspace),
            str(self.store_c.workspace),
        }
        self.assertTrue(result.hits, "merged ranking is empty")
        self.assertEqual({hit["workspace"] for hit in result.hits}, workspaces)
        self.assertEqual(result.dropped, [])
        scores = [hit["score"] for hit in result.hits]
        self.assertEqual(scores, sorted(scores, reverse=True))
        # Stamp mismatch blocks sharing: alpha and beta embed per store,
        # gamma's foreign-stamp rows contribute BM25 with no embed call.
        self.assertEqual(calls, ["authenticate", "authenticate"])


class FederatedAskTests(FederationStoreCase):
    """``cairn ask --all-repos`` composes per-repo attributed answers."""

    @classmethod
    def _enter_sandbox(cls):
        token = super()._enter_sandbox()
        # The CLI recorder starts a metrics flusher whose drain resolves a
        # store at process exit; the master kill switch keeps it inert.
        os.environ["CAIRN_TELEMETRY"] = "off"
        return token

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="cairn-federation-ask-")
        cls._cairn_home = Path(cls._tmp) / "_cairn_home"
        token = cls._enter_sandbox()
        try:
            cls.store_a = cls._make_store("alpha", "auth.py", ALPHA_SRC)
            cls.store_b = cls._make_store("beta", "session.py", BETA_SRC)
        finally:
            cls._exit_sandbox(token)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def setUp(self):
        self._token = self._enter_sandbox()

    def tearDown(self):
        self._exit_sandbox(self._token)

    def _invoke_ask(self, *args):
        from click.testing import CliRunner

        from cairn.cli.main import main

        return CliRunner().invoke(main, ["ask", *args])

    def test_ask_all_repos_attributes_context_per_repo(self):
        import json

        result = self._invoke_ask("--all-repos", "--json", "authenticate")

        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.stdout)
        ws_a = str(self.store_a.workspace)
        ws_b = str(self.store_b.workspace)
        self.assertEqual(set(payload["answers"]), {ws_a, ws_b})
        self.assertEqual(payload["states"], {ws_a: "ok", ws_b: "ok"})
        self.assertEqual(payload["dropped"], [])
        # Each repo's answer draws on that repo's own context only.
        alpha_json = json.dumps(payload["answers"][ws_a])
        beta_json = json.dumps(payload["answers"][ws_b])
        self.assertIn("authenticate", alpha_json)
        self.assertIn("auth.py", alpha_json)
        self.assertNotIn("session.py", alpha_json)
        self.assertIn("authenticate", beta_json)
        self.assertIn("session.py", beta_json)
        self.assertNotIn("auth.py", beta_json)

    def test_ask_all_repos_names_dropped_stores(self):
        import json

        from cairn import paths

        ws = Path(self._tmp) / "ws-delta" / "delta"
        (ws / ".git").mkdir(parents=True)
        (ws / "delta.py").write_text(GAMMA_SRC)
        store_d = paths.register_workspace(ws)
        shutil.rmtree(store_d.home)  # registered, data gone
        try:
            result = self._invoke_ask("--all-repos", "--json", "authenticate")

            self.assertEqual(result.exit_code, 0, result.output)
            payload = json.loads(result.stdout)
            ws_a = str(self.store_a.workspace)
            ws_b = str(self.store_b.workspace)
            self.assertEqual(set(payload["answers"]), {ws_a, ws_b})
            self.assertEqual(payload["dropped"], [str(store_d.workspace)])
            self.assertEqual(payload["states"][str(store_d.workspace)], "missing")

            text = self._invoke_ask("--all-repos", "authenticate")

            self.assertEqual(text.exit_code, 0, text.output)
            self.assertIn("Dropped stores:", text.output)
            self.assertIn(f"missing: {store_d.workspace}", text.output)
        finally:
            reg = paths._load_registry()
            reg.pop(str(store_d.workspace), None)
            paths._save_registry(reg)

    def test_blank_input_rejected_before_querying(self):
        touched = str(self.store_a.workspace) + str(self.store_b.workspace)
        for blank in ("", "   "):
            result = self._invoke_ask("--all-repos", blank)

            self.assertNotEqual(result.exit_code, 0)
            combined = result.output + result.stderr
            self.assertIn("must not be empty", combined)
            self.assertNotIn(touched, combined)


class SingleStoreBaselineTests(FederationStoreCase):
    """Default commands answer from the one resolved store only (FR-005)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="cairn-federation-baseline-")
        cls._cairn_home = Path(cls._tmp) / "_cairn_home"
        token = cls._enter_sandbox()
        try:
            cls._build_stores()
        finally:
            cls._exit_sandbox(token)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def setUp(self):
        self._token = self._enter_sandbox()

    def tearDown(self):
        from cairn.mcp_server._server_core import _reset_conn_pool

        _reset_conn_pool()
        self._exit_sandbox(self._token)

    @classmethod
    def _build_stores(cls):
        from cairn.graph.schema import get_db
        from cairn.memory.promotion import capture_memory
        from cairn.okf.bundle import OKFBundle

        cls.store_a = cls._make_store("alpha", "auth.py", ALPHA_SRC)
        cls.store_b = cls._make_store("beta", "session.py", BETA_SRC)
        cls.store_c = cls._make_store("gamma", "gamma.py", GAMMA_SRC)
        # One distinct memory per store: a default-path recall may only
        # surface the resolved store's own memory.
        memories = (
            (cls.store_a, "Authenticator delegates verification to login"),
            (cls.store_b, "SessionManager refreshes sessions on authenticate"),
            (cls.store_c, "TokenValidator checks claims on authenticate"),
        )
        for store, title in memories:
            conn = get_db(str(store.db))
            try:
                capture_memory(
                    conn, OKFBundle(str(store.knowledge)),
                    type_="decision", title=title,
                    body=f"{title} (single-store baseline fixture).",
                )
            finally:
                conn.close()

    def test_default_commands_stay_single_store(self):
        import json
        from click.testing import CliRunner

        from cairn import paths
        from cairn.cli.main import main
        from cairn.mcp_server.tools_memory import recall_memory

        # A fresh `cairn` process resolves its store from the workspace
        # context; CAIRN_WORKSPACE pins that context to alpha.
        os.environ["CAIRN_WORKSPACE"] = str(self.store_a.workspace)
        resolved = paths.resolve_store()
        self.assertEqual(Path(resolved.db), Path(self.store_a.db))
        self.assertEqual(Path(resolved.knowledge), Path(self.store_a.knowledge))

        other_tokens = (
            "SessionManager", "refresh_session", "session.py",
            "TokenValidator", "gamma.py",
        )

        def assert_no_leakage(text):
            for token in other_tokens:
                self.assertNotIn(token, text)

        cli = CliRunner()
        with self._counted_registry_iters() as iters:
            # The CLI bakes --db/--knowledge defaults in at import time;
            # resolve_store() is the store a fresh process in this workspace
            # resolves, so passing it explicitly reproduces the default path.
            search_out = cli.invoke(
                main,
                ["search", "Authenticator", "--db", str(resolved.db), "--json"],
                catch_exceptions=False,
            )
            self.assertEqual(search_out.exit_code, 0, search_out.output)
            rows = json.loads(search_out.stdout)
            self.assertTrue(rows, "resolved store returned no search rows")
            self.assertIn("Authenticator", json.dumps(rows))
            self.assertIn("auth.py", json.dumps(rows))
            assert_no_leakage(json.dumps(rows))

            ask_out = cli.invoke(
                main,
                [
                    "ask", "authenticate",
                    "--db", str(resolved.db),
                    "--knowledge", str(resolved.knowledge),
                    "--json",
                ],
                catch_exceptions=False,
            )
            self.assertEqual(ask_out.exit_code, 0, ask_out.output)
            payload = json.loads(ask_out.stdout)
            # Single-store route shape, not the --all-repos envelope.
            self.assertIn("intent", payload)
            self.assertIn("results", payload)
            self.assertIn("auth.py", json.dumps(payload))
            assert_no_leakage(json.dumps(payload))

            memory_out = cli.invoke(
                main,
                [
                    "memory", "search", "Authenticator",
                    "--db", str(resolved.db),
                    "--knowledge", str(resolved.knowledge),
                ],
                catch_exceptions=False,
            )
            self.assertEqual(memory_out.exit_code, 0, memory_out.output)
            self.assertIn(
                "Authenticator delegates verification to login",
                memory_out.output,
            )
            assert_no_leakage(memory_out.output)

            # The tool resolves its store at call time: the true default
            # path, with no db passed at all.
            recall_out = recall_memory("Authenticator")
            self.assertIn(
                "Authenticator delegates verification to login", recall_out
            )
            assert_no_leakage(recall_out)

        self.assertEqual(iters, [])


if __name__ == "__main__":
    unittest.main()
