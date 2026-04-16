"""NexusAgent integration for tau2-bench

This module provides integration between tau2-bench and NexusAgent backend,
allowing tau2 to use NexusAgent as an LLM provider for agent evaluation.
"""

from .nexusagent_backend import NexusAgentClient

__all__ = ["NexusAgentClient"]
