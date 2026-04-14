import asyncio
import tempfile
import unittest
from pathlib import Path

from orchestrator.agents.agent2.knowledge import Agent2
from orchestrator.core.bus import MessageBus, MsgType
from orchestrator.core.registry import AgentRegistry


class TestAgent2Knowledge(unittest.IsolatedAsyncioTestCase):
    async def test_model_recommendation_returns_context_packet(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bus = MessageBus(Path(temp_dir) / "bus.db")
            await bus.init()
            registry = AgentRegistry(bus)
            agent2 = Agent2(bus, registry)

            msg_id = await bus.send(
                "agent1",
                "agent2",
                MsgType.MODEL_RECOMMENDATION,
                {"task_type": "coding", "constraints": {"provider_preference": "google"}},
            )
            msgs = await bus.receive("agent2", msg_types=[MsgType.MODEL_RECOMMENDATION], limit=1)
            await agent2._dispatch(msgs[0])
            await bus.mark_read(msgs[0].id)

            reply = None
            for _ in range(10):
                responses = await bus.receive("agent1", msg_types=[MsgType.MODEL_RECOMMENDATION], limit=5)
                if responses:
                    reply = responses[0]
                    break
                await asyncio.sleep(0.1)

            self.assertIsNotNone(reply)
            self.assertEqual(reply.payload["request_id"], msg_id)
            self.assertIn("context_packet", reply.payload)
            self.assertTrue(reply.payload["context_packet"]["eligible_models"])
            await bus.close()


if __name__ == "__main__":
    unittest.main()
