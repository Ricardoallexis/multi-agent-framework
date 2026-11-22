# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Token protected routes of the «Paquetes» view (PAQ-T02): install from a ``.zip``, a folder or the example, and remove.

The ``.zip`` travels as JSON with the bytes in base64 (``require_json`` stays as it is: no multipart, no new dependency).
The route reads ``content-length`` before it reads the body and gives 413 above the cap, like ``/api/v1/blobs``. The
actor is always ``user:gui`` and the channel ``gui``: a person pressed the button. Nothing here returns a path.
"""
from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import Any

import anyio
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, ValidationError

from ..services.errors import INVALID_REQUEST, ServiceError
from ..services.installed import SHIPPED_EXAMPLES, ZIP_UPLOAD_MAX_BYTES

ACTOR, CHANNEL = "user:gui", "gui"
BODY_MAX_BYTES = ZIP_UPLOAD_MAX_BYTES * 4 // 3 + 64 * 1024   # the .zip in base64 plus the other fields of the JSON


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ZipBody(_Body):
    name: str
    filename: str = ""
    zip_base64: str
    trust_functions: bool = False


class ReinstallZipBody(_Body):
    filename: str = ""
    zip_base64: str


class FolderBody(_Body):
    name: str
    folder: str
    trust_functions: bool = False


class EmptyBody(_Body):
    pass


def _too_large(limit: int) -> ServiceError:
    return ServiceError(INVALID_REQUEST, f"The .zip is larger than {ZIP_UPLOAD_MAX_BYTES} bytes", status=413,
                        details={"reason": "zip_too_large", "field": "zip", "limit": limit})


async def _read_body(request: Request, model: type[_Body]) -> Any:
    """The JSON body as ``model``, never reading more than ``BODY_MAX_BYTES`` (413 ``zip_too_large`` above it)."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > BODY_MAX_BYTES:
        raise _too_large(ZIP_UPLOAD_MAX_BYTES)
    raw = bytearray()
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > BODY_MAX_BYTES:
            raise _too_large(ZIP_UPLOAD_MAX_BYTES)
    try:
        return model.model_validate_json(bytes(raw))
    except ValidationError as exc:
        field = ".".join(str(part) for part in exc.errors()[0]["loc"]) if exc.errors() else ""
        raise ServiceError(INVALID_REQUEST, "The request body is not valid", status=422,
                           details={"field": field}) from None


def _decode(text: str) -> bytes:
    try:
        data = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise ServiceError(INVALID_REQUEST, "The file is not a valid .zip", status=422,
                           details={"reason": "zip_invalid", "field": "zip_base64"}) from None
    if len(data) > ZIP_UPLOAD_MAX_BYTES:
        raise _too_large(ZIP_UPLOAD_MAX_BYTES)
    return data


def add_package_routes(api: APIRouter, services: Any) -> None:
    @api.get("/packages")
    def list_packages():
        return {"packages": services.installed.list(),
                "example_available": services.installed.example_available(next(iter(SHIPPED_EXAMPLES))),
                "mode": services.gui_settings.mode()}

    @api.post("/packages/zip", status_code=201)
    async def install_zip(request: Request):
        body = await _read_body(request, ZipBody)
        data = _decode(body.zip_base64)

        def work():
            if body.trust_functions:  # the order of L3: trust_functions, install_folder, install_code
                services.gui_settings.require_advanced_mode("trust_functions")
            return services.installed.install_zip(body.name, data, actor=ACTOR, channel=CHANNEL,
                                                  trust_functions=body.trust_functions, filename=body.filename[:120],
                                                  require_advanced_for_code=True)
        return await anyio.to_thread.run_sync(work)

    @api.post("/packages/folder", status_code=201)
    def install_folder(body: FolderBody):
        if body.trust_functions:
            services.gui_settings.require_advanced_mode("trust_functions")
        services.gui_settings.require_advanced_mode("install_folder")
        return services.installed.install_folder(body.name, Path(body.folder), actor=ACTOR, channel=CHANNEL,
                                                 trust_functions=body.trust_functions,
                                                 source_label=Path(body.folder).name)

    @api.post("/packages/example", status_code=201)
    def install_example(body: EmptyBody):
        return services.installed.install_shipped(next(iter(SHIPPED_EXAMPLES)), actor=ACTOR, channel=CHANNEL)

    @api.post("/packages/{name}/remove")
    def remove_package(name: str, body: EmptyBody):
        return services.installed.remove(name, actor=ACTOR, channel=CHANNEL)

    @api.post("/packages/{name}/reinstall-zip")
    async def reinstall_zip(name: str, request: Request):
        body = await _read_body(request, ReinstallZipBody)
        data = _decode(body.zip_base64)
        return await anyio.to_thread.run_sync(lambda: services.installed.reinstall_zip(
            name, data, actor=ACTOR, channel=CHANNEL, require_advanced_for_code=True))
