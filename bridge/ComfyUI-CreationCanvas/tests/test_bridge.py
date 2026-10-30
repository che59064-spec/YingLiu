"""Run with Python stdlib: python tests/test_bridge.py (no ComfyUI import needed)."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


class Routes:
    def get(self, path):
        return lambda function: function

    post = get


sys.modules["server"] = types.SimpleNamespace(PromptServer=types.SimpleNamespace(
    instance=types.SimpleNamespace(routes=Routes())))
sys.modules.setdefault("aiohttp", types.SimpleNamespace(web=types.SimpleNamespace()))
spec = importlib.util.spec_from_file_location("bridge", Path(__file__).resolve().parents[1] / "__init__.py",
                                            submodule_search_locations=[str(Path(__file__).resolve().parents[1])])
bridge = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bridge
spec.loader.exec_module(bridge)


class BridgeChecks(unittest.TestCase):
    def setUp(self):
        self.state = bridge.BridgeState()
        self.graph = {"nodes": [{"id": 1, "type": "UnknownNode", "extra": "保留"}],
                      "definitions": {"subgraphs": [{"id": "nested", "nodes": []}]}}
        self.state.client({"clientId": "editor", "revision": 0, "workflow": self.graph})

    def test_idempotent_command_and_lossless_result(self):
        payload = {"opId": "one", "type": "execute", "revision": 1,
                   "workflow": self.graph, "workflowId": "qwen"}
        self.assertEqual(self.state.submit(payload)[1], 202)
        self.assertEqual(self.state.submit(payload)[1], 200)
        self.assertEqual(len(self.state.take("editor")["commands"]), 1)
        self.assertEqual(self.state.take("editor")["commands"], [])
        result = {"clientId": "editor", "opId": "one", "workflow": self.graph,
                  "result": {"loaded": True, "promptId": "prompt-one"}}
        completed = self.state.finish(result)
        self.assertEqual(completed["workflow"], self.graph)
        self.assertEqual(completed["execution"]["promptId"], "prompt-one")
        revision = completed["revision"]
        self.assertEqual(self.state.finish(result)["revision"], revision)
        with self.assertRaises(bridge.BridgeError):
            self.state.submit({**payload, "type": "load"})

    def test_conflicts_do_not_replace_graph(self):
        with self.assertRaises(bridge.BridgeError):
            self.state.submit({"opId": "stale", "type": "load", "revision": 0, "workflow": self.graph})
        with self.assertRaises(bridge.BridgeError):
            self.state.client({"clientId": "other", "revision": 1, "workflow": {"nodes": []}})
        self.assertEqual(self.state.workflow, self.graph)
        self.assertEqual(self.state.revision, 1)

    def test_disconnected_client_and_execution_attribution(self):
        with patch.object(bridge.time, "time", return_value=self.state.last_seen + 13):
            self.assertFalse(self.state.state()["clientConnected"])
            with self.assertRaises(bridge.BridgeError):
                self.state.submit({"opId": "offline", "type": "load", "revision": 1, "workflow": self.graph})
        self.state.record_event({"type": "execution_start", "data": {"prompt_id": "a"}})
        self.state.record_event({"type": "progress", "promptId": "b", "data": {"value": 8, "max": 10}})
        self.assertIsNone(self.state.execution["progress"])
        self.state.record_event({"type": "progress", "promptId": "a", "data": {"value": 3, "max": 10}})
        self.assertEqual(self.state.execution["progress"]["value"], 3)
        self.state.record_event({"type": "execution_error", "data": {"prompt_id": "a", "exception_message": "test"}})
        self.assertEqual(self.state.execution["status"], "failed")


if __name__ == "__main__":
    unittest.main()
