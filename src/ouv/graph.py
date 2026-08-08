from pathlib import Path
from typing import List, Dict, Set, Tuple, Optional
import networkx as nx
from ouv.discovery import ModuleInfo


class CycleError(Exception):
    def __init__(self, cycle: List[str]):
        self.cycle = cycle
        super().__init__(f"Cyclic dependency detected: {' -> '.join(cycle)}")


class DependencyGraphBuilder:
    """Builds a dependency DAG from discovered modules using networkx."""

    def __init__(self, modules: List[ModuleInfo], external_addons: Optional[Set[str]] = None):
        self.modules = {m.name: m for m in modules}
        self.external_addons = external_addons or set()
        self.graph = nx.DiGraph()
        self._build_graph()

    def _build_graph(self):
        # Add all discovered internal modules as nodes
        for name, mod in self.modules.items():
            self.graph.add_node(name, is_internal=True, module_info=mod)

        # Add edges for dependencies
        for name, mod in self.modules.items():
            for dep in mod.depends:
                if dep in self.modules:
                    # Dep must validate BEFORE mod, so edge goes dep -> mod
                    self.graph.add_edge(dep, name)
                else:
                    # External or missing dependency node
                    is_known_external = dep in self.external_addons
                    if not self.graph.has_node(dep):
                        self.graph.add_node(dep, is_internal=False, is_external=is_known_external)
                    self.graph.add_edge(dep, name)

    def check_cycles(self) -> None:
        try:
            cycles = list(nx.simple_cycles(self.graph))
            if cycles:
                cycle_path = cycles[0] + [cycles[0][0]]
                raise CycleError(cycle_path)
        except nx.NetworkXNoCycle:
            pass

    def get_topological_order(self) -> List[str]:
        """Returns topological ordering of internal modules."""
        self.check_cycles()
        topological = list(nx.topological_sort(self.graph))
        # Filter to return only internal modules
        return [node for node in topological if node in self.modules]

    def get_missing_dependencies(self, module_name: str) -> List[str]:
        """Identifies missing non-internal and non-external dependencies for a module."""
        missing = []
        for predecessor in self.graph.predecessors(module_name):
            node_data = self.graph.nodes[predecessor]
            if not node_data.get("is_internal") and not node_data.get("is_external"):
                missing.append(predecessor)
        return missing

    def get_layers(self) -> Dict[int, List[str]]:
        """Group internal modules by dependency depth layers."""
        self.check_cycles()
        topological = self.get_topological_order()
        layers: Dict[int, List[str]] = {}
        depths: Dict[str, int] = {}

        for node in topological:
            preds = list(self.graph.predecessors(node))
            if not preds:
                d = 0
            else:
                d = max(depths.get(p, 0) for p in preds) + 1
            depths[node] = d
            layers.setdefault(d, []).append(node)

        return layers
