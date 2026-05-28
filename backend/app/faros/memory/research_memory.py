from __future__ import annotations

from typing import Any, Dict, Iterable


class ResearchMemory:
    """A mutable runtime memory envelope backed by the FAROS state store."""

    ENVELOPE_KEY = '_farosMemoryEnvelope'

    def __init__(self, state_store, run_id: str, policy: Dict[str, Any] | None = None):
        self.state_store = state_store
        self.run_id = run_id
        self.policy = policy or {}
        raw = state_store.get_memory(run_id)
        self._envelope = self._normalize(raw)
        self._envelope['policy'].update(self.policy)
        self.compact(persist=False)
        self._persist()

    @property
    def data(self) -> Dict[str, Any]:
        return self._envelope['data']

    @property
    def summary(self) -> Dict[str, Any]:
        return self._envelope['summary']

    @property
    def scopes(self) -> Dict[str, Any]:
        return self._envelope['scopes']

    @property
    def history(self) -> list[Dict[str, Any]]:
        return self._envelope['history']

    @property
    def archives(self) -> Dict[str, Any]:
        return self._envelope['archives']

    def update(self, key: str, value: Any, scope: str = 'run') -> None:
        self._envelope['data'][key] = value
        self._mark_scope(scope, [key])
        self.compact()

    def merge(self, payload: Dict[str, Any], scope: str = 'run') -> None:
        self._envelope['data'].update(payload)
        self._mark_scope(scope, payload.keys())
        self.compact()

    def record_step(self, node_id: str, outputs: Dict[str, Any]) -> None:
        history = self._envelope['history']
        history.append({
            'nodeId': node_id,
            'keys': sorted(outputs.keys()),
        })
        self._mark_scope(node_id, outputs.keys())
        self.compact()

    def compact(self, persist: bool = True) -> None:
        self._apply_scope_compaction()
        self._apply_scope_retention()
        self._apply_volatile_cleanup()
        self._trim_history()
        self._refresh_summary()
        self._refresh_run_scope()
        if persist:
            self._persist()

    def snapshot(self) -> Dict[str, Any]:
        return {
            'data': dict(self._envelope['data']),
            'summary': dict(self._envelope['summary']),
            'scopes': dict(self._envelope['scopes']),
            'history': list(self._envelope['history']),
            'archives': dict(self._envelope['archives']),
            'policy': dict(self._envelope['policy']),
        }

    def query(
        self,
        *,
        keys: list[str] | None = None,
        scopes: list[str] | None = None,
        include_archives: bool = False,
        include_summary: bool = True,
        include_history: bool = False,
        history_limit: int | None = None,
    ) -> Dict[str, Any]:
        key_filter = set(keys or [])
        scope_filter = list(scopes or [])
        scoped_keys = set()
        if scope_filter:
            for scope in scope_filter:
                scoped_keys.update(self._envelope['scopes'].get(scope, []))
        else:
            scoped_keys.update(self._envelope['data'].keys())
        if key_filter:
            scoped_keys &= key_filter

        data = {key: self._envelope['data'][key] for key in sorted(scoped_keys) if key in self._envelope['data']}
        summary = dict(self._envelope['summary']) if include_summary else {}
        if key_filter:
            summary = {key: value for key, value in summary.items() if key in key_filter}
        archives = {}
        if include_archives:
            archive_scope_filter = set(scope_filter) if scope_filter else None
            for scope_name, payload in self._envelope['archives'].items():
                if archive_scope_filter is not None and scope_name not in archive_scope_filter:
                    continue
                archive_data = dict(payload.get('data', {}))
                if key_filter:
                    archive_data = {key: value for key, value in archive_data.items() if key in key_filter}
                archives[scope_name] = {
                    'keys': [key for key in payload.get('keys', []) if not key_filter or key in key_filter],
                    'data': archive_data,
                }
        history = []
        if include_history:
            history = list(self._envelope['history'])
            if scope_filter:
                scope_set = set(scope_filter)
                history = [item for item in history if item.get('nodeId') in scope_set or 'run' in scope_set]
            if key_filter:
                history = [
                    {**item, 'keys': [key for key in item.get('keys', []) if key in key_filter]}
                    for item in history
                    if any(key in key_filter for key in item.get('keys', []))
                ]
            if history_limit is not None and history_limit >= 0:
                history = history[-history_limit:] if history_limit else []

        return {
            'data': data,
            'summary': summary,
            'scopes': {scope: list(keys) for scope, keys in self._envelope['scopes'].items() if not scope_filter or scope in scope_filter or scope == 'run'},
            'archives': archives,
            'history': history,
            'policy': dict(self._envelope['policy']),
        }

    def recall(self, scope: str, *, merge_into_run: bool = False, persist: bool = True) -> Dict[str, Any]:
        archive = self._envelope['archives'].get(scope)
        if not archive:
            raise KeyError(f"memory archive scope '{scope}' not found")
        payload = dict(archive.get('data', {}))
        self._envelope['data'].update(payload)
        self._mark_scope(scope, payload.keys())
        if merge_into_run:
            self._mark_scope('run', payload.keys())
        self._refresh_summary()
        self._refresh_run_scope()
        if persist:
            self._persist()
        return {
            'scope': scope,
            'restoredKeys': sorted(payload.keys()),
            'mergeIntoRun': merge_into_run,
        }

    def _normalize(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        if raw.get(self.ENVELOPE_KEY):
            raw.setdefault('data', {})
            raw.setdefault('summary', {})
            raw.setdefault('scopes', {'run': []})
            raw.setdefault('history', [])
            raw.setdefault('archives', {})
            raw.setdefault('policy', {})
            return raw
        return {
            self.ENVELOPE_KEY: True,
            'data': dict(raw),
            'summary': {},
            'scopes': {'run': sorted(raw.keys()) if raw else []},
            'history': [],
            'archives': {},
            'policy': {},
        }

    def _mark_scope(self, scope: str, keys: Iterable[str]) -> None:
        scopes = self._envelope['scopes']
        existing = set(scopes.get(scope, []))
        existing.update(list(keys))
        scopes[scope] = sorted(existing)
        if scope != 'run' and self._envelope['policy'].get('scope_strategy') == 'node':
            run_keys = set(scopes.get('run', []))
            run_keys.update(list(keys))
            scopes['run'] = sorted(run_keys)

    def _refresh_summary(self) -> None:
        summary_keys = list(self._envelope['policy'].get('summary_keys', []) or [])
        summary = {}
        for key in summary_keys:
            if key in self._envelope['data']:
                summary[key] = self._envelope['data'][key]
        self._envelope['summary'] = summary

    def _trim_history(self) -> None:
        history = self._envelope['history']
        max_entries = int(self._envelope['policy'].get('max_history_entries', 32) or 32)
        if len(history) > max_entries:
            del history[:-max_entries]

    def _apply_volatile_cleanup(self) -> None:
        prefixes = tuple(self._envelope['policy'].get('volatile_prefixes', []) or [])
        if not prefixes:
            return
        keys_to_remove = [key for key in list(self._envelope['data'].keys()) if key.startswith(prefixes)]
        for key in keys_to_remove:
            self._envelope['data'].pop(key, None)
        if keys_to_remove:
            self._remove_keys_from_scopes(keys_to_remove)

    def _apply_scope_compaction(self) -> None:
        mode = self._envelope['policy'].get('compaction_mode', 'summary_only')
        if mode != 'archive_nonretained':
            return

        scopes = self._envelope['scopes']
        archives = self._envelope['archives']
        retained = set(self._envelope['policy'].get('retained_scopes', []) or ['run'])
        summary_keys = set(self._envelope['policy'].get('summary_keys', []) or [])
        retained_non_run_keys = set()
        for scope_name, scope_keys in scopes.items():
            if scope_name == 'run':
                continue
            if scope_name in retained:
                retained_non_run_keys.update(scope_keys)

        archived_keys = []
        for scope_name in list(scopes.keys()):
            if scope_name == 'run' or scope_name in retained:
                continue
            scope_keys = list(scopes.get(scope_name, []))
            scope_payload = {key: self._envelope['data'][key] for key in scope_keys if key in self._envelope['data']}
            if scope_payload:
                archives[scope_name] = {
                    'keys': sorted(scope_payload.keys()),
                    'data': scope_payload,
                }
            if self._envelope['policy'].get('remove_archived_keys', False):
                for key in scope_keys:
                    if key in retained_non_run_keys or key in summary_keys:
                        continue
                    if key in self._envelope['data']:
                        archived_keys.append(key)
                        self._envelope['data'].pop(key, None)
        if archived_keys:
            self._remove_keys_from_scopes(archived_keys)

    def _apply_scope_retention(self) -> None:
        retained = set(self._envelope['policy'].get('retained_scopes', []) or ['run'])
        scopes = self._envelope['scopes']
        if self._envelope['policy'].get('scope_strategy') != 'compact':
            return
        for scope in list(scopes.keys()):
            if scope != 'run' and scope not in retained:
                del scopes[scope]

    def _refresh_run_scope(self) -> None:
        self._envelope['scopes']['run'] = sorted(self._envelope['data'].keys())

    def _remove_keys_from_scopes(self, keys: Iterable[str]) -> None:
        key_set = set(keys)
        for scope, scope_keys in list(self._envelope['scopes'].items()):
            self._envelope['scopes'][scope] = [key for key in scope_keys if key not in key_set]

    def _persist(self) -> None:
        self.state_store.save_memory(self.run_id, self._envelope)
