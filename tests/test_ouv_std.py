import unittest
import tempfile
from pathlib import Path
from ouv.discovery import ModuleDiscoverer, ModuleInfo
from ouv.graph import DependencyGraphBuilder
from ouv.history import HistoryStore
from ouv.dispatch_lock import DispatchLock


class TestOUVCore(unittest.TestCase):

    def test_module_discovery(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            mod_dir = tmp_path / "test_module"
            mod_dir.mkdir()
            manifest = mod_dir / "__manifest__.py"
            manifest.write_text("{'name': 'test_module', 'depends': ['base']}", encoding="utf-8")

            discovered = ModuleDiscoverer.discover_all([tmp_path])
            self.assertEqual(len(discovered), 1)
            self.assertEqual(discovered[0].name, "test_module")
            self.assertEqual(discovered[0].depends, ["base"])

    def test_dependency_dag_topological_sort(self):
        m1 = ModuleInfo(name="mod_a", path=Path("/a"), manifest={}, depends=["base"])
        m2 = ModuleInfo(name="mod_b", path=Path("/b"), manifest={}, depends=["mod_a"])

        builder = DependencyGraphBuilder([m1, m2])
        order = builder.get_topological_order()
        self.assertEqual(order, ["mod_a", "mod_b"])

    def test_dispatch_lock(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            store = HistoryStore(tmp_path)
            lock = DispatchLock(store.db_path)

            self.assertTrue(lock.acquire("module_x", "agent_1"))
            self.assertFalse(lock.acquire("module_x", "agent_2"))

            lock.release("module_x", "agent_1")
            self.assertTrue(lock.acquire("module_x", "agent_2"))


if __name__ == "__main__":
    unittest.main()
