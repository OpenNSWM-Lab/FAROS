import hashlib
import json

from app.models.plan_package import (
    PlanPackage,
    PlanPackageHandoff,
    PlanPackagePresentation,
)


def _schema_hash(model) -> str:
    payload = json.dumps(
        model.model_json_schema(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def test_plan_package_public_schemas_are_frozen():
    assert _schema_hash(PlanPackage) == (
        "1c8e042cef9b59caaac268a62e38914473e9bf61c3f990e6369e3182f6c4c975"
    )
    assert _schema_hash(PlanPackageHandoff) == (
        "79bb44f881629465e3e1c1c8e62dfad524a7b912ad4a1406f98c2dec9c56db70"
    )
    assert _schema_hash(PlanPackagePresentation) == (
        "6a0cf5271bb04207654a82759c60ea9ff304ddd0acb000d07a42df5dd9563de6"
    )


def test_plan_package_schema_versions_remain_compatible():
    package_schema = PlanPackage.model_json_schema()
    handoff_schema = PlanPackageHandoff.model_json_schema()

    assert package_schema["properties"]["schemaVersion"]["default"] == "plan-package/v4"
    assert handoff_schema["properties"]["schemaVersion"]["default"] == (
        "plan-package-handoff/v1"
    )
