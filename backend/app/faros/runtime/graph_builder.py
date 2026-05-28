from typing import Dict, List

from app.faros.models.blueprint import Blueprint, WorkflowNode
from app.faros.models.execution import StepState


class GraphBuilder:
    """Build dependency-aware execution plans from a blueprint."""

    def build(self, blueprint: Blueprint) -> List[WorkflowNode]:
        return blueprint.workflow

    def dependency_map(self, blueprint: Blueprint) -> Dict[str, Dict[str, List[str]]]:
        mapping = {node.id: {'upstream': [], 'downstream': []} for node in blueprint.workflow}
        for edge in blueprint.edges:
            mapping.setdefault(edge.target, {'upstream': [], 'downstream': []})['upstream'].append(edge.source)
            mapping.setdefault(edge.source, {'upstream': [], 'downstream': []})['downstream'].append(edge.target)
        return mapping

    def initial_step_states(self, blueprint: Blueprint) -> List[StepState]:
        dependencies = self.dependency_map(blueprint)
        return [
            StepState(
                node_id=node.id,
                capability=node.capability,
                agent_id=node.agent,
                skill_ids=list(node.skills),
                status='ready' if not dependencies.get(node.id, {}).get('upstream') else 'blocked',
            )
            for node in self.build(blueprint)
        ]

    def refresh_step_statuses(self, blueprint: Blueprint, step_index: Dict[str, Dict]) -> None:
        dependencies = self.dependency_map(blueprint)
        for node in self.build(blueprint):
            step = step_index[node.id]
            status = step.get('status', 'pending')
            if status in {'completed', 'skipped', 'failed', 'running'}:
                continue
            upstream = dependencies.get(node.id, {}).get('upstream', [])
            if not upstream:
                step['status'] = 'ready'
                continue
            upstream_statuses = [step_index[parent].get('status', 'pending') for parent in upstream]
            if any(parent_status == 'failed' for parent_status in upstream_statuses):
                step['status'] = 'blocked'
            elif all(parent_status in {'completed', 'skipped'} for parent_status in upstream_statuses):
                step['status'] = 'ready'
            else:
                step['status'] = 'blocked'

    def ready_node_ids(self, blueprint: Blueprint, step_index: Dict[str, Dict], policy: str = 'fifo') -> List[str]:
        self.refresh_step_statuses(blueprint, step_index)
        ready = [node.id for node in self.build(blueprint) if step_index[node.id].get('status') == 'ready']
        if policy == 'reverse':
            return list(reversed(ready))
        if policy == 'wave':
            return ready
        return ready
