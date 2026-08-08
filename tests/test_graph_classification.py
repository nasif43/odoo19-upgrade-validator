"""Tests for manifestoo-core CE/EE dependency classification in graph.py."""

from pathlib import Path
import pytest
from ouv.discovery import ModuleInfo
from ouv.graph import DependencyGraphBuilder


def _make_mod(name: str, depends: list) -> ModuleInfo:
    return ModuleInfo(name=name, path=Path(f"/{name}"), manifest={}, depends=depends)


class TestDepKind:
    def test_internal_dep_classified_internal(self):
        m1 = _make_mod("mod_a", [])
        m2 = _make_mod("mod_b", ["mod_a"])
        builder = DependencyGraphBuilder([m1, m2])
        assert builder.graph.nodes["mod_a"]["kind"] == DependencyGraphBuilder.KIND_INTERNAL

    def test_known_ce_dep_classified_core_ce(self):
        m1 = _make_mod("my_module", ["sale", "base", "account"])
        builder = DependencyGraphBuilder([m1])
        for ce_dep in ["sale", "base", "account"]:
            assert builder.graph.nodes[ce_dep]["kind"] == DependencyGraphBuilder.KIND_CE, ce_dep

    def test_known_ee_dep_classified_core_ee(self):
        m1 = _make_mod("my_module", ["account_accountant", "sign"])
        builder = DependencyGraphBuilder([m1])
        for ee_dep in ["account_accountant", "sign"]:
            assert builder.graph.nodes[ee_dep]["kind"] == DependencyGraphBuilder.KIND_EE, ee_dep

    def test_unknown_third_party_classified_unknown_external(self):
        m1 = _make_mod("my_module", ["some_oca_module", "fictional_addon_xyz"])
        builder = DependencyGraphBuilder([m1])
        for unknown in ["some_oca_module", "fictional_addon_xyz"]:
            assert builder.graph.nodes[unknown]["kind"] == DependencyGraphBuilder.KIND_UNKNOWN, unknown


class TestGetEeDependencies:
    def test_returns_ee_deps_only(self):
        m1 = _make_mod("my_module", ["sale", "account_accountant", "sign", "some_oca"])
        builder = DependencyGraphBuilder([m1])
        ee_deps = builder.get_ee_dependencies("my_module")
        assert set(ee_deps) == {"account_accountant", "sign"}

    def test_returns_empty_when_no_ee_deps(self):
        m1 = _make_mod("my_module", ["sale", "base"])
        builder = DependencyGraphBuilder([m1])
        assert builder.get_ee_dependencies("my_module") == []


class TestGetUnknownExternalDependencies:
    def test_returns_unknown_external_deps_only(self):
        m1 = _make_mod("my_module", ["sale", "account_accountant", "some_oca", "fictional_xyz"])
        builder = DependencyGraphBuilder([m1])
        unknowns = builder.get_unknown_external_dependencies("my_module")
        assert set(unknowns) == {"some_oca", "fictional_xyz"}

    def test_returns_empty_when_all_known(self):
        m1 = _make_mod("my_module", ["sale", "base", "account_accountant"])
        builder = DependencyGraphBuilder([m1])
        assert builder.get_unknown_external_dependencies("my_module") == []


class TestTopologicalOrderUnchanged:
    """Verify that the networkx DAG logic is still intact after refactor."""

    def test_topological_order_with_ce_deps(self):
        m1 = _make_mod("mod_a", ["base"])
        m2 = _make_mod("mod_b", ["mod_a", "sale"])
        builder = DependencyGraphBuilder([m1, m2])
        order = builder.get_topological_order()
        assert order.index("mod_a") < order.index("mod_b")

    def test_cycle_detection_still_works(self):
        from ouv.graph import CycleError
        m1 = _make_mod("mod_a", ["mod_b"])
        m2 = _make_mod("mod_b", ["mod_a"])
        builder = DependencyGraphBuilder([m1, m2])
        with pytest.raises(CycleError):
            builder.get_topological_order()

    def test_layers_with_mixed_deps(self):
        m1 = _make_mod("mod_a", ["base"])             # layer 0
        m2 = _make_mod("mod_b", ["mod_a", "sale"])   # layer 1
        m3 = _make_mod("mod_c", ["mod_b"])           # layer 2
        builder = DependencyGraphBuilder([m1, m2, m3])
        layers = builder.get_layers()
        layer_map = {mod: layer for layer, mods in layers.items() for mod in mods}
        assert layer_map["mod_a"] < layer_map["mod_b"] < layer_map["mod_c"]
