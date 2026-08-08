import ast
from pathlib import Path
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field


class ModuleInfo(BaseModel):
    name: str
    path: Path
    manifest: Dict[str, Any]
    depends: List[str] = Field(default_factory=list)
    has_legacy_manifest: bool = False
    error: Optional[str] = None


class ModuleDiscoverer:
    """Recursively discovers Odoo modules in specified directories and parses manifests safely."""

    @staticmethod
    def discover_module(dir_path: Path) -> Optional[ModuleInfo]:
        manifest_path = dir_path / "__manifest__.py"
        legacy_manifest_path = dir_path / "__openerp__.py"

        if legacy_manifest_path.exists() and not manifest_path.exists():
            return ModuleInfo(
                name=dir_path.name,
                path=dir_path,
                manifest={},
                depends=[],
                has_legacy_manifest=True,
                error="Legacy __openerp__.py manifest detected; module must be updated to __manifest__.py"
            )

        if not manifest_path.exists():
            return None

        try:
            content = manifest_path.read_text(encoding="utf-8")
            # Parse dict safely using literal_eval via ast.parse for maximum safety
            parsed_ast = ast.parse(content, filename=str(manifest_path))
            
            # Find the main dictionary in manifest file
            manifest_dict = None
            for node in ast.walk(parsed_ast):
                if isinstance(node, ast.Dict):
                    manifest_dict = ast.literal_eval(node)
                    break

            if manifest_dict is None or not isinstance(manifest_dict, dict):
                return ModuleInfo(
                    name=dir_path.name,
                    path=dir_path,
                    manifest={},
                    depends=[],
                    error="Manifest file does not contain a valid dictionary"
                )

            depends = manifest_dict.get("depends", [])
            if not isinstance(depends, list):
                depends = []

            return ModuleInfo(
                name=dir_path.name,
                path=dir_path,
                manifest=manifest_dict,
                depends=[str(d) for d in depends]
            )

        except Exception as e:
            return ModuleInfo(
                name=dir_path.name,
                path=dir_path,
                manifest={},
                depends=[],
                error=f"Failed to parse __manifest__.py: {str(e)}"
            )

    @classmethod
    def discover_all(cls, root_paths: List[Path]) -> List[ModuleInfo]:
        modules: List[ModuleInfo] = []
        visited_paths = set()

        for root in root_paths:
            root = root.resolve()
            if not root.exists():
                continue

            if (root / "__manifest__.py").exists() or (root / "__openerp__.py").exists():
                mod = cls.discover_module(root)
                if mod and mod.path not in visited_paths:
                    modules.append(mod)
                    visited_paths.add(mod.path)
                continue

            for dirpath, dirnames, filenames in root.walk():
                if "__manifest__.py" in filenames or "__openerp__.py" in filenames:
                    mod = cls.discover_module(dirpath)
                    if mod and mod.path not in visited_paths:
                        modules.append(mod)
                        visited_paths.add(mod.path)

        return modules
