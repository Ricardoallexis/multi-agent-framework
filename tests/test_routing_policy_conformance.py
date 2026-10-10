from __future__ import annotations

from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.executors.registry import ExecutorRegistry
from multiagent.routing.executor_capabilities import (
    CapabilityEvidence, ExecutorDescriptor, SupportState, VerifiedOperatorContext,
)
from multiagent.services import ApplicationServices
from multiagent.services.assignment import AssignmentServices


SURFACE = "lab.synthetic/remote-review-v2"


class SyntheticAdapter:
    adapter_id = "review-adapter"
    version = "3"

    def describe(self, config):
        return ExecutorDescriptor(
            executor_id="review-executor", surface=SURFACE, version="2",
            adapter_id=self.adapter_id, adapter_version=self.version, mode="automatic",
            capabilities_available=frozenset(), roles_available=frozenset(),
            permissions_available=frozenset(), permissions_required=frozenset(),
            permissions_consumed=frozenset(), permission_ceiling=frozenset(),
            allowed_data_classes=frozenset({"public"}), execution_modes=frozenset({"AUTO"}),
        )


def _services(tmp_path, *, requirements=(), profile=None, requirement_map=None):
    settings = Settings(_env_file=None, local_dir=tmp_path, worker_enabled=False,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    services = ApplicationServices.create(settings)
    config = {
        "version": 1,
        "profiles": {"review": {
            "version": 1, "enabled": True, "executor_ids": ["review-executor"],
            "permission_ceiling": [], "allowed_data_classes": ["public"],
            **(profile or {}),
        }},
        "executors": {"review-executor": {"surface": SURFACE, "adapter_id": "review-adapter"}},
        "requirement_map": requirement_map or {},
        "data_classifications": {"group:ORQ": "public"},
        "operator_boundary": {
            "operator_id": "ops", "authorized_aliases": ["agent:verified"],
            "claim_actor_alias": "agent:verified", "operator_grants": [],
            "alias_grants": {"agent:verified": []},
        },
    }
    registry = ExecutorRegistry(config, [SyntheticAdapter()])
    services.system.catalog.executor_routing = config
    services.system.catalog.executor_routing_valid = True
    services.system.catalog.executor_routing_hash = "test-config"
    services.assignment = AssignmentServices(services, registry=registry)
    services.tasks.create_group("ORQ", "Authorized", actor="test", authorized=True)
    task = services.tasks.create_task("ORQ", "ORQ-REVIEW", "Synthetic review", actor="test",
                                      requirements=requirements, file_scope=["src"])
    return services, task, registry


def _context(grants=frozenset(), *, alias="agent:verified"):
    return VerifiedOperatorContext("ops", alias, grants=grants,
                                   provenance="synthetic-conformance-provider", verified=True,
                                   trusted_data_class="public", data_class_scope="group:ORQ")


def test_unregistered_surface_id_flows_through_registry_and_http_assignment(tmp_path):
    services, task, registry = _services(tmp_path)
    assert registry.resolve("review-executor") is not None
    assert registry.get("review-executor").surface == SURFACE
    with TestClient(create_app(services=services, operator_context_provider=lambda _: _context()),
                    base_url="http://localhost") as client:
        preview = client.post(f"/api/v1/tasks/{task['id']}/assignment/preview",
                              json={"profile_id": "review"})
        assert preview.status_code == 200, preview.text
        result = preview.json()
        accepted = client.post(f"/api/v1/tasks/{task['id']}/assignment/accept",
                               json={"preview_token": result["preview_token"], "acknowledge": True})
    assert result["decision"]["status"] == "selected"
    assert result["decision"]["selected_executor"] == "review-executor"
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["task"]["state"] == "claimed"


def test_http_accept_uses_current_request_grants_instead_of_preview_authority(tmp_path):
    services, task, registry = _services(tmp_path, profile={"permission_ceiling": ["provider:cloud"]})
    descriptor = registry.get("review-executor")
    # Rebuild a synthetic descriptor whose only eligible path requires the grant.
    class CloudAdapter(SyntheticAdapter):
        def describe(self, config):
            return ExecutorDescriptor(
                executor_id="review-executor", surface=SURFACE, version="2",
                adapter_id=self.adapter_id, adapter_version=self.version, mode="automatic",
                capabilities_available=frozenset(), roles_available=frozenset(),
                permissions_available=frozenset({"provider:cloud"}),
                permissions_required=frozenset({"provider:cloud"}),
                permissions_consumed=frozenset({"provider:cloud"}),
                permission_ceiling=frozenset({"provider:cloud"}),
                allowed_data_classes=frozenset({"public"}), execution_modes=frozenset({"AUTO"}),
            )
    config = services.system.catalog.executor_routing
    config["operator_boundary"].update(
        operator_grants=["provider:cloud"],
        alias_grants={"agent:verified": ["provider:cloud"]},
    )
    registry = ExecutorRegistry(config, [CloudAdapter()])
    services.assignment = AssignmentServices(services, registry=registry)
    contexts = iter((_context({"provider:cloud"}), _context()))
    with TestClient(create_app(services=services, operator_context_provider=lambda _: next(contexts)),
                    base_url="http://localhost") as client:
        preview = client.post(f"/api/v1/tasks/{task['id']}/assignment/preview",
                              json={"profile_id": "review"})
        assert preview.status_code == 200, preview.text
        accepted = client.post(f"/api/v1/tasks/{task['id']}/assignment/accept",
                               json={"preview_token": preview.json()["preview_token"], "acknowledge": True})
    assert preview.json()["decision"]["status"] == "selected"
    assert accepted.status_code in {200, 409}, accepted.text
    assert services.tasks.get_task(task["id"])["state"] == "open"
    assert accepted.status_code == 409 or accepted.json()["decision"]["status"] != "selected"


def test_http_accept_rejects_identity_substitution_between_requests(tmp_path):
    services, task, _ = _services(tmp_path)
    contexts = iter((_context(), _context(alias="agent:other")))
    seen = []

    def provider(request):
        seen.append(request.url.path.rsplit("/", 1)[-1])
        return next(contexts)

    with TestClient(create_app(services=services, operator_context_provider=provider),
                    base_url="http://localhost") as client:
        preview = client.post(f"/api/v1/tasks/{task['id']}/assignment/preview",
                              json={"profile_id": "review"})
        assert preview.status_code == 200, preview.text
        accepted = client.post(f"/api/v1/tasks/{task['id']}/assignment/accept",
                               json={"preview_token": preview.json()["preview_token"], "acknowledge": True})
    assert seen == ["preview", "accept"]
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["decision"]["status"] == "manual"
    assert "operator_context_unverified" in accepted.json()["decision"]["reason_codes"]
    assert services.tasks.get_task(task["id"])["state"] == "open"

def test_canonical_and_request_requirements_remain_jointly_enforced(tmp_path):
    services, task, _ = _services(
        tmp_path, requirements=["canonical-build"],
        requirement_map={
            "canonical-build": {"capabilities": ["compile"], "permissions": [], "roles": []},
            "extra-review": {"capabilities": ["review"], "permissions": [], "roles": []},
        },
    )
    preview = services.assignment.preview(task["id"],
        {"profile_id": "review", "additional_requirements": ["extra-review"]}, _context())
    decision = preview["decision"]
    assert decision["request"]["canonical_requirements"] == ["canonical-build"]
    assert decision["request"]["additional_requirements"] == ["extra-review"]
    assert decision["status"] != "selected"
    assert "capability_missing" in decision["reason_codes"]
