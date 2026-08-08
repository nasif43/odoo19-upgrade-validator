import ast
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from lxml import etree
import ruamel.yaml


class Rule(BaseModel):
    id: str
    category: str
    severity: str  # blocking | warning | info
    evidence: str  # OFFICIAL | COMMUNITY
    description: str
    check_type: str  # regex | ast | xpath | callable
    pattern: Optional[str] = None
    ast_pattern: Optional[str] = None
    xpath_pattern: Optional[str] = None
    applies_to: str
    fix_hint: str
    source: str
    confirmed_in_module_count: int = 0
    confirmed_fixes: List[Any] = Field(default_factory=list)
    install_error_signatures: List[Any] = Field(default_factory=list)


class RuleMatch(BaseModel):
    rule_id: str
    file_path: Path
    line_number: Optional[int] = None
    snippet: Optional[str] = None
    severity: str
    fix_hint: str
    evidence: str
    source: str


class StaticAnalysisEngine:
    """Generic static rule executor driven by YAML rulesets."""

    def __init__(self, ruleset_path: Path):
        self.ruleset_path = ruleset_path
        self.rules: List[Rule] = []
        self._load_ruleset()

    def _load_ruleset(self):
        yaml = ruamel.yaml.YAML(typ="safe")
        data = yaml.load(self.ruleset_path.read_text(encoding="utf-8"))
        for rule_dict in data.get("rules", []):
            self.rules.append(Rule(**rule_dict))

    def analyze_module(self, module_path: Path) -> List[RuleMatch]:
        matches: List[RuleMatch] = []
        for rule in self.rules:
            # Find files matching applies_to glob
            matching_files = list(module_path.glob(rule.applies_to))
            for file_path in matching_files:
                if not file_path.is_file():
                    continue
                file_matches = self._check_rule_on_file(rule, file_path)
                matches.extend(file_matches)
        return matches

    def _check_rule_on_file(self, rule: Rule, file_path: Path) -> List[RuleMatch]:
        matches = []
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception:
            return matches

        if rule.check_type == "regex" and rule.pattern:
            matches.extend(self._check_regex(rule, file_path, content))
        elif rule.check_type == "ast":
            matches.extend(self._check_ast(rule, file_path, content))
        elif rule.check_type == "xpath" and rule.xpath_pattern:
            matches.extend(self._check_xpath(rule, file_path, content))

        return matches

    def _check_regex(self, rule: Rule, file_path: Path, content: str) -> List[RuleMatch]:
        matches = []
        pattern = re.compile(rule.pattern)
        for i, line in enumerate(content.splitlines(), start=1):
            if pattern.search(line):
                matches.append(RuleMatch(
                    rule_id=rule.id,
                    file_path=file_path,
                    line_number=i,
                    snippet=line.strip(),
                    severity=rule.severity,
                    fix_hint=rule.fix_hint,
                    evidence=rule.evidence,
                    source=rule.source
                ))
        return matches

    def _check_ast(self, rule: Rule, file_path: Path, content: str) -> List[RuleMatch]:
        matches = []
        if not file_path.suffix == ".py":
            return matches

        try:
            tree = ast.parse(content, filename=str(file_path))
        except Exception:
            return matches

        lines = content.splitlines()

        class ASTVisitor(ast.NodeVisitor):
            def visit_ImportFrom(self, node: ast.ImportFrom):
                if rule.ast_pattern and "import_from_module starts_with 'odoo.osv'" in rule.ast_pattern:
                    if node.module and node.module.startswith("odoo.osv"):
                        line_no = getattr(node, "lineno", None)
                        snippet = lines[line_no - 1].strip() if line_no and line_no <= len(lines) else None
                        matches.append(RuleMatch(
                            rule_id=rule.id,
                            file_path=file_path,
                            line_number=line_no,
                            snippet=snippet,
                            severity=rule.severity,
                            fix_hint=rule.fix_hint,
                            evidence=rule.evidence,
                            source=rule.source
                        ))
                self.generic_visit(node)

            def visit_FunctionDef(self, node: ast.FunctionDef):
                if rule.ast_pattern and "decorator_name == 'api.returns'" in rule.ast_pattern:
                    for dec in node.decorator_list:
                        dec_name = ""
                        if isinstance(dec, ast.Attribute) and dec.attr == "returns":
                            dec_name = "api.returns"
                        elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr == "returns":
                            dec_name = "api.returns"
                        if dec_name == "api.returns":
                            line_no = getattr(dec, "lineno", None)
                            snippet = lines[line_no - 1].strip() if line_no and line_no <= len(lines) else None
                            matches.append(RuleMatch(
                                rule_id=rule.id,
                                file_path=file_path,
                                line_number=line_no,
                                snippet=snippet,
                                severity=rule.severity,
                                fix_hint=rule.fix_hint,
                                evidence=rule.evidence,
                                source=rule.source
                            ))
                self.generic_visit(node)

            def visit_Assign(self, node: ast.Assign):
                if rule.ast_pattern and "attribute_assignment_name == '_sql_constraints'" in rule.ast_pattern:
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == "_sql_constraints":
                            line_no = getattr(node, "lineno", None)
                            snippet = lines[line_no - 1].strip() if line_no and line_no <= len(lines) else None
                            matches.append(RuleMatch(
                                rule_id=rule.id,
                                file_path=file_path,
                                line_number=line_no,
                                snippet=snippet,
                                severity=rule.severity,
                                fix_hint=rule.fix_hint,
                                evidence=rule.evidence,
                                source=rule.source
                            ))
                self.generic_visit(node)

        visitor = ASTVisitor()
        visitor.visit(tree)
        return matches

    def _check_xpath(self, rule: Rule, file_path: Path, content: str) -> List[RuleMatch]:
        matches = []
        if not file_path.suffix == ".xml":
            return matches

        try:
            parser = etree.XMLParser(recover=True, remove_blank_text=True)
            doc = etree.fromstring(content.encode("utf-8"), parser=parser)
            if doc is None:
                return matches
            elements = doc.xpath(rule.xpath_pattern)
            for elem in elements:
                line_no = getattr(elem, "sourceline", None)
                matches.append(RuleMatch(
                    rule_id=rule.id,
                    file_path=file_path,
                    line_number=line_no,
                    snippet=etree.tostring(elem, encoding="utf-8").decode("utf-8").strip()[:100],
                    severity=rule.severity,
                    fix_hint=rule.fix_hint,
                    evidence=rule.evidence,
                    source=rule.source
                ))
        except Exception:
            pass
        return matches
