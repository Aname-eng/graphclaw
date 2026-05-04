import json
import os
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional, List


@dataclass
class GraphInfo:
    id: str
    name: str
    description: str
    status: str
    created_at: str
    task_description: str
    file_path: str
    parent_graph_id: Optional[str]


class GraphRegistry:
    DEFAULT_GRAPH_ID = "default"
    VALID_STATUSES = {"active", "inactive", "completed", "error", "archived"}

    def __init__(self, graphs_dir: str = "./graphs"):
        self.graphs_dir = os.path.abspath(graphs_dir)
        self.registry_path = os.path.join(self.graphs_dir, "registry.json")
        self._graphs: dict[str, GraphInfo] = {}
        self._load_all_graphs()

    def _load_all_graphs(self) -> None:
        if os.path.exists(self.registry_path):
            try:
                with open(self.registry_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for graph_id, graph_dict in data.get("graphs", {}).items():
                    self._graphs[graph_id] = GraphInfo(**graph_dict)
            except (json.JSONDecodeError, TypeError, KeyError):
                self._graphs = {}
        else:
            os.makedirs(self.graphs_dir, exist_ok=True)
            self._ensure_default_graph()

    def _load_graph(self, graph_id: str) -> Optional[GraphInfo]:
        return self._graphs.get(graph_id)

    def _ensure_default_graph(self) -> None:
        if self.DEFAULT_GRAPH_ID not in self._graphs:
            default_graph = GraphInfo(
                id=self.DEFAULT_GRAPH_ID,
                name="default",
                description="Default graph",
                status="active",
                created_at=datetime.now(timezone.utc).isoformat(),
                task_description="Default task",
                file_path=os.path.join(self.graphs_dir, f"{self.DEFAULT_GRAPH_ID}.json"),
                parent_graph_id=None,
            )
            self._graphs[self.DEFAULT_GRAPH_ID] = default_graph
            self._persist_registry()

    def register_graph(
        self,
        name: str,
        description: str,
        task_description: str,
        file_path: str,
        parent_id: Optional[str] = None,
    ) -> str:
        graph_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        graph_info = GraphInfo(
            id=graph_id,
            name=name,
            description=description,
            status="inactive",
            created_at=created_at,
            task_description=task_description,
            file_path=file_path,
            parent_graph_id=parent_id,
        )
        self._graphs[graph_id] = graph_info
        self._persist_registry()
        return graph_id

    def get_graph(self, graph_id: str) -> Optional[GraphInfo]:
        return self._graphs.get(graph_id)

    def get_active_graph(self) -> Optional[GraphInfo]:
        for graph in self._graphs.values():
            if graph.status == "active":
                return graph
        return None

    def get_default_graph(self) -> Optional[GraphInfo]:
        return self._graphs.get(self.DEFAULT_GRAPH_ID)

    def set_active_graph(self, graph_id: str) -> bool:
        if graph_id not in self._graphs:
            return False
        for g in self._graphs.values():
            if g.status == "active":
                g.status = "inactive"
        self._graphs[graph_id].status = "active"
        self._persist_registry()
        return True

    def update_status(self, graph_id: str, status: str) -> None:
        if status not in self.VALID_STATUSES:
            raise ValueError(f"Invalid status: {status}. Must be one of {self.VALID_STATUSES}")
        if graph_id not in self._graphs:
            raise KeyError(f"Graph not found: {graph_id}")
        self._graphs[graph_id].status = status
        self._persist_registry()

    def list_graphs(self) -> List[GraphInfo]:
        return list(self._graphs.values())

    def delete_graph(self, graph_id: str) -> bool:
        if graph_id == self.DEFAULT_GRAPH_ID:
            return False
        if graph_id not in self._graphs:
            return False
        del self._graphs[graph_id]
        self._persist_registry()
        return True

    def _persist_registry(self) -> None:
        os.makedirs(self.graphs_dir, exist_ok=True)
        data = {
            "graphs": {
                graph_id: asdict(graph_info)
                for graph_id, graph_info in self._graphs.items()
            }
        }
        with open(self.registry_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
