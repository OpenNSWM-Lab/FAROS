import re
from typing import Any, Dict, List, Tuple

from app.faros.errors import FarosConflictError
from app.faros.registry.package_lifecycle import compare_semver

CONSTRAINT_RE = re.compile(r"^(>=|<=|>|<|==)?\s*(\d+\.\d+\.\d+)$")
ALLOWED_TARGETS = {"profiles", "agents", "skills", "blueprints", "verifiers"}
SINGULAR = {
    'profiles': 'Profile',
    'agents': 'Agent',
    'skills': 'Skill',
    'blueprints': 'Blueprint',
    'verifiers': 'Verifier',
}


def validate_compatibility_map(compatibility: Dict[str, Dict[str, str]], subject: str) -> None:
    for target_type, requirements in compatibility.items():
        if target_type not in ALLOWED_TARGETS:
            raise ValueError(f"{subject} has unsupported compatibility target '{target_type}'")
        if not isinstance(requirements, dict):
            raise ValueError(f"{subject} compatibility target '{target_type}' must be a mapping")
        for target_id, constraint in requirements.items():
            if not isinstance(target_id, str) or not target_id.strip():
                raise ValueError(f"{subject} compatibility target ids under '{target_type}' must be non-empty strings")
            if not isinstance(constraint, str) or not constraint.strip():
                raise ValueError(f"{subject} compatibility constraint for '{target_type}:{target_id}' must be a non-empty string")
            if not CONSTRAINT_RE.match(constraint.strip()):
                raise ValueError(
                    f"{subject} compatibility constraint '{constraint}' for '{target_type}:{target_id}' is invalid; use semver or operators like >=1.2.3"
                )


def parse_constraint(constraint: str) -> Tuple[str, str]:
    match = CONSTRAINT_RE.match(constraint.strip())
    if not match:
        raise ValueError(f"Invalid compatibility constraint '{constraint}'")
    operator, required = match.groups()
    return operator or '==', required


def satisfies_constraint(version: str, constraint: str) -> bool:
    operator, required = parse_constraint(constraint)
    cmp = compare_semver(version, required)
    if operator == '==':
        return cmp == 0
    if operator == '>=':
        return cmp >= 0
    if operator == '<=':
        return cmp <= 0
    if operator == '>':
        return cmp > 0
    if operator == '<':
        return cmp < 0
    raise ValueError(f"Unsupported compatibility operator '{operator}'")


def enforce_compatibility(
    subject: str,
    compatibility: Dict[str, Dict[str, str]],
    available: Dict[str, Dict[str, str]],
) -> None:
    validate_compatibility_map(compatibility, subject)
    for issue in collect_compatibility_issues(subject, compatibility, available):
        raise FarosConflictError(issue['message'], error_code='package_compatibility_conflict', category='package')



def build_available_versions(packages: Dict[str, list]) -> Dict[str, Dict[str, str]]:
    available: Dict[str, Dict[str, str]] = {}
    for package_type in ALLOWED_TARGETS:
        entries = packages.get(package_type, [])
        available[package_type] = {
            entry.id: getattr(entry, 'version', '0.0.0')
            for entry in entries
        }
    return available



def build_version_catalog(packages: Dict[str, list]) -> Dict[str, Dict[str, List[str]]]:
    catalog: Dict[str, Dict[str, List[str]]] = {}
    for package_type in ALLOWED_TARGETS:
        entries = packages.get(package_type, [])
        typed_catalog: Dict[str, List[str]] = {}
        for entry in entries:
            typed_catalog.setdefault(entry.id, []).append(getattr(entry, 'version', '0.0.0'))
        catalog[package_type] = {
            package_id: sorted(set(versions), key=lambda item: tuple(int(part) for part in item.split('.')))
            for package_id, versions in typed_catalog.items()
        }
    return catalog



