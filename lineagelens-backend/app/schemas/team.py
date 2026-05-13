from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


TeamRole = Literal["admin", "member"]


class TeamMemberStats(BaseModel):
    id: str
    username: str
    role: str
    record_count: int = Field(alias="recordCount")
    net_added_lines: int = Field(default=0, alias="netAddedLines")
    joined_at_iso: str | None = Field(alias="joinedAtIso")

    model_config = ConfigDict(populate_by_name=True, by_alias=True)


class TeamMembersResponse(BaseModel):
    workspace_id: str = Field(alias="workspaceId")
    members: list[TeamMemberStats]

    model_config = ConfigDict(populate_by_name=True, by_alias=True)


class InviteMemberRequest(BaseModel):
    username: str
    password: str
    role: TeamRole = Field(default="member")

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class InviteMemberResponse(BaseModel):
    id: str
    username: str
    workspace_id: str = Field(alias="workspaceId")
    role: TeamRole

    model_config = ConfigDict(populate_by_name=True, by_alias=True)
