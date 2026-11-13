"""Per-repo MCP policy from design rev 3.

Remote URL servers and vault auth only. Code graph is a sandbox CLI, not MCP.
This module does not open a connection.
"""

from __future__ import annotations

GITHUB_MCP_BASE = "https://api.githubcopilot.com/mcp/"
GITHUB_ISSUES_URL = "https://api.githubcopilot.com/mcp/x/issues/readonly"
GITHUB_PR_URL = "https://api.githubcopilot.com/mcp/x/pull_requests"
ATLASSIAN_URL = "https://mcp.atlassian.com/v2/mcp"

ALLOWED_MCP = {
    "github_issues_readonly": GITHUB_ISSUES_URL,
    "github_pull_requests": GITHUB_PR_URL,
    "atlassian": ATLASSIAN_URL,
}

ATLASSIAN_READ_TOOLS = {
    "getJiraIssue",
    "listJiraIssueComments",
    "searchJiraIssuesUsingJql",
    "getConfluenceContent",
    "searchConfluence",
    "getAccessibleAtlassianResources",
}
ATLASSIAN_PLAN_TOOLS = {"getJiraIssue", "searchJiraIssuesUsingJql"}
ATLASSIAN_WRITE_TOOLS = {"addOrEditJiraIssueComment", "transitionJiraIssue"}
CONFLUENCE_WRITE_TOOLS = {"createConfluenceContent", "updateConfluenceContent"}
BLOCKED_ATLASSIAN_TOOLS = {
    "executeWrite",
    "executeDestructive",
    "executeRead",
    "discover",
    "createJiraIssue",
    "editJiraIssue",
    *CONFLUENCE_WRITE_TOOLS,
}
GITHUB_PR_TOOLS = {"create_pull_request"}
GITHUB_READ_TOOLS = {"issue_read", "search_issues"}
MERGE_TOOLS = {"merge_pull_request", "merge", "update_pull_request"}
SECRET_KEYS = {
    "token",
    "api_key",
    "secret",
    "password",
    "authorization",
    "bearer",
    "credential_value",
}


def _url(value: object) -> str:
    return str(value).rstrip("/")


def validate_mcp_config(config: dict) -> list[str]:
    """Return policy errors. An empty list means the config is allowed."""
    errors: list[str] = []
    _reject_secret_values(config, "config", errors)
    servers = config.get("mcp_servers") or {}
    if not isinstance(servers, dict):
        return ["mcp_servers must be a mapping"]
    unknown = sorted(set(servers) - set(ALLOWED_MCP))
    if unknown:
        errors.append("unknown mcp server " + ", ".join(unknown))
    for name, expected in ALLOWED_MCP.items():
        spec = servers.get(name)
        if spec is None:
            errors.append(f"missing mcp server {name}")
            continue
        _validate_server(name, spec, expected, errors)
    _validate_code_graph(config.get("code_graph") or {}, errors)
    integrations = config.get("integrations") or {}
    if integrations.get("code_graph") not in (None, "none"):
        errors.append(
            "integrations.code_graph must stay none until a later stage enables the CLI pilot"
        )
    return errors


def _validate_server(name: str, spec: object, expected_url: str, errors: list[str]) -> None:
    if not isinstance(spec, dict):
        errors.append(f"{name} must be a mapping")
        return
    if spec.get("type") != "url":
        errors.append(f"{name} type must be url (remote MCP only; no stdio)")
    if "command" in spec or str(spec.get("transport", "")).lower() == "stdio":
        errors.append(f"{name} must not use a local or stdio transport")
    if _url(spec.get("url")) != _url(expected_url):
        errors.append(f"{name} url must be {expected_url}")
    auth = spec.get("auth") or {}
    if not isinstance(auth, dict) or auth.get("type") != "static_bearer":
        errors.append(f"{name} auth type must be static_bearer")
    toolset = spec.get("toolset") or {}
    if toolset.get("default_enabled") is not False:
        errors.append(f"{name} toolset.default_enabled must be false")
    deny = set(spec.get("deny_tools") or [])
    if deny & set(_tool_names(spec)):
        errors.append(f"{name} enables a tool that is on its deny list")
    if name == "github_pull_requests":
        _validate_github_pr(spec, errors)
    elif name == "github_issues_readonly":
        _validate_github_read(spec, errors)
    elif name == "atlassian":
        _validate_atlassian(spec, errors)


def _validate_github_pr(spec: dict, errors: list[str]) -> None:
    if (spec.get("auth") or {}).get("vault") != "vault-github-pr":
        errors.append("github PR MCP must use vault-github-pr")
    if "merge_pull_request" not in set(spec.get("deny_tools") or []):
        errors.append("github PR MCP must deny merge_pull_request")
    stages = spec.get("stages") or {}
    if set(stages) != {"pr"}:
        errors.append("github pull_requests MCP is assigned to the PR stage only")
    pr = stages.get("pr") or {}
    if set(pr.get("tools") or []) != GITHUB_PR_TOOLS:
        errors.append("PR stage GitHub tools must be create_pull_request only")
    if pr.get("permission") != "always_ask" or pr.get("draft") is not True:
        errors.append("create_pull_request must be always_ask and draft")
    if _has_merge(spec):
        errors.append("merge is not an allowed GitHub tool")


