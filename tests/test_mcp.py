from copy import deepcopy

from tests.conftest import repo_root

from claude_dev_pipeline.config import load_yaml
from claude_dev_pipeline.mcp_policy import validate_mcp_config


def _example() -> dict:
    return load_yaml(repo_root() / "config/pipeline.config.example.yaml")


def test_example_mcp_config_is_off_and_valid():
    config = _example()
    assert validate_mcp_config(config) == []
    for spec in config["mcp_servers"].values():
        assert spec["enabled"] is False
        assert spec["type"] == "url"
    assert config["code_graph"]["enabled"] is False
    assert config["code_graph"]["mcp"] is False
    assert config["integrations"]["code_graph"] == "none"
    pr = config["mcp_servers"]["github_pull_requests"]["stages"]["pr"]
    assert pr["tools"] == ["create_pull_request"]
    assert pr["permission"] == "always_ask"
    assert "merge_pull_request" in config["mcp_servers"]["github_pull_requests"]["deny_tools"]


def test_policy_rejects_merge_stdio_and_confluence_writes():
    config = _example()
    merged = deepcopy(config)
    merged["mcp_servers"]["github_pull_requests"]["stages"]["pr"]["tools"] = ["merge_pull_request"]
    assert any("create_pull_request" in item for item in validate_mcp_config(merged))

    stdio = deepcopy(config)
    stdio["mcp_servers"]["atlassian"]["type"] = "stdio"
    assert any("url" in item for item in validate_mcp_config(stdio))

    confluence = deepcopy(config)
    confluence["mcp_servers"]["atlassian"]["stages"]["pr"]["tools"] = ["createConfluenceContent"]
    assert any("comment and transition" in item for item in validate_mcp_config(confluence))

    graph = deepcopy(config)
    graph["code_graph"]["mcp"] = True
    assert any("sandbox CLI" in item for item in validate_mcp_config(graph))
