# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Read-only GUI routes for installed offices (VOF-T01)."""
from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import Any, Callable, Literal

import anyio
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..services.errors import INVALID_REQUEST, NOT_FOUND, ServiceError
from ..services.installed import ZIP_UPLOAD_MAX_BYTES

ACTOR, CHANNEL = "user:gui", "gui"
BODY_MAX_BYTES = ZIP_UPLOAD_MAX_BYTES * 4 // 3 + 64 * 1024


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ZipBody(_Body):
    filename: str = Field(default="", max_length=120)
    zip_base64: str
    into: str | None = None
    reimport: str | None = None


class _FolderBody(_Body):
    folder: str = Field(min_length=1)
    into: str | None = None
    reimport: str | None = None


class _StageInstallBody(_Body):
    approval_id: str = Field(min_length=1)


class _ExportBody(_Body):
    area: str | None = None
    include_code: bool = Field(default=False, strict=True)


class _PrivacyLevelBody(_Body):
    area: str | None = None
    sensitivity: Literal["normal", "sensitive"] | None = None
    local_only: bool | None = Field(default=None, strict=True)


class _PrivacyRaiseBody(_PrivacyLevelBody):
    cloud_allowed: bool | None = Field(default=None, strict=True)


class _PrivacyLowerBody(_Body):
    area: str | None = None
    approval_id: str = Field(min_length=1)


class _PrivacyCloudRequestBody(_Body):
    area: str | None = None
    allowed: Literal[True] = True


class _PrivacyCloudBody(_Body):
    area: str | None = None
    approval_id: str = Field(min_length=1)


class _EmptyBody(_Body):
    pass


def _too_large() -> ServiceError:
    return ServiceError(INVALID_REQUEST, f"The .zip is larger than {ZIP_UPLOAD_MAX_BYTES} bytes", status=413,
                        details={"reason": "zip_too_large", "field": "zip", "limit": ZIP_UPLOAD_MAX_BYTES})


async def _read_body(request: Request, model: type[_Body]) -> Any:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > BODY_MAX_BYTES:
        raise _too_large()
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > BODY_MAX_BYTES:
            raise _too_large()
    try:
        return model.model_validate_json(bytes(raw))
    except ValidationError as exc:
        field = ".".join(str(part) for part in exc.errors()[0]["loc"]) if exc.errors() else ""
        raise ServiceError(INVALID_REQUEST, "The request body is not valid", status=422,
                           details={"field": field}) from None


def _decode_zip(text: str) -> bytes:
    try:
        data = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise ServiceError(INVALID_REQUEST, "The file is not a valid .zip", status=422,
                           details={"reason": "zip_invalid", "field": "zip_base64"}) from None
    if len(data) > ZIP_UPLOAD_MAX_BYTES:
        raise _too_large()
    return data


def _for_target(target: str | None, read: Callable[[str], Any], work: Callable[[], Any]) -> Any:
    if target is not None:
        _read_office(target, read)
    return work()


def _read_office(office_id: str, read: Callable[[str], Any]) -> Any:
    try:
        return read(office_id)
    except ServiceError as error:
        if error.details.get("reason") == "office_not_installed":
            raise ServiceError(NOT_FOUND, f"Office not found: {office_id}", status=404,
                               details={"office": office_id}) from error
        raise