def collect_compatibility_issues(
    subject: str,
    compatibility: Dict[str, Dict[str, str]],
    available: Dict[str, Dict[str, str]],
) -> List[Dict[str, Any]]:
    validate_compatibility_map(compatibility, subject)
    issues: List[Dict[str, Any]] = []
    for target_type, requirements in compatibility.items():
        available_versions = available.get(target_type, {})
        for target_id, constraint in requirements.items():
            if target_id not in available_versions:
                issues.append({
                    'subject': subject,
                    'targetType': target_type,
                    'targetId': target_id,
                    'constraint': constraint,
                    'installedVersion': None,
                    'status': 'missing',
                    'message': f"{subject} requires {target_type[:-1]} '{target_id}' satisfying '{constraint}', but it is not installed",
                })
                continue
            version = available_versions[target_id]
            if not satisfies_constraint(version, constraint):
                issues.append({
                    'subject': subject,
                    'targetType': target_type,
                    'targetId': target_id,
                    'constraint': constraint,
                    'installedVersion': version,
                    'status': 'version_mismatch',
                    'message': f"{subject} requires {target_type[:-1]} '{target_id}' satisfying '{constraint}', but installed version is {version}",
                })
    return issues


def _subject_label(package_type: str, entry: Any) -> str:
    label = SINGULAR.get(package_type, package_type[:-1].capitalize())
    return f"{label} '{entry.id}'"



def _is_stronger_lower(candidate: Dict[str, Any], current: Dict[str, Any] | None) -> bool:
    if current is None:
        return True
    cmp = compare_semver(candidate['version'], current['version'])
    if cmp > 0:
        return True
    if cmp == 0 and candidate['inclusive'] is False and current['inclusive'] is True:
        return True
    return False



def _is_stronger_upper(candidate: Dict[str, Any], current: Dict[str, Any] | None) -> bool:
    if current is None:
        return True
    cmp = compare_semver(candidate['version'], current['version'])
    if cmp < 0:
        return True
    if cmp == 0 and candidate['inclusive'] is False and current['inclusive'] is True:
        return True
    return False



def _summarize_constraint_solver(constraints: List[Dict[str, Any]]) -> Dict[str, Any]:
    lower_bound = None
    upper_bound = None
    exact_versions = set()

    for item in constraints:
        operator, version = parse_constraint(item['constraint'])
        if operator == '==':
            exact_versions.add(version)
            bound = {'version': version, 'inclusive': True, 'operator': operator}
            if _is_stronger_lower(bound, lower_bound):
                lower_bound = bound
            if _is_stronger_upper(bound, upper_bound):
                upper_bound = bound
        elif operator in {'>=', '>'}:
            bound = {'version': version, 'inclusive': operator == '>=', 'operator': operator}
            if _is_stronger_lower(bound, lower_bound):
                lower_bound = bound
        elif operator in {'<=', '<'}:
            bound = {'version': version, 'inclusive': operator == '<=', 'operator': operator}
            if _is_stronger_upper(bound, upper_bound):
                upper_bound = bound

    reasons: List[str] = []
    satisfiable = True

    if len(exact_versions) > 1:
        satisfiable = False
        reasons.append(f"multiple exact version requirements: {sorted(exact_versions)}")

    if lower_bound and upper_bound:
        cmp = compare_semver(lower_bound['version'], upper_bound['version'])
        if cmp > 0:
            satisfiable = False
            reasons.append(
                f"lower bound {lower_bound['operator']}{lower_bound['version']} exceeds upper bound {upper_bound['operator']}{upper_bound['version']}"
            )
        elif cmp == 0 and (not lower_bound['inclusive'] or not upper_bound['inclusive']):
            satisfiable = False
            reasons.append(
                f"bounds collapse to excluded version {lower_bound['version']} via {lower_bound['operator']} and {upper_bound['operator']}"
            )

    if exact_versions:
        exact_version = sorted(exact_versions)[0]
        if lower_bound and not satisfies_constraint(exact_version, f"{lower_bound['operator']}{lower_bound['version']}"):
            satisfiable = False
            reasons.append(f"exact version {exact_version} violates lower bound {lower_bound['operator']}{lower_bound['version']}")
        if upper_bound and not satisfies_constraint(exact_version, f"{upper_bound['operator']}{upper_bound['version']}"):
            satisfiable = False
            reasons.append(f"exact version {exact_version} violates upper bound {upper_bound['operator']}{upper_bound['version']}")
    else:
        exact_version = None

    if exact_version is not None:
        recommended = f"=={exact_version}"
    elif lower_bound and upper_bound and lower_bound['version'] == upper_bound['version'] and lower_bound['inclusive'] and upper_bound['inclusive']:
        recommended = f"=={lower_bound['version']}"
    elif lower_bound and upper_bound:
        recommended = f"{lower_bound['operator']}{lower_bound['version']},{upper_bound['operator']}{upper_bound['version']}"
    elif lower_bound:
        recommended = f"{lower_bound['operator']}{lower_bound['version']}"
    elif upper_bound:
        recommended = f"{upper_bound['operator']}{upper_bound['version']}"
    else:
        recommended = None

    return {
        'satisfiable': satisfiable,
        'exactVersion': exact_version,
        'lowerBound': lower_bound,
        'upperBound': upper_bound,
        'recommendedConstraint': recommended,
        'reasons': reasons,
    }



