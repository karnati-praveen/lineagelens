from __future__ import annotations

"""OpenVEX and OSV Schemas for AI Change Exposure Exchange.

Enables LineageLens to export machine-readable advisories about recalled AI
models, prompt-injection incidents, and quarantined code blocks in standard
formats (OpenVEX v0.2.0 and OSV 1.6.0) consumable by enterprise scanners
(Trivy, Grype, Snyk, and SBOM pipelines).
"""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

OPENVEX_CONTEXT = "https://openvex.dev/ns/v0.2.0"
OSV_SCHEMA_VERSION = "1.6.0"

OpenVexStatus = Literal["not_affected", "affected", "fixed", "under_investigation"]


class OpenVexVulnerability(BaseModel):
    name: str
    description: str | None = None
    aliases: list[str] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class OpenVexStatement(BaseModel):
    vulnerability: OpenVexVulnerability
    products: list[str] = Field(default_factory=list)
    status: OpenVexStatus = "affected"
    justification: str | None = None
    impact_statement: str | None = Field(default=None, alias="impactStatement")
    action_statement: str | None = Field(default=None, alias="actionStatement")
    status_notes: str | None = Field(default=None, alias="statusNotes")
    timestamp: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class OpenVexDocument(BaseModel):
    context: str = Field(default=OPENVEX_CONTEXT, alias="@context")
    id: str = Field(..., alias="@id")
    author: str = "LineageLens AI Change Exposure Exchange"
    role: str = "AI Provenance & Attestation Engine"
    timestamp: str
    version: int = 1
    tooling: str = "LineageLens/1.0"
    statements: list[OpenVexStatement] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class OSVAffectedPackage(BaseModel):
    ecosystem: str = "LineageLens"
    name: str
    purl: str | None = None


class OSVAffectedRangeEvent(BaseModel):
    introduced: str | None = None
    fixed: str | None = None


class OSVAffectedRange(BaseModel):
    type: str = "SEMVER"
    events: list[OSVAffectedRangeEvent] = Field(default_factory=list)


class OSVAffected(BaseModel):
    package: OSVAffectedPackage
    ranges: list[OSVAffectedRange] = Field(default_factory=list)
    versions: list[str] = Field(default_factory=list)


class OSVReference(BaseModel):
    type: str = "ADVISORY"
    url: str


class OSVDocument(BaseModel):
    schema_version: str = Field(default=OSV_SCHEMA_VERSION, alias="schemaVersion")
    id: str
    modified: str
    published: str
    summary: str
    details: str
    affected: list[OSVAffected] = Field(default_factory=list)
    references: list[OSVReference] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)
