"""
ReplayEngine — generic replay engine that feeds ReplaySessions through
NexusAgent's Narrative / Memory / SocialNetwork pipeline without running
the LLM dialogue loop (Step 3).

For each round it executes:
    Step 1  — NarrativeService.select()
    Step 4  — Event creation + narrative update
    Step 5  — Module hooks (MemoryModule → EverMemOS, SocialNetworkModule, …)
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from loguru import logger

from .models import ReplayConfig, ReplayRound, ReplaySession


class ReplayEngine:
    """
    Dataset-agnostic replay engine.

    Usage:
        engine = ReplayEngine(config)
        await engine.setup()
        stats = await engine.replay(sessions)
    """

    def __init__(self, config: ReplayConfig):
        self.config = config
        # Lazy-loaded services and modules
        self._db_client = None
        self._narrative_service = None
        self._event_service = None
        self._session_service = None
        self._event_crud = None
        self._hook_manager = None
        self._module_list: List = []

    # =========================================================================
    # Setup
    # =========================================================================

    async def setup(self, module_list: Optional[List] = None) -> None:
        """
        Initialize DB, services, and modules.

        Args:
            module_list: List of module instances to execute hooks for.
                         If None, defaults to [MemoryModule, SocialNetworkModule].
        """
        from xyz_agent_context.narrative import (
            EventService,
            NarrativeService,
            SessionService,
        )
        from xyz_agent_context.narrative._event_impl.crud import EventCRUD
        from xyz_agent_context.module import HookManager
        from xyz_agent_context.repository import AgentRepository, UserRepository
        from xyz_agent_context.utils.db_factory import get_db_client

        self._db_client = await get_db_client()

        # Ensure user and agent exist
        await self._ensure_user()
        await self._ensure_agent()

        # Services
        self._narrative_service = NarrativeService(self.config.agent_id)
        self._event_service = EventService(self.config.agent_id)
        self._narrative_service.set_event_service(self._event_service)
        self._session_service = SessionService()

        self._event_crud = EventCRUD(self.config.agent_id)
        self._event_crud.set_database_client(self._db_client)

        self._hook_manager = HookManager()

        # Modules
        if module_list is not None:
            self._module_list = module_list
        else:
            self._module_list = await self._build_default_modules()

        logger.info(
            f"ReplayEngine setup complete: agent={self.config.agent_id}, "
            f"user={self.config.user_id}, modules={[type(m).__name__ for m in self._module_list]}"
        )

    async def _ensure_user(self) -> None:
        from xyz_agent_context.repository import UserRepository

        repo = UserRepository(self._db_client)
        existing = await repo.get_user(self.config.user_id)
        if not existing:
            await repo.add_user(
                user_id=self.config.user_id,
                user_type="user",
                display_name=self.config.user_name or self.config.user_id,
            )
            logger.info(f"Created user: {self.config.user_id}")

    async def _ensure_agent(self) -> None:
        from xyz_agent_context.repository import AgentRepository

        repo = AgentRepository(self._db_client)
        existing = await repo.get_agent(self.config.agent_id)
        if not existing:
            await repo.add_agent(
                agent_id=self.config.agent_id,
                agent_name=self.config.agent_name or self.config.agent_id,
                created_by=self.config.user_id,
                agent_description=f"Replay agent ({self.config.agent_name})",
            )
            logger.info(f"Created agent: {self.config.agent_id}")

    async def _build_default_modules(self) -> List:
        """Build default module list: MemoryModule + SocialNetworkModule."""
        from xyz_agent_context.module.memory_module.memory_module import get_memory_module
        from xyz_agent_context.module.social_network_module.social_network_module import (
            SocialNetworkModule,
        )
        from xyz_agent_context.repository import InstanceRepository
        from xyz_agent_context.schema.instance_schema import (
            InstanceStatus,
            ModuleInstanceRecord,
        )
        from xyz_agent_context.module import generate_instance_id

        memory_module = get_memory_module(self.config.agent_id, self.config.user_id)

        # Get or create SocialNetworkModule instance
        inst_repo = InstanceRepository(self._db_client)
        instances = await inst_repo.get_by_agent(
            self.config.agent_id, module_class="SocialNetworkModule"
        )
        if instances:
            social_instance_id = instances[0].instance_id
        else:
            from xyz_agent_context.utils import utc_now

            social_instance_id = generate_instance_id("social")
            record = ModuleInstanceRecord(
                instance_id=social_instance_id,
                module_class="SocialNetworkModule",
                agent_id=self.config.agent_id,
                user_id=None,
                is_public=True,
                status=InstanceStatus.ACTIVE,
                description="SocialNetworkModule for replay",
                keywords=["social", "network"],
                topic_hint="Social network interactions",
                created_at=utc_now(),
            )
            await inst_repo.create_instance(record)
            logger.info(f"Created SocialNetworkModule instance: {social_instance_id}")

        social_module = SocialNetworkModule(
            agent_id=self.config.agent_id,
            user_id=self.config.user_id,
            database_client=self._db_client,
            instance_id=social_instance_id,
        )

        return [memory_module, social_module]

    # =========================================================================
    # Replay
    # =========================================================================

    async def replay(self, sessions: List[ReplaySession]) -> Dict[str, Any]:
        """
        Replay all sessions through the pipeline.

        Returns:
            Stats dict with total_sessions, total_rounds, elapsed_seconds.
        """
        t0 = time.time()
        total_rounds = 0
        dump_records: Optional[List[Dict]] = [] if self.config.dump_file else None

        for session in sessions:
            rounds = await self._replay_session(session, dump_records)
            total_rounds += rounds

        elapsed = time.time() - t0

        stats = {
            "total_sessions": len(sessions),
            "total_rounds": total_rounds,
            "elapsed_seconds": round(elapsed, 1),
        }

        logger.info(
            f"Replay complete: {stats['total_sessions']} sessions, "
            f"{total_rounds} rounds, {elapsed:.1f}s"
        )

        if dump_records and self.config.dump_file:
            dump_path = Path(self.config.dump_file)
            dump_path.parent.mkdir(parents=True, exist_ok=True)
            dump_path.write_text(
                json.dumps(dump_records, indent=2, ensure_ascii=False, default=str)
            )
            logger.info(f"Dumped {len(dump_records)} records to {dump_path}")

        return stats

    async def _replay_session(
        self,
        session: ReplaySession,
        dump_records: Optional[List[Dict]],
    ) -> int:
        """Replay one session. Returns number of rounds processed."""
        logger.info(f"=== Session {session.session_id}: {len(session.rounds)} rounds ===")

        conv_session = await self._session_service.get_or_create_session(
            self.config.user_id, self.config.agent_id
        )
        # Reset session state so narrative selection starts fresh
        conv_session.last_query = None
        conv_session.last_response = None
        conv_session.current_narrative_id = None

        processed = 0

        for ri, rnd in enumerate(session.rounds):
            if not rnd.user_input and not rnd.agent_response:
                continue

            await self._replay_round(
                rnd, ri, conv_session, session, dump_records
            )
            processed += 1

            if self.config.inter_turn_delay > 0 and ri < len(session.rounds) - 1:
                await asyncio.sleep(self.config.inter_turn_delay)

        logger.success(f"=== Session {session.session_id} done: {processed} rounds ===")
        return processed

    async def _replay_round(
        self,
        rnd: ReplayRound,
        round_idx: int,
        conv_session,
        session: ReplaySession,
        dump_records: Optional[List[Dict]],
    ) -> None:
        """Replay a single round through Step 1 → Step 4 → Step 5."""
        from xyz_agent_context.narrative import Event, TriggerType
        from xyz_agent_context.schema import (
            HookAfterExecutionParams,
            HookExecutionContext,
            HookIOData,
            HookExecutionTrace,
            WorkingSource,
        )

        selection_input = rnd.user_input or rnd.agent_response

        # -- Step 1: Narrative selection --
        selection = await self._narrative_service.select(
            self.config.agent_id,
            self.config.user_id,
            selection_input,
            session=conv_session,
        )
        if not selection.narratives:
            logger.warning(f"  Round {round_idx}: no narratives returned, skipping")
            return

        narrative = selection.narratives[0]

        # -- Step 4: Create Event + update narrative --
        now = datetime.now(timezone.utc)
        event = Event(
            id=f"evt_{uuid4().hex[:16]}",
            trigger=TriggerType.CHAT,
            trigger_source=self.config.user_id,
            env_context={"input": rnd.user_input, "timestamp": now.isoformat()},
            module_instances=[],
            event_log=[],
            final_output=rnd.agent_response,
            created_at=now,
            updated_at=now,
            narrative_id=narrative.id,
            agent_id=self.config.agent_id,
            user_id=self.config.user_id,
        )
        await self._event_crud.save(event)

        is_default = getattr(narrative, "is_special", None) == "default"
        await self._narrative_service.update_with_event(
            narrative,
            event,
            is_main_narrative=not is_default,
            is_default_narrative=is_default,
        )

        # -- Step 5: Execute hooks --
        hook_params = HookAfterExecutionParams(
            execution_ctx=HookExecutionContext(
                event_id=event.id,
                agent_id=self.config.agent_id,
                user_id=self.config.user_id,
                working_source=WorkingSource.CHAT,
            ),
            io_data=HookIOData(
                input_content=rnd.user_input,
                final_output=rnd.agent_response,
            ),
            trace=HookExecutionTrace(),
            event=event,
            narrative=narrative,
        )
        try:
            await self._hook_manager.hook_after_event_execution(
                self._module_list, hook_params
            )
        except Exception as exc:
            logger.warning(f"  Hook error (round {round_idx}): {exc}")

        # -- Dump --
        if dump_records is not None:
            dump_records.append({
                "session_id": session.session_id,
                "round_idx": round_idx,
                "user_input": rnd.user_input,
                "agent_response": rnd.agent_response,
                "event_id": event.id,
                "narrative_id": narrative.id,
                "selection_method": selection.selection_method,
                "is_new": selection.is_new,
                "metadata": rnd.metadata,
            })

        # -- Update session --
        conv_session.last_query = rnd.user_input
        conv_session.last_response = rnd.agent_response
        conv_session.current_narrative_id = narrative.id
        conv_session.last_query_time = datetime.now(timezone.utc)
        await self._session_service.save_session(conv_session)