def _variant_sort_key(version: str) -> Tuple[int, int, int]:
    return tuple(int(part) for part in version.split('.'))


def _build_variant_index(packages: Dict[str, list]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    index: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for package_type in ALLOWED_TARGETS:
        entries = packages.get(package_type, [])
        typed_index: Dict[str, Dict[str, Any]] = {}
        for entry in entries:
            typed_index.setdefault(entry.id, {})[getattr(entry, 'version', '0.0.0')] = entry
        index[package_type] = typed_index
    return index


def _collect_constraints_for_selected(
    selected: Dict[Tuple[str, str], str],
    variant_index: Dict[str, Dict[str, Dict[str, Any]]],
) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    constraints: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for (package_type, package_id), version in selected.items():
        entry = variant_index[package_type][package_id][version]
        compatibility = getattr(entry, 'compatibility', {}) or {}
        for target_type, requirements in compatibility.items():
            for target_id, constraint in requirements.items():
                constraints.setdefault((target_type, target_id), []).append({
                    'subjectType': package_type,
                    'subjectId': package_id,
                    'subjectVersion': version,
                    'constraint': constraint,
                })
    return constraints


def _candidate_versions_for_constraints(
    target_type: str,
    target_id: str,
    constraints: List[Dict[str, Any]],
    variant_index: Dict[str, Dict[str, Dict[str, Any]]],
) -> List[str]:
    versions = list(variant_index.get(target_type, {}).get(target_id, {}).keys())
    versions.sort(key=_variant_sort_key)
    return [
        version
        for version in versions
        if all(satisfies_constraint(version, item['constraint']) for item in constraints)
    ]


def solve_dependency_graph(packages: Dict[str, list]) -> Dict[str, Any]:
    variant_index = _build_variant_index(packages)
    available = build_available_versions(packages)
    all_keys = sorted(
        [(package_type, package_id) for package_type, typed in variant_index.items() for package_id in typed.keys()],
        key=lambda item: (item[0], item[1]),
    )
    stats = {
        'visitedStates': 0,
        'backtracks': 0,
        'propagations': 0,
        'decisions': 0,
    }

    def dedupe(items: List[str]) -> List[str]:
        ordered: List[str] = []
        for item in items:
            if item not in ordered:
                ordered.append(item)
        return ordered

    def initial_domains() -> Dict[Tuple[str, str], List[str]]:
        domains: Dict[Tuple[str, str], List[str]] = {}
        for key in all_keys:
            package_type, package_id = key
            versions = list(variant_index[package_type][package_id].keys())
            versions.sort(key=_variant_sort_key, reverse=True)
            installed = available.get(package_type, {}).get(package_id)
            if installed in versions:
                versions.remove(installed)
                versions.insert(0, installed)
            domains[key] = versions
        return domains

    def build_trace_entry(kind: str, **payload: Any) -> Dict[str, Any]:
        event = {'kind': kind}
        event.update(payload)
        return event

    def propagate(
        selected: Dict[Tuple[str, str], str],
        domains: Dict[Tuple[str, str], List[str]],
        trace: List[Dict[str, Any]],
    ) -> Tuple[bool, Dict[Tuple[str, str], str], Dict[Tuple[str, str], List[str]], Dict[Tuple[str, str], List[Dict[str, Any]]], List[str], List[Dict[str, Any]]]:
        local_selected = dict(selected)
        local_domains = {key: list(values) for key, values in domains.items()}
        local_trace = list(trace)
        reasons: List[str] = []

        while True:
            changed = False
            constraints = _collect_constraints_for_selected(local_selected, variant_index)
            for target_key, target_constraints in constraints.items():
                target_type, target_id = target_key
                existing_domain = local_domains.get(target_key)
                if existing_domain is None:
                    reasons.append(f'missing target {target_type}:{target_id}')
                    local_trace.append(build_trace_entry(
                        'contradiction',
                        targetType=target_type,
                        targetId=target_id,
                        reason='missing_target',
                    ))
                    return False, local_selected, local_domains, constraints, dedupe(reasons), local_trace
                filtered = [
                    version
                    for version in existing_domain
                    if all(satisfies_constraint(version, item['constraint']) for item in target_constraints)
                ]
                if not filtered:
                    reasons.append(f'no available version for {target_type}:{target_id} satisfies current constraints')
                    local_trace.append(build_trace_entry(
                        'contradiction',
                        targetType=target_type,
                        targetId=target_id,
                        constraints=[item['constraint'] for item in target_constraints],
                        reason='empty_domain',
                    ))
                    return False, local_selected, local_domains, constraints, dedupe(reasons), local_trace
                if filtered != existing_domain:
                    local_domains[target_key] = filtered
                    stats['propagations'] += 1
                    changed = True
                    local_trace.append(build_trace_entry(
                        'domain_reduction',
                        targetType=target_type,
                        targetId=target_id,
                        before=existing_domain,
                        after=filtered,
                        constraints=[item['constraint'] for item in target_constraints],
                    ))
                selected_version = local_selected.get(target_key)
                if selected_version is not None and selected_version not in filtered:
                    reasons.append(
                        f'selected version {selected_version} for {target_type}:{target_id} violates accumulated constraints'
                    )
                    local_trace.append(build_trace_entry(
                        'contradiction',
                        targetType=target_type,
                        targetId=target_id,
                        selectedVersion=selected_version,
                        allowedVersions=filtered,
                        reason='selected_version_invalid',
                    ))
                    return False, local_selected, local_domains, constraints, dedupe(reasons), local_trace
                if selected_version is None and len(filtered) == 1:
                    auto_version = filtered[0]
                    local_selected[target_key] = auto_version
                    changed = True
                    local_trace.append(build_trace_entry(
                        'auto_select',
                        targetType=target_type,
                        targetId=target_id,
                        version=auto_version,
                    ))
            if not changed:
                return True, local_selected, local_domains, constraints, dedupe(reasons), local_trace

    def choose_next(
        selected: Dict[Tuple[str, str], str],
        domains: Dict[Tuple[str, str], List[str]],
        constraints: Dict[Tuple[str, str], List[Dict[str, Any]]],
    ) -> Tuple[Tuple[str, str] | None, List[str]]:
        best_key = None
        best_domain: List[str] = []
        best_score = None
        for key in all_keys:
            if key in selected:
                continue
            domain = list(domains.get(key, []))
            if not domain:
                return key, []
            constrained = key in constraints
            score = (0 if constrained else 1, len(domain), key[0], key[1])
            if best_score is None or score < best_score:
                best_score = score
                best_key = key
                best_domain = domain
        return best_key, best_domain

    def backtrack(
        selected: Dict[Tuple[str, str], str],
        domains: Dict[Tuple[str, str], List[str]],
        trace: List[Dict[str, Any]],
    ) -> Tuple[Dict[Tuple[str, str], str] | None, List[str], List[Dict[str, Any]]]:
        stats['visitedStates'] += 1
        ok, propagated_selected, propagated_domains, constraints, reasons, propagated_trace = propagate(selected, domains, trace)
        if not ok:
            stats['backtracks'] += 1
            return None, reasons, propagated_trace
        if len(propagated_selected) == len(all_keys):
            return dict(propagated_selected), [], propagated_trace
        next_key, domain = choose_next(propagated_selected, propagated_domains, constraints)
        if next_key is None:
            return dict(propagated_selected), [], propagated_trace
        if not domain:
            target_type, target_id = next_key
            failure_reason = f'no candidate versions available for {target_type}:{target_id}'
            failed_trace = list(propagated_trace)
            failed_trace.append(build_trace_entry(
                'contradiction',
                targetType=target_type,
                targetId=target_id,
                reason='no_candidates',
            ))
            stats['backtracks'] += 1
            return None, [failure_reason], failed_trace
        failure_reasons: List[str] = []
        last_trace = list(propagated_trace)
        for version in domain:
            stats['decisions'] += 1
            next_selected = dict(propagated_selected)
            next_selected[next_key] = version
            branch_trace = list(propagated_trace)
            branch_trace.append(build_trace_entry(
                'decision',
                targetType=next_key[0],
                targetId=next_key[1],
                version=version,
                candidates=domain,
            ))
            solved, child_reasons, child_trace = backtrack(next_selected, propagated_domains, branch_trace)
            if solved is not None:
                return solved, [], child_trace
            failure_reasons.extend(child_reasons)
            last_trace = child_trace
        stats['backtracks'] += 1
        return None, dedupe(failure_reasons), last_trace

    solution, reasons, trace = backtrack({}, initial_domains(), [])
    if solution is None:
        return {
            'status': 'unsatisfied',
            'selected': [],
            'selection': {package_type: [] for package_type in ALLOWED_TARGETS},
            'changes': [],
            'reasons': reasons,
            'trace': trace,
            'stats': stats,
        }

    selected_items = [
        {
            'packageType': package_type,
            'packageId': package_id,
            'version': version,
            'installedVersion': available.get(package_type, {}).get(package_id),
            'changed': available.get(package_type, {}).get(package_id) != version,
        }
        for (package_type, package_id), version in sorted(solution.items(), key=lambda item: (item[0][0], item[0][1]))
    ]
    selection = {package_type: [] for package_type in ALLOWED_TARGETS}
    changes = []
    for item in selected_items:
        payload = {'id': item['packageId'], 'version': item['version']}
        selection[item['packageType']].append(payload)
        if item['changed']:
            changes.append({
                'packageType': item['packageType'],
                'packageId': item['packageId'],
                'fromVersion': item['installedVersion'],
                'toVersion': item['version'],
            })

    return {
        'status': 'solved',
        'selected': selected_items,
        'selection': selection,
        'changes': changes,
        'reasons': [],
        'trace': trace,
        'stats': stats,
    }


def _build_constraint_summary(
    packages: Dict[str, list],
    available: Dict[str, Dict[str, str]],
    catalog: Dict[str, Dict[str, List[str]]],
) -> Dict[str, Any]:
    grouped: Dict[tuple[str, str], Dict[str, Any]] = {}
    for package_type, entries in packages.items():
        for entry in entries:
            compatibility = getattr(entry, 'compatibility', {}) or {}
            subject = {
                'type': package_type,
                'id': entry.id,
                'version': getattr(entry, 'version', '0.0.0'),
                'label': _subject_label(package_type, entry),
            }
            for target_type, requirements in compatibility.items():
                for target_id, constraint in requirements.items():
                    key = (target_type, target_id)
                    bucket = grouped.setdefault(key, {
                        'targetType': target_type,
                        'targetId': target_id,
                        'installedVersion': available.get(target_type, {}).get(target_id),
                        'constraints': [],
                    })
                    bucket['constraints'].append({
                        'subjectType': subject['type'],
                        'subjectId': subject['id'],
                        'subjectVersion': subject['version'],
                        'subjectLabel': subject['label'],
                        'constraint': constraint,
                        'satisfied': bucket['installedVersion'] is not None and satisfies_constraint(bucket['installedVersion'], constraint),
                    })

    targets = []
    conflicts = []
    unsatisfiable = []
    for (_, _), bucket in sorted(grouped.items()):
        installed = bucket['installedVersion']
        constraints = bucket['constraints']
        all_satisfied = installed is not None and all(item['satisfied'] for item in constraints)
        missing = installed is None
        solver = _summarize_constraint_solver(constraints)
        status = 'missing' if missing else ('satisfied' if all_satisfied else 'conflict')
        if not solver['satisfiable']:
            status = 'unsatisfiable'
        candidate_versions = [
            version
            for version in catalog.get(bucket['targetType'], {}).get(bucket['targetId'], [])
            if all(satisfies_constraint(version, item['constraint']) for item in constraints)
        ]
        target_summary = {
            'targetType': bucket['targetType'],
            'targetId': bucket['targetId'],
            'installedVersion': installed,
            'status': status,
            'constraintCount': len(constraints),
            'constraints': constraints,
            'blockingSubjects': [
                {
                    'subjectType': item['subjectType'],
                    'subjectId': item['subjectId'],
                    'subjectVersion': item['subjectVersion'],
                    'constraint': item['constraint'],
                }
                for item in constraints
                if not item['satisfied']
            ],
            'candidateVersions': candidate_versions,
            'selectedVersion': candidate_versions[-1] if candidate_versions else None,
            'solver': solver,
        }
        targets.append(target_summary)
        if status != 'satisfied':
            conflicts.append(target_summary)
        if not solver['satisfiable']:
            unsatisfiable.append(target_summary)

    return {
        'targets': targets,
        'conflicts': conflicts,
        'unsatisfiable': unsatisfiable,
        'counts': {
            'targets': len(targets),
            'conflicts': len(conflicts),
            'unsatisfiable': len(unsatisfiable),
        },
    }



def build_dependency_report(packages: Dict[str, list]) -> Dict[str, Any]:
    available = build_available_versions(packages)
    catalog = build_version_catalog(packages)
    package_summary: Dict[str, List[Dict[str, Any]]] = {}
    edges: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []

    for package_type in ALLOWED_TARGETS:
        entries = packages.get(package_type, [])
        package_summary[package_type] = [
            {
                'id': entry.id,
                'version': getattr(entry, 'version', '0.0.0'),
                'compatibilityTargets': sorted((getattr(entry, 'compatibility', {}) or {}).keys()),
            }
            for entry in entries
        ]

    for package_type, entries in packages.items():
        for entry in entries:
            subject = _subject_label(package_type, entry)
            compatibility = getattr(entry, 'compatibility', {}) or {}
            validate_compatibility_map(compatibility, subject)
            for target_type, requirements in compatibility.items():
                available_versions = available.get(target_type, {})
                for target_id, constraint in requirements.items():
                    installed_version = available_versions.get(target_id)
                    satisfied = installed_version is not None and satisfies_constraint(installed_version, constraint)
                    edges.append({
                        'subjectType': package_type,
                        'subjectId': entry.id,
                        'subjectVersion': getattr(entry, 'version', '0.0.0'),
                        'targetType': target_type,
                        'targetId': target_id,
                        'constraint': constraint,
                        'installedVersion': installed_version,
                        'status': 'satisfied' if satisfied else ('missing' if installed_version is None else 'version_mismatch'),
                    })
            subject_issues = collect_compatibility_issues(subject, compatibility, available)
            for issue in subject_issues:
                issue.update({
                    'subjectType': package_type,
                    'subjectId': entry.id,
                    'subjectVersion': getattr(entry, 'version', '0.0.0'),
                })
            issues.extend(subject_issues)

    resolution = _build_constraint_summary(packages, available, catalog)
    solution = solve_dependency_graph(packages)
    counts = {
        'packages': sum(len(items) for items in package_summary.values()),
        'edges': len(edges),
        'issues': len(issues),
    }
    status = 'passed' if not issues else 'failed'
    if solution['status'] != 'solved':
        status = 'failed'
    return {
        'status': status,
        'counts': counts,
        'packages': package_summary,
        'edges': edges,
        'issues': issues,
        'resolution': resolution,
        'solution': solution,
    }



def validate_dependency_graph(packages: Dict[str, list]) -> None:
    report = build_dependency_report(packages)
    if report['issues']:
        raise FarosConflictError(report['issues'][0]['message'], error_code='dependency_graph_conflict', category='package')
