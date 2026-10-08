"""
agent_bridge.py
---------------
Thin adapter that connects the FileScheduler to ANY agent framework
(LangChain, CrewAI, custom, etc.)

Usage
-----
from scheduler.agent_bridge import SchedulerAgentBridge, AgentInterface

class MyAgent(AgentInterface):
    async def handle_scan_result(self, result: ScanResult) -> None:
        for f in result.new_files:
            await self.process_file(f)

bridge = SchedulerAgentBridge(scheduler, MyAgent())
bridge.attach()
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional

from scheduler_engine import FileScheduler, ScanResult, ScheduleConfig

logger = logging.getLogger("scheduler.bridge")


class AgentInterface(ABC):
    """
    Implement this in your agent to receive scan events.
    Both sync and async handle_scan_result are supported.
    """

    @abstractmethod
    async def handle_scan_result(self, result: ScanResult) -> None:
        """Called after every successful (or failed) scan."""
        ...

    async def on_scheduler_error(self, error: str, result: ScanResult) -> None:
        """Override to handle scan errors specially."""
        logger.error("[Agent] Scan error: %s", error)


class SchedulerAgentBridge:
    """
    Wires a FileScheduler to an AgentInterface.

    The bridge:
      1. Registers itself as a scan callback on the scheduler.
      2. Forwards ScanResults to the agent's handle_scan_result.
      3. Exposes convenience methods so the agent can control the schedule.
    """

    def __init__(self, scheduler: FileScheduler, agent: AgentInterface):
        self._scheduler = scheduler
        self._agent     = agent
        self._attached  = False

    def attach(self) -> "SchedulerAgentBridge":
        if not self._attached:
            self._scheduler.on_scan_complete(self._dispatch)
            self._attached = True
            logger.info("AgentBridge attached to scheduler %s", self._scheduler.scheduler_id)
        return self

    async def _dispatch(self, result: ScanResult) -> None:
        if result.error:
            await self._agent.on_scheduler_error(result.error, result)
        await self._agent.handle_scan_result(result)

    # ── pass-through controls so agents don't need direct scheduler access ──

    def start(self)   -> None: self._scheduler.start()
    def stop(self)    -> None: self._scheduler.stop()
    def pause(self)   -> None: self._scheduler.pause()
    def resume(self)  -> None: self._scheduler.resume()
    def trigger(self) -> None: self._scheduler.trigger_now()

    def update_schedule(self, config: ScheduleConfig) -> None:
        self._scheduler.update_config(config)


# ─────────────────────────────────────────────
#  Example: minimal agent that logs files
# ─────────────────────────────────────────────

class LoggingAgent(AgentInterface):
    """
    Drop-in example agent — just logs every discovered file.
    Replace with your real agent logic.
    """

    async def handle_scan_result(self, result: ScanResult) -> None:
        logger.info(
            "[LoggingAgent] Scan %s: %d new, %d modified",
            result.scan_id, len(result.new_files), len(result.modified_files),
        )
        for f in result.new_files:
            logger.info("  NEW      %s  (%d bytes)", f.path, f.size_bytes)
        for f in result.modified_files:
            logger.info("  MODIFIED %s  (%d bytes)", f.path, f.size_bytes)
