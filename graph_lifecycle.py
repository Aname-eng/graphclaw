import os
import shutil
from datetime import datetime, timezone, timedelta
from typing import Optional

from graph_registry import GraphRegistry, GraphInfo


class GraphLifecycleManager:
    def __init__(self, registry: GraphRegistry):
        self.registry = registry
        self.archive_dir = os.path.join(registry.graphs_dir, "archive")

    def create_graph(
        self,
        name: str,
        description: str,
        task_description: str,
        file_path: str,
        parent_id: Optional[str] = None,
    ) -> str:
        graph_id = self.registry.register_graph(
            name=name,
            description=description,
            task_description=task_description,
            file_path=file_path,
            parent_id=parent_id,
        )
        return graph_id

    def activate_graph(self, graph_id: str) -> bool:
        if graph_id not in self.registry._graphs:
            return False
        return self.registry.set_active_graph(graph_id)

    def complete_graph(self, graph_id: str) -> bool:
        if graph_id not in self.registry._graphs:
            return False

        graph = self.registry._graphs[graph_id]
        if graph.status == "archived":
            return False

        self.registry.update_status(graph_id, "completed")

        parent_id = graph.parent_graph_id
        if parent_id and parent_id in self.registry._graphs:
            self.registry.set_active_graph(parent_id)
        else:
            default_graph = self.registry.get_default_graph()
            if default_graph:
                self.registry.set_active_graph(default_graph.id)

        return True

    def error_graph(self, graph_id: str, error_msg: str) -> bool:
        if graph_id not in self.registry._graphs:
            return False

        graph = self.registry._graphs[graph_id]
        if graph.status == "archived":
            return False

        self.registry.update_status(graph_id, "error")

        graph_dir = os.path.dirname(graph.file_path)
        error_log_path = os.path.join(graph_dir, f"{graph_id}_error.log")
        os.makedirs(graph_dir, exist_ok=True)
        with open(error_log_path, "w", encoding="utf-8") as f:
            timestamp = datetime.now(timezone.utc).isoformat()
            f.write(f"[{timestamp}] {error_msg}\n")

        return True

    def archive_graph(self, graph_id: str) -> bool:
        if graph_id not in self.registry._graphs:
            return False
        if graph_id == self.registry.DEFAULT_GRAPH_ID:
            return False

        graph = self.registry._graphs[graph_id]
        if graph.status not in {"completed", "error"}:
            return False

        target_dir = os.path.join(self.archive_dir, graph_id)
        os.makedirs(target_dir, exist_ok=True)

        current_dir = os.path.dirname(graph.file_path)
        if os.path.exists(current_dir):
            for item in os.listdir(current_dir):
                src = os.path.join(current_dir, item)
                dst = os.path.join(target_dir, item)
                if os.path.isfile(src):
                    shutil.copy2(src, dst)
                elif os.path.isdir(src):
                    shutil.copytree(src, dst, dirs_exist_ok=True)

        new_file_path = os.path.join(target_dir, os.path.basename(graph.file_path))
        graph.file_path = new_file_path
        graph.status = "archived"
        self.registry._persist_registry()

        return True

    def auto_archive_old_graphs(self, days: int = 7) -> None:
        now = datetime.now(timezone.utc)
        cutoff = timedelta(days=days)

        for graph_id, graph in list(self.registry._graphs.items()):
            if graph_id == self.registry.DEFAULT_GRAPH_ID:
                continue
            if graph.status not in {"completed", "error"}:
                continue

            try:
                created = datetime.fromisoformat(graph.created_at)
                if now - created > cutoff:
                    self.archive_graph(graph_id)
            except (ValueError, TypeError):
                continue

    def get_graph_status(self, graph_id: str) -> str:
        graph = self.registry.get_graph(graph_id)
        if graph is None:
            return "unknown"
        return graph.status

    def can_delete(self, graph_id: str) -> bool:
        graph = self.registry.get_graph(graph_id)
        if graph is None:
            return False
        if graph_id == self.registry.DEFAULT_GRAPH_ID:
            return False
        return graph.status in {"completed", "error", "archived"}
