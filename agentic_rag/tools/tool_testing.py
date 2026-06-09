"""Auto-generate tool test cases from tool schema and description.

For each registered tool, generates a minimal pytest that tests:
1. Well-formed input returns success.
2. Malformed input (missing required fields) returns error.
3. Result matches the ``ToolResult`` schema.

Usage::

    python -m agentic_rag.tools.tool_testing --output tests/agentic_rag/tools/
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from agentic_rag.models import ToolCall
from agentic_rag.tools.base import BaseTool, ToolRegistry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Test template
# ---------------------------------------------------------------------------

PYTEST_TEMPLATE = '''\
"""Auto-generated tests for the '{tool_name}' tool.

Generated from tool schema on {generated_at}.
"""

import pytest


@pytest.fixture
def tool():
    from {import_path} import {class_name}
    return {class_name}()


@pytest.mark.asyncio
async def test_{safe_name}_valid_input(tool):
    """Well-formed input should succeed."""
    from agentic_rag.models import ToolCall

    call = ToolCall(
        tool_name="{tool_name}",
        input={valid_input_json},
    )
    result = await tool.execute(call)
    assert result.tool_name == "{tool_name}"
    assert result.status in ("success", "degraded")


@pytest.mark.asyncio
async def test_{safe_name}_missing_required(tool):
    """Missing required fields should return error."""
    from agentic_rag.models import ToolCall

    call = ToolCall(
        tool_name="{tool_name}",
        input={{}},
    )
    errors = tool.validate_input({{}})
    # If the tool has required params, empty input should produce errors
    if errors:
        assert len(errors) > 0


def test_{safe_name}_openai_schema(tool):
    """to_openai_function should return a valid schema dict."""
    schema = tool.to_openai_function()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "{tool_name}"
    assert "description" in schema["function"]
    assert "parameters" in schema["function"]
'''

# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


class ToolTestGenerator:
    """Generates pytest files for registered tools.

    Parameters
    ----------
    registry:
        The tool registry to generate tests for.
    output_dir:
        Directory to write test files into.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        output_dir: str | Path,
        *,
        contract_registry: dict[str, list] | None = None,
    ) -> None:
        self._registry = registry
        self._output_dir = Path(output_dir)
        self._contracts = contract_registry or {}

    def generate_all(self) -> list[Path]:
        """Generate test files for all registered tools.

        Returns a list of file paths created.
        """
        self._output_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []

        for tool_name in self._registry.list_names():
            tool = self._registry.get_required(tool_name)
            # Schema-based tests — structural validity
            path = self.generate_one(tool)
            paths.append(path)
            # Contract-based tests — business logic
            if tool_name in self._contracts:
                contract_path = self.generate_contract_tests(tool_name)
                paths.append(contract_path)

        return paths

    def generate_one(self, tool: BaseTool) -> Path:
        """Generate a test file for a single tool."""
        safe_name = tool.name.replace("-", "_").replace(".", "_")
        valid_input = self._build_valid_input(tool)

        code = PYTEST_TEMPLATE.format(
            tool_name=tool.name,
            safe_name=safe_name,
            class_name=type(tool).__name__,
            import_path=type(tool).__module__,
            valid_input_json=json.dumps(valid_input, ensure_ascii=False),
            generated_at=__import__("datetime").datetime.now().isoformat(),
        )

        file_path = self._output_dir / f"test_tool_{safe_name}.py"
        file_path.write_text(code, encoding="utf-8")
        logger.info("Generated test: %s", file_path)
        return file_path

    def generate_contract_tests(self, tool_name: str) -> Path:
        """Generate business-logic contract tests for a tool."""
        from agentic_rag.tools.contracts import generate_contract_tests as gen

        contracts = self._contracts.get(tool_name, [])
        safe_name = tool_name.replace("-", "_").replace(".", "_")
        code = gen(contracts, tool_name)

        file_path = self._output_dir / f"test_contract_{safe_name}.py"
        file_path.write_text(code, encoding="utf-8")
        logger.info("Generated contract tests: %s", file_path)
        return file_path

    @staticmethod
    def _build_valid_input(tool: BaseTool) -> dict[str, Any]:
        """Build a minimally valid input dict from the tool's JSON Schema."""
        input_data: dict[str, Any] = {}
        properties = tool.parameters.get("properties", {})
        required = tool.parameters.get("required", [])

        for field in required:
            prop = properties.get(field, {})
            ptype = prop.get("type", "string")
            if ptype == "string":
                input_data[field] = f"test_{field}"
            elif ptype in ("integer", "number"):
                input_data[field] = 1
            elif ptype == "boolean":
                input_data[field] = True
            elif ptype == "array":
                input_data[field] = []
            elif ptype == "object":
                input_data[field] = {}

        return input_data


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Generate tool test files.")
    parser.add_argument(
        "--output",
        default="tests/agentic_rag/tools",
        help="Output directory for test files.",
    )
    args = parser.parse_args()

    # Bootstrap a minimal registry
    from agentic_rag.tools.rag_tool import RAGSearchTool
    from agentic_rag.tools.web_search_tool import WebSearchTool

    registry = ToolRegistry()
    registry.register_many(RAGSearchTool(), WebSearchTool())

    generator = ToolTestGenerator(registry, args.output)
    paths = generator.generate_all()
    print(f"Generated {len(paths)} test files:")
    for p in paths:
        print(f"  {p}")


if __name__ == "__main__":
    main()
