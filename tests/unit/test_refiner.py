"""Unit tests for AI refiner."""

from unittest.mock import patch, MagicMock
from testgen.ai.refiner import refine_scripts


def _make_script(pattern="/login → /dashboard"):
    return {
        "flow_pattern": pattern,
        "content": 'import { test } from "@playwright/test";\ntest("login", async () => {});',
        "filename": "login-dashboard.spec.ts",
    }


def _make_flow(pattern="/login → /dashboard"):
    return {
        "canonical_pattern": pattern,
        "significant_actions": [],
        "popularity_score": 50.0,
    }


class TestRefineScripts:
    @patch("testgen.ai.refiner.refine_script")
    def test_returns_refined_scripts(self, mock_refine):
        mock_refine.return_value = "// refined script"

        scripts = [_make_script()]
        flows = [_make_flow()]
        result = refine_scripts(scripts, flows)

        assert len(result) == 1
        assert result[0]["ai_refined"] is True
        assert result[0]["content"] == "// refined script"

    @patch("testgen.ai.refiner.refine_script")
    def test_graceful_failure_returns_original(self, mock_refine):
        mock_refine.side_effect = Exception("API error")

        scripts = [_make_script()]
        flows = [_make_flow()]
        result = refine_scripts(scripts, flows)

        assert len(result) == 1
        assert result[0]["ai_refined"] is False
        # Content should be unchanged (original)
        assert result[0]["content"] == scripts[0]["content"]

    @patch("testgen.ai.refiner.refine_script")
    def test_handles_multiple_scripts(self, mock_refine):
        mock_refine.return_value = "// improved"

        scripts = [_make_script("/a → /b"), _make_script("/c → /d")]
        flows = [_make_flow("/a → /b"), _make_flow("/c → /d")]
        result = refine_scripts(scripts, flows)

        assert len(result) == 2
        assert all(r["ai_refined"] for r in result)

    @patch("testgen.ai.refiner.refine_script")
    def test_passes_checkpoints_to_refine(self, mock_refine):
        mock_refine.return_value = "// with assertions"

        scripts = [_make_script()]
        flows = [_make_flow()]
        checkpoints = {"/login → /dashboard": [{"visible_elements": [{"tag": "H1", "text_content": "Dashboard"}]}]}

        result = refine_scripts(scripts, flows, checkpoints)
        assert result[0]["ai_refined"] is True
        mock_refine.assert_called_once()
        # Verify checkpoints were passed
        call_args = mock_refine.call_args
        assert len(call_args[1].get("checkpoints", call_args[0][2] if len(call_args[0]) > 2 else [])) > 0

    def test_empty_scripts(self):
        result = refine_scripts([], [])
        assert result == []
