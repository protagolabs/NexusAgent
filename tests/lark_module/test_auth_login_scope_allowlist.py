"""
@file_name: test_auth_login_scope_allowlist.py
@date: 2026-04-22
@description: Validates that `auth login --scope X` is allowed for
incremental scope top-ups, while bare `auth login` / `auth login
--recommend` remain blocked (must route through lark_permission_advance).
"""

from __future__ import annotations

import pytest

from xyz_agent_context.module.lark_module._lark_command_security import (
    validate_command,
)


class TestAuthLoginBlocked:
    @pytest.mark.parametrize("cmd", [
        "auth login",
        "auth login --domain all",
        "auth login --recommend",
        "auth login --recommend --json --no-wait",
        "auth login --domain all --recommend --json",
    ])
    def test_bare_or_recommend_forms_blocked(self, cmd):
        ok, reason = validate_command(cmd)
        assert ok is False
        assert (
            "lark_permission_advance" in reason
            or "three-click" in reason
            or "reserved" in reason
        )


class TestAuthLoginWithScopeAllowed:
    @pytest.mark.parametrize("cmd", [
        'auth login --scope "im:message:send_as_user"',
        'auth login --scope im:message:send_as_user',
        'auth login --scope "im:message:send_as_bot" --json --no-wait',
        'auth login --scope "contact:user.base:readonly calendar:calendar" --json',
    ])
    def test_scope_targeted_login_allowed(self, cmd):
        ok, reason = validate_command(cmd)
        assert ok, f"expected allow, got block: {reason}"


class TestAuthLoginRecommendWithScopeStillBlocked:
    def test_recommend_plus_scope_is_still_blocked(self):
        """Defense against an Agent trying to sneak --recommend past the gate
        by also adding --scope. --recommend is reserved for initial three-click."""
        ok, reason = validate_command(
            'auth login --scope "im:chat:readonly" --recommend --json'
        )
        assert ok is False
        assert "reserved" in reason.lower() or "recommend" in reason.lower()


class TestOtherAuthSubcommandsUnaffected:
    @pytest.mark.parametrize("cmd", [
        "auth status",
        "auth status --json",
        "auth check",
        "auth scopes",
        "auth list",
    ])
    def test_readonly_subcommands_still_allowed(self, cmd):
        ok, reason = validate_command(cmd)
        assert ok, f"expected allow, got block: {reason}"

    def test_auth_logout_still_blocked(self):
        ok, reason = validate_command("auth logout")
        assert ok is False

    def test_unknown_auth_subcommand_rejected(self):
        ok, reason = validate_command("auth hack")
        assert ok is False
        assert "auth hack" in reason or "not allowed" in reason
