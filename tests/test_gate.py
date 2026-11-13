from claude_dev_pipeline.github_gate import JIRA_APPROVED_LABEL, admit_issue


def test_github_label_must_be_applied_by_the_owner():
    missing = admit_issue(labels=[], timeline=[])
    assert missing.allowed is False
    stranger = admit_issue(
        labels=["agent:approved"],
        timeline=[{"event": "labeled", "label": "agent:approved", "actor": "octocat"}],
    )
    assert stranger.allowed is False
    owner = admit_issue(
        labels=["agent:approved"],
        timeline=[
            {"event": "labeled", "label": "agent:approved", "actor": "octocat"},
            {"event": "labeled", "label": "agent:approved", "actor": "EladAriel"},
        ],
    )
    assert owner.allowed is True


def test_jira_label_uses_the_hyphenated_name():
    decision = admit_issue(
        labels=[JIRA_APPROVED_LABEL],
        timeline=[{"event": "labeled", "label": JIRA_APPROVED_LABEL, "actor": "EladAriel"}],
        label=JIRA_APPROVED_LABEL,
    )
    assert decision.allowed is True
