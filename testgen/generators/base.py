from __future__ import annotations
"""Generator base interface — abstract class for script generators.

All framework-specific generators must extend this interface.
Adding a new framework requires implementing these methods.
"""

from abc import ABC, abstractmethod


class ScriptGenerator(ABC):
    """Abstract base class for test script generators."""

    @abstractmethod
    def generate_script(self, flow: dict, checkpoints: list[dict]) -> str:
        """Generate a test script for a single flow.

        Args:
            flow: Flow dict with canonical_pattern, significant_actions, path_nodes.
            checkpoints: DOM checkpoints associated with this flow's sessions.

        Returns:
            Generated test script content as a string.
        """
        ...

    @abstractmethod
    def generate_config(self, site_domain: str, flows: list[dict] | None = None) -> str:
        """Generate the test framework configuration file.

        Args:
            site_domain: The domain being tested.
            flows: Optional list of flows to extract base URL from.

        Returns:
            Configuration file content as a string.
        """
        ...

    @abstractmethod
    def generate_readme(self, flows: list[dict], site_domain: str,
                        dedup_strategy: str = "full",
                        original_flow_count: int | None = None) -> str:
        """Generate a README for the generated test suite.

        Args:
            flows: List of all generated flows (post-deduplication).
            site_domain: The domain being tested.
            dedup_strategy: The deduplication strategy used.
            original_flow_count: Number of flows before deduplication.

        Returns:
            README content as a string.
        """
        ...

    @property
    @abstractmethod
    def framework_name(self) -> str:
        """Return the framework name (e.g., 'playwright')."""
        ...

    @property
    @abstractmethod
    def file_extension(self) -> str:
        """Return the test file extension (e.g., '.spec.ts')."""
        ...