def add_office_routes(api: APIRouter, services: Any) -> None:
    @api.get("/offices")
    def list_offices() -> dict[str, Any]:
        offices = services.offices.list()
        return {"offices": [
            {"id": office["id"], "name": office["name"], "state": office["state"],
             "areas": len(office["areas"])}
            for office in offices
        ], "example_available": services.offices.example_available()}

    # MVP-T01: install the example only when the person asks from a checkout.
    @api.post("/offices/example", status_code=201)
    def install_example_office(body: _EmptyBody) -> dict[str, Any]:
        return services.offices.install_example(actor=ACTOR, channel=CHANNEL)

    @api.get("/offices/{office_id}")
    def get_office(office_id: str) -> dict[str, Any]:
        office = _read_office(office_id, services.offices.get)
        requirements = _read_office(office_id, services.offices.requirements)
        return {**office, "requirements": requirements}

    @api.get("/offices/{office_id}/scene")
    def office_scene(office_id: str) -> dict[str, Any]:
        return _read_office(office_id, services.offices.scene)

    # VOF-T02: stage one office card, install it, export it, or remove an office/area.
    @api.post("/offices/stage-zip")
    async def stage_zip(request: Request) -> dict[str, Any]:
        body = await _read_body(request, _ZipBody)
        data = _decode_zip(body.zip_base64)
        filename = Path(body.filename.replace("\\", "/")).name[:120]
        target = body.into or body.reimport
        return await anyio.to_thread.run_sync(lambda: _for_target(
            target, services.offices.get,
            lambda: services.offices.stage_zip(data, filename=filename, into=body.into, reimport=body.reimport)))

    @api.post("/offices/stage-folder")
    def stage_folder(body: _FolderBody) -> dict[str, Any]:
        services.gui_settings.require_advanced_mode("install_folder")
        target = body.into or body.reimport
        return _for_target(
            target, services.offices.get,
            lambda: services.offices.stage_folder(Path(body.folder), into=body.into, reimport=body.reimport))

    @api.get("/offices/stage/{stage_id}")
    def office_stage(stage_id: str) -> dict[str, Any]:
        return services.offices.card(stage_id)

    @api.post("/offices/stage/{stage_id}/install")
    def install_office_stage(stage_id: str, body: _StageInstallBody) -> dict[str, Any]:
        return services.offices.install_staged(stage_id, approval_id=body.approval_id,
                                               actor="user:operator", channel=CHANNEL)

    @api.post("/offices/stage/{stage_id}/discard")
    def discard_office_stage(stage_id: str, body: _EmptyBody) -> dict[str, bool]:
        services.offices.discard_stage(stage_id)
        return {"discarded": True}

    @api.post("/offices/{office_id}/export")
    def export_office(office_id: str, body: _ExportBody) -> dict[str, Any]:
        result = _read_office(
            office_id, lambda target: services.offices.export(
                target, area=body.area, include_code=body.include_code))
        return {"filename": result["filename"], "zip_base64": base64.b64encode(result["data"]).decode("ascii"),
                "manifest": result["manifest"], "warnings": result["warnings"]}

    @api.post("/offices/{office_id}/remove")
    def remove_office(office_id: str, body: _EmptyBody) -> dict[str, Any]:
        return _read_office(office_id, lambda target: services.offices.remove(
            target, actor=ACTOR, channel=CHANNEL))

    @api.post("/offices/{office_id}/areas/{area_id}/remove")
    def remove_office_area(office_id: str, area_id: str, body: _EmptyBody) -> dict[str, Any]:
        return _read_office(office_id, lambda target: services.offices.remove_area(
            target, area_id, actor=ACTOR, channel=CHANNEL))

    # VOF-T04: privacy levels and one-use cloud permission are managed only in this GUI.
    @api.get("/offices/{office_id}/privacy")
    def office_privacy(office_id: str) -> dict[str, Any]:
        return _read_office(office_id, services.offices.privacy)

    @api.post("/offices/{office_id}/privacy/raise")
    def raise_office_privacy(office_id: str, body: _PrivacyRaiseBody) -> dict[str, Any]:
        return _read_office(office_id, lambda target: services.offices.raise_level(
            target, body.area, sensitivity=body.sensitivity, local_only=body.local_only,
            cloud_allowed=body.cloud_allowed,
            actor=ACTOR, channel=CHANNEL))

    @api.post("/offices/{office_id}/privacy/lower-request")
    def request_office_privacy_lowering(office_id: str, body: _PrivacyLevelBody) -> dict[str, str]:
        approval = _read_office(office_id, lambda target: services.offices.request_lowering(
            target, body.area, sensitivity=body.sensitivity, local_only=body.local_only))
        return {"approval_id": approval["id"], "sha256": approval["sha256"]}

    @api.post("/offices/{office_id}/privacy/lower")
    def lower_office_privacy(office_id: str, body: _PrivacyLowerBody) -> dict[str, Any]:
        return _read_office(office_id, lambda target: services.offices.lower_level(
            target, body.area, approval_id=body.approval_id, actor="user:operator", channel=CHANNEL))

    @api.post("/offices/{office_id}/privacy/cloud-request")
    def request_office_cloud_permission(office_id: str, body: _PrivacyCloudRequestBody) -> dict[str, str]:
        return _read_office(office_id, lambda target: services.offices.request_cloud_permission(
            target, body.area))

    @api.post("/offices/{office_id}/privacy/cloud")
    def grant_office_cloud_permission(office_id: str, body: _PrivacyCloudBody) -> dict[str, Any]:
        return _read_office(office_id, lambda target: services.offices.grant_cloud_permission(
            target, body.area, approval_id=body.approval_id, actor="user:operator", channel=CHANNEL))
