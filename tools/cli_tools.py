from tools.registry import registry, tool_result, tool_error
from tools.utils import is_dangerous_command, redact_sensitive_text
import yaml
import subprocess
import shlex
import os
from pathlib import Path
from typing import Dict, Any, Optional, Callable


class CLIToolManager:
    """CLI工具管理器，负责从YAML配置加载和注册CLI工具。"""

    def __init__(self, tools_dir: str = "config/cli_tools"):
        """初始化CLI工具管理器。

        Args:
            tools_dir: 存储CLI工具配置的目录路径。
        """
        self.tools_dir = Path(tools_dir)
        self.loaded_tools: Dict[str, Dict[str, Any]] = {}

    def discover_and_register(self, on_log: Optional[Callable[[str], None]] = None) -> None:
        """扫描工具目录并注册所有CLI工具。

        Args:
            on_log: 可选的日志回调函数。
        """
        if not self.tools_dir.exists():
            if on_log:
                on_log(f"工具目录不存在: {self.tools_dir}")
            return

        if on_log:
            on_log(f"扫描工具目录: {self.tools_dir}")

        for yaml_path in self.tools_dir.glob("*.yaml"):
            try:
                self._register_from_file(yaml_path, on_log)
            except Exception as e:
                if on_log:
                    on_log(f"加载工具失败 {yaml_path}: {e}")

    def _register_from_file(
        self, yaml_path: Path, on_log: Optional[Callable[[str], None]] = None
    ) -> None:
        """从YAML文件加载并注册工具。

        Args:
            yaml_path: YAML配置文件路径。
            on_log: 可选的日志回调函数。
        """
        with open(yaml_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        if not config or "name" not in config:
            if on_log:
                on_log(f"无效配置文件 (缺少name字段): {yaml_path}")
            return

        name = config["name"]
        schema = self._build_schema(config)
        handler = self._create_handler(config)

        registry.register(
            name=name,
            handler=handler,
            description=config.get("description", ""),
            parameters=schema,
            toolset=config.get("toolset", "cli"),
            emoji=config.get("emoji", "⚡"),
        )

        self.loaded_tools[name] = config

        if on_log:
            on_log(f"已注册CLI工具: {name}")

    def _build_schema(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """从配置构建JSON Schema。

        Args:
            config: 工具配置字典。

        Returns:
            JSON Schema格式的参数定义。
        """
        parameters = config.get("parameters", {})
        required_fields = config.get("required", [])

        if isinstance(parameters, dict) and parameters:
            for param_name, param_info in parameters.items():
                if isinstance(param_info, dict):
                    param_info.setdefault("description", "")
                    if "type" not in param_info:
                        param_info["type"] = "string"
                else:
                    parameters[param_name] = {"type": "string", "description": ""}
        else:
            parameters = {"type": "object", "properties": {}}

        schema = {
            "type": "object",
            "properties": parameters,
            "required": required_fields,
        }
        return schema

    def _create_handler(self, config: Dict[str, Any]) -> Callable[..., Dict[str, Any]]:
        """创建工具处理器闭包函数。

        Args:
            config: 工具配置字典。

        Returns:
            处理CLI命令执行的闭包函数。
        """
        command_template = config.get("command", "")
        timeout = config.get("timeout", 30)
        output_parser = config.get("output_parser", "text")

        def handler(**kwargs) -> Dict[str, Any]:
            try:
                formatted_command = command_template.format(**kwargs)

                if is_dangerous_command(formatted_command):
                    return tool_error(
                        code="DANGEROUS_COMMAND",
                        message="检测到危险命令，已拒绝执行",
                        details={"command": redact_sensitive_text(formatted_command)},
                    )

                result = subprocess.run(
                    formatted_command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=os.getcwd(),
                )

                stdout = redact_sensitive_text(result.stdout) if result.stdout else ""
                stderr = redact_sensitive_text(result.stderr) if result.stderr else ""

                if output_parser == "json":
                    try:
                        output_data = yaml.safe_load(stdout) if stdout else {}
                        return tool_result(data={
                            "stdout": stdout,
                            "stderr": stderr,
                            "returncode": result.returncode,
                            "parsed": output_data,
                        })
                    except yaml.YAMLError:
                        return tool_result(data={
                            "stdout": stdout,
                            "stderr": stderr,
                            "returncode": result.returncode,
                            "parse_error": "JSON解析失败",
                        })
                else:
                    return tool_result(data={
                        "stdout": stdout,
                        "stderr": stderr,
                        "returncode": result.returncode,
                    })

            except subprocess.TimeoutExpired:
                return tool_error(
                    code="TIMEOUT",
                    message=f"命令执行超时 (timeout={timeout}s)",
                    details={"timeout": timeout},
                )
            except Exception as e:
                return tool_error(
                    code="EXECUTION_ERROR",
                    message=f"命令执行失败: {str(e)}",
                )

        return handler
