from pathlib import Path
from typing import List, Dict, Set, Tuple, Optional
import networkx as nx
from ouv.discovery import ModuleInfo


class CycleError(Exception):
    def __init__(self, cycle: List[str]):
        self.cycle = cycle
        super().__init__(f"Cyclic dependency detected: {' -> '.join(cycle)}")


class DependencyGraphBuilder:
    """Builds a dependency DAG from discovered modules using networkx.

    Non-internal dependencies are classified into four kinds using
    manifestoo-core's bundled per-series addon lists (public __all__ API only):

        internal        — present in the scanned module set
        core_ce         — Odoo 19.0 Community Edition built-in addon
        core_ee         — Odoo 19.0 Enterprise Edition built-in addon
                          (module depending on this gets 'blocked' if EE is
                          unavailable — see §4.2 of the spec)
        unknown_external — not internal, not a known Odoo 19.0 core addon
                          (e.g. OCA modules, other custom modules not scanned)

    The networkx DAG, cycle detection, and topological sort are unchanged.
    """

    # Dep-kind constants stored as node attributes in the networkx graph.
    KIND_INTERNAL = "internal"
    KIND_CE = "core_ce"
    KIND_EE = "core_ee"
    KIND_UNKNOWN = "unknown_external"

    def __init__(self, modules: List[ModuleInfo], odoo_series: str = "19.0"):
        """
        Args:
            modules: All discovered ModuleInfo objects to include in the DAG.
            odoo_series: The target Odoo series string (default "19.0").
                         Used to look up CE/EE addon membership via manifestoo-core.
        """
        from manifestoo_core.core_addons import is_core_ce_addon, is_core_ee_addon
        from manifestoo_core.odoo_series import OdooSeries

        self.modules = {m.name: m for m in modules}
        self._odoo_series = OdooSeries.from_str(odoo_series)
        self._is_core_ce = is_core_ce_addon
        self._is_core_ee = is_core_ee_addon
        self.graph = nx.DiGraph()
        self._build_graph()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _dep_kind(self, dep_name: str) -> str:
        """Classify a single dependency name into one of the four kinds."""
        if dep_name in self.modules:
            return self.KIND_INTERNAL
        if self._is_core_ce(dep_name, self._odoo_series):
            return self.KIND_CE
        if self._is_core_ee(dep_name, self._odoo_series):
            return self.KIND_EE
        return self.KIND_UNKNOWN

    def _build_graph(self):
        # Add all discovered internal modules as nodes
        for name, mod in self.modules.items():
            self.graph.add_node(name, kind=self.KIND_INTERNAL, module_info=mod)

        # Add edges for dependencies, stamping each non-internal node with its kind
        for name, mod in self.modules.items():
            for dep in mod.depends:
                kind = self._dep_kind(dep)
                if not self.graph.has_node(dep):
                    # Non-internal nodes carry no module_info
                    self.graph.add_node(dep, kind=kind)
                # Edge direction: dep must validate BEFORE name
                self.graph.add_edge(dep, name)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_cycles(self) -> None:
        """Raise CycleError if any dependency cycle exists."""
        cycles = list(nx.simple_cycles(self.graph))
        if cycles:
            cycle_path = cycles[0] + [cycles[0][0]]
            raise CycleError(cycle_path)

    def get_topological_order(self) -> List[str]:
        """Return topological ordering of *internal* modules only."""
        self.check_cycles()
        return [
            node for node in nx.topological_sort(self.graph)
            if node in self.modules
        ]

    def get_ee_dependencies(self, module_name: str) -> List[str]:
        """Return EE addon names that module_name depends on (directly).

        The orchestrator uses this to issue a 'blocked' verdict when the
        validator is running against a CE-only Odoo install (spec §4.2).
        """
        return [
            pred for pred in self.graph.predecessors(module_name)
            if self.graph.nodes[pred].get("kind") == self.KIND_EE
        ]

    def get_unknown_external_dependencies(self, module_name: str) -> List[str]:
        """Return dependencies that are neither internal nor known Odoo 19.0 addons.

        These are OCA modules, other custom codebases, or genuinely missing
        addons — the caller must decide how to handle them.
        """
        return [
            pred for pred in self.graph.predecessors(module_name)
            if self.graph.nodes[pred].get("kind") == self.KIND_UNKNOWN
        ]

    def get_layers(self) -> Dict[int, List[str]]:
        """Group internal modules by dependency depth (for DB template reuse)."""
        self.check_cycles()
        topological = self.get_topological_order()
        layers: Dict[int, List[str]] = {}
        depths: Dict[str, int] = {}

        for node in topological:
            preds = list(self.graph.predecessors(node))
            d = max((depths.get(p, 0) for p in preds), default=-1) + 1
            depths[node] = d
            layers.setdefault(d, []).append(node)

        return layers