def _validate_github_read(spec: dict, errors: list[str]) -> None:
    if (spec.get("auth") or {}).get("vault") != "vault-github-read":
        errors.append("github issues MCP must use vault-github-read")
    stages = spec.get("stages") or {}
    if set(stages) != {"spec"}:
        errors.append("github issues readonly MCP is assigned to spec only")
    spec_stage = stages.get("spec") or {}
    if spec_stage.get("access") != "read":
        errors.append("github issues MCP spec stage must be read")
    if set(spec_stage.get("tools") or []) != GITHUB_READ_TOOLS:
        errors.append("github issues MCP spec tools must be issue_read and search_issues")
    if (spec.get("auth") or {}).get("vault") == "vault-github-pr":
        errors.append("read and write GitHub vaults must differ")


def _validate_atlassian(spec: dict, errors: list[str]) -> None:
    auth = spec.get("auth") or {}
    read_vault = auth.get("read_vault")
    write_vault = auth.get("write_vault")
    if read_vault != "vault-atlassian-read" or write_vault != "vault-atlassian-write":
        errors.append("atlassian needs vault-atlassian-read and vault-atlassian-write")
    if read_vault == write_vault:
        errors.append("atlassian read and write vaults must be different")
    deny = set(spec.get("deny_tools") or [])
    if not {"executeWrite", "executeDestructive"} <= deny:
        errors.append("atlassian must deny executeWrite and executeDestructive")
    if not CONFLUENCE_WRITE_TOOLS <= deny:
        errors.append("atlassian must deny Confluence writes")
    stages = spec.get("stages") or {}
    allowed_stages = {"spec", "plan", "finder-standards", "pr"}
    if set(stages) - allowed_stages:
        errors.append("atlassian stages must be spec, plan, optional finder-standards, or pr")
    for stage_name, stage in stages.items():
        tools = set(stage.get("tools") or [])
        if tools & BLOCKED_ATLASSIAN_TOOLS or tools & MERGE_TOOLS:
            errors.append(f"atlassian stage {stage_name} enables a blocked tool")
        if stage.get("permission") != "always_ask":
            errors.append(f"atlassian stage {stage_name} must be always_ask")
        if stage_name == "pr":
            if stage.get("access") != "write" or stage.get("vault") != "vault-atlassian-write":
                errors.append("atlassian writes use vault-atlassian-write on the PR stage only")
            if tools != ATLASSIAN_WRITE_TOOLS:
                errors.append("atlassian PR tools must be the comment and transition tools only")
        else:
            if stage.get("access") != "read" or stage.get("vault") != "vault-atlassian-read":
                errors.append(f"atlassian stage {stage_name} must be a read with the read vault")
            allowed = ATLASSIAN_PLAN_TOOLS if stage_name == "plan" else ATLASSIAN_READ_TOOLS
            if stage_name == "finder-standards":
                allowed = {"getConfluenceContent"}
            if not tools <= allowed:
                errors.append(f"atlassian stage {stage_name} has a tool outside its read set")
    write_stages = [name for name, stage in stages.items() if stage.get("access") == "write"]
    if write_stages != ["pr"]:
        errors.append("atlassian writes are allowed only on the PR stage")


def _validate_code_graph(spec: object, errors: list[str]) -> None:
    if not isinstance(spec, dict):
        errors.append("code_graph must be a mapping")
        return
    if spec.get("transport") != "sandbox-cli" or spec.get("mcp") is not False:
        errors.append("code graph runs as a sandbox CLI, not as MCP or stdio")
    if spec.get("mode") != "code-only":
        errors.append("code graph mode must be code-only")
    if str(spec.get("transport", "")).lower() == "stdio":
        errors.append("code graph must not use stdio")
    pilots = spec.get("pilots") or {}
    codegraph = pilots.get("codegraph") if isinstance(pilots, dict) else None
    graphify = pilots.get("graphify") if isinstance(pilots, dict) else None
    if not isinstance(codegraph, dict) or codegraph.get("version") != "1.6.2":
        errors.append("codegraph pilot must pin @colbymchenry/codegraph 1.6.2")
    if not isinstance(graphify, dict) or graphify.get("version") != "0.9.108":
        errors.append("graphify pilot must pin graphifyy 0.9.108")


def _tool_names(spec: dict) -> list[str]:
    names: list[str] = []
    for stage in (spec.get("stages") or {}).values():
        names.extend(stage.get("tools") or [])
    return names


def _has_merge(spec: dict) -> bool:
    names = {name.lower() for name in _tool_names(spec)}
    return bool(names & {item.lower() for item in MERGE_TOOLS})


def _reject_secret_values(node: object, path: str, errors: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key.lower() in SECRET_KEYS and isinstance(value, str) and value.strip():
                errors.append(f"{path}.{key} must not carry a secret value")
            else:
                _reject_secret_values(value, f"{path}.{key}", errors)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _reject_secret_values(item, f"{path}[{index}]", errors)
