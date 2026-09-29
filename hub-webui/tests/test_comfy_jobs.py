"""CPU-only regression tests; use the Hub's uv venv, no running servers needed."""
import asyncio
import json
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import comfy_jobs as jobs
from fastapi import FastAPI, HTTPException
import painter_routes as painter
import minimax_h3_routes as h3
import ideogram_routes as ideogram
import faceswap_routes as faceswap

# Fusion reads machine configuration at import time. Never point tests at real
# models; leave all other reads intact (including workflow/schema files).
_read_text = Path.read_text
def _fixture_config(path, *args, **kwargs):
    if path.name == 'hub_config.json':
        return json.dumps({'paths': {'models': str(Path(__file__).parent / 'absent-models')}})
    return _read_text(path, *args, **kwargs)

with patch.object(Path, 'read_text', _fixture_config):
    import fusion_routes as fusion

MODULES = (painter, h3, ideogram, faceswap, fusion)


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertIsNone(jobs._active)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.tasks = []

    async def asyncTearDown(self):
        pending = list(self.tasks)
        for module in MODULES:
            pending.extend(module._bg_tasks)
        for task in pending:
            if not task.done():
                task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.sleep(0)
        self.assertIsNone(jobs._active, 'reservation leaked')
        for module in MODULES:
            module._jobs.clear()
            if hasattr(module, '_active_job'):
                module._active_job = None
        painter._queues.clear()

    async def busy(self, call, owner):
        with self.assertRaises(HTTPException) as caught:
            await call
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn(owner, caught.exception.detail)

    async def hold(self, owner='Painter'):
        gate = asyncio.Event()
        @jobs.exclusive(owner)
        async def launch():
            self.tasks.append(jobs.spawn(gate.wait()))
        await launch()
        return gate

    async def test_one_winner_during_preflight_await(self):
        started, gate = asyncio.Event(), asyncio.Event()
        @jobs.exclusive('Face Swap')
        async def launch():
            started.set()
            await gate.wait()
        task = asyncio.create_task(launch())
        self.tasks.append(task)
        await started.wait()
        await self.busy(launch(), 'Face Swap')
        await self.busy(h3.generate(None), 'Face Swap')
        gate.set()
        await task

    async def test_all_generators_and_llm_mutations_observe_foreign_owner(self):
        gate = await self.hold('Other module')
        calls = [painter._launch({}, {}), painter.adetailer(None),
                 h3.generate(None), h3.segment_generate('c', 's', None),
                 h3.prompt_enrich(None), h3.prompt_rewrite(None), h3.prompt_unload_llm(),
                 ideogram.generate(None), ideogram.make_caption(None),
                 ideogram.refine_caption(None), ideogram.translate_caption(None),
                 ideogram.autoprompt(None), ideogram.unload_llm(), faceswap.run(None),
                 fusion.sets_run('x', None), fusion.explore_generate(None),
                 fusion.explore_draft(None), fusion.explore_confirm(None),
                 fusion.explore_battery(None)]
        for call in calls:
            await self.busy(call, 'Other module')
        gate.set()

    def h3_fixture(self):
        self.stack.enter_context(patch.object(h3, '_models_root', return_value=Path('.')))
        self.stack.enter_context(patch.object(h3.dl, 'missing_specs', return_value=[]))
        self.stack.enter_context(patch.object(h3, '_free_llm_vram', AsyncMock()))
        return h3.GenerateBody(prompt='fixture', unet_name='fake', clip_name='fake',
                              video_vae_name='fake', audio_vae_name='fake')

    async def test_painter_h3_regression_and_handoff_after_completion(self):
        gate, started = asyncio.Event(), asyncio.Event()
        async def render(*args):
            started.set()
            await gate.wait()
            return b'fixture'
        self.stack.enter_context(patch.object(painter._comfy, 'run_workflow', render))
        self.stack.enter_context(patch.object(painter.session, 'set_preview'))
        generate = self.stack.enter_context(patch.object(h3.pipeline, 'generate', AsyncMock(return_value={'id': 'fake'})))
        body = self.h3_fixture()
        await painter._launch({}, {})
        await started.wait()
        await self.busy(h3.generate(body), 'Painter')
        generate.assert_not_awaited()
        gate.set()
        await asyncio.gather(*painter._bg_tasks)
        result = await h3.generate(body)
        await asyncio.gather(*h3._bg_tasks)
        self.assertEqual(h3._jobs[result['job_id']]['status'], 'done')

    async def test_h3_blocks_painter_and_error_releases(self):
        gate, started = asyncio.Event(), asyncio.Event()
        async def fail(*args, **kwargs):
            started.set()
            await gate.wait()
            raise RuntimeError('fixture failure')
        self.stack.enter_context(patch.object(h3.pipeline, 'generate', fail))
        body = self.h3_fixture()
        result = await h3.generate(body)
        await started.wait()
        await self.busy(painter._launch({}, {}), 'MiniMax H3')
        gate.set()
        await asyncio.gather(*h3._bg_tasks)
        self.assertEqual(h3._jobs[result['job_id']]['status'], 'error')
        self.assertIsNone(jobs._active)

    async def test_adetailer_early_return_releases(self):
        self.stack.enter_context(patch.object(painter._comfy, 'probe_node', AsyncMock(return_value=True)))
        request = painter.ADetailerRequest.model_construct(detectors=['not-a-detector'])
        # Worker reads the image before its no-valid-detectors branch.
        request.image_b64 = 'fixture'
        await painter.adetailer(request)
        await asyncio.gather(*painter._bg_tasks)
        self.assertIsNone(jobs._active)

    async def test_other_module_workers_keep_turn_after_http_return(self):
        async def check(module, owner, launch, worker):
            gate, entered = asyncio.Event(), asyncio.Event()
            async def run(*args, **kwargs):
                entered.set()
                await gate.wait()
            with patch.object(module, worker, run):
                await launch()
                await entered.wait()
                await self.busy(painter._launch({}, {}), owner)
                gate.set()
                await asyncio.gather(*module._bg_tasks)
                self.assertIsNone(jobs._active)
                module._jobs.clear()
                if hasattr(module, '_active_job'):
                    module._active_job = None

        body = ideogram.GenerateBody(description='fixture', unet_cond='fake',
                                    unet_uncond='fake', clip_name=ideogram._CLIP_OK[0],
                                    vae_name=ideogram._VAE_OK[0])
        await check(ideogram, 'Ideogram', lambda: ideogram.generate(body), '_run_job')
        self.stack.enter_context(patch.object(faceswap, '_decode_data_uri', return_value=b'fixture'))
        self.stack.enter_context(patch.object(faceswap._comfy, 'health_check', AsyncMock(return_value=True)))
        self.stack.enter_context(patch.object(faceswap.nsfw, 'set_config'))
        swap = faceswap.RunBody(scene='fixture', donor='fixture', use_mask_helper=False)
        await check(faceswap, 'Face Swap', lambda: faceswap.run(swap), '_run_job')
        vs = SimpleNamespace(locked=False, prompts=['fixture'], sampling={'steps': 1})
        self.stack.enter_context(patch.object(fusion, '_vs_or_404', return_value=vs))
        self.stack.enter_context(patch.object(fusion._comfy, 'health_check', AsyncMock(return_value=True)))
        await check(fusion, 'Forge Fusion', lambda: fusion.sets_run('fixture', fusion.SetRunBody()), '_run_set_job')
        self.h3_fixture()
        self.stack.enter_context(patch.object(h3.chains, 'update_segment'))
        segment = h3.SegmentGenerateBody(prompt='fixture')
        await check(h3, 'MiniMax H3', lambda: h3.segment_generate('c', 's', segment), '_run_chain_job')

    async def test_fusion_cancel_releases_only_after_worker_unwinds(self):
        entered, unwind = asyncio.Event(), asyncio.Event()
        async def worker(job):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await unwind.wait()
        @jobs.exclusive('Forge Fusion')
        async def launch():
            job = {'id': 'fixture', 'status': 'running', 'type': 'explore'}
            fusion._jobs['fixture'] = job
            fusion._spawn(worker(job), job)
        self.stack.enter_context(patch.object(fusion._comfy, 'interrupt', AsyncMock()))
        await launch()
        await entered.wait()
        await fusion.job_cancel('fixture')
        await self.busy(h3.generate(None), 'Forge Fusion')
        unwind.set()
        await asyncio.gather(*fusion._bg_tasks, return_exceptions=True)
        self.assertIsNone(jobs._active)

    async def test_invalid_request_releases_without_background_work(self):
        body = self.h3_fixture()
        body.prompt = ''
        with self.assertRaises(HTTPException) as caught:
            await h3.generate(body)
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIsNone(jobs._active)

    async def test_failed_task_creation_releases(self):
        @jobs.exclusive('Fixture')
        async def launch():
            jobs.spawn(asyncio.sleep(0))
        with patch.object(asyncio, 'create_task', side_effect=RuntimeError('cannot schedule')):
            with self.assertRaises(RuntimeError):
                await launch()
        self.assertIsNone(jobs._active)

    async def test_cancel_before_worker_starts_releases(self):
        @jobs.exclusive('Fixture')
        async def launch():
            task = jobs.spawn(asyncio.sleep(60))
            task.cancel()
            self.tasks.append(task)
        await launch()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.assertIsNone(jobs._active)

    async def test_request_cancel_does_not_release_running_worker(self):
        started, gate = asyncio.Event(), asyncio.Event()
        @jobs.exclusive('Fixture')
        async def launch():
            self.tasks.append(jobs.spawn(gate.wait()))
            started.set()
            await asyncio.Event().wait()
        request = asyncio.create_task(launch())
        self.tasks.append(request)
        await started.wait()
        request.cancel()
        await asyncio.gather(request, return_exceptions=True)
        await self.busy(h3.generate(None), 'Fixture')
        gate.set()

    async def test_foreign_and_idle_interrupts_never_reach_backend(self):
        interrupt = self.stack.enter_context(patch.object(painter._comfy, 'interrupt', AsyncMock()))
        with self.assertRaises(HTTPException):
            await painter.interrupt_job()
        gate = await self.hold('MiniMax H3')
        await self.busy(painter.interrupt_job(), 'MiniMax H3')
        await self.busy(faceswap.interrupt(), 'MiniMax H3')
        await self.busy(fusion.job_cancel('stale-job'), 'MiniMax H3')
        interrupt.assert_not_awaited()
        gate.set()

    async def test_control_pins_owner_until_interrupt_finishes(self):
        gate = await self.hold('Painter')
        entered, finish = asyncio.Event(), asyncio.Event()
        async def interrupt():
            entered.set()
            await finish.wait()
        self.stack.enter_context(patch.object(painter._comfy, 'interrupt', interrupt))
        control = asyncio.create_task(painter.interrupt_job())
        self.tasks.append(control)
        await entered.wait()
        gate.set()
        await self.tasks[0]
        await self.busy(h3.generate(None), 'Painter')
        finish.set()
        await control

    async def test_cpu_job_does_not_take_comfy_turn(self):
        task = fusion._spawn(asyncio.sleep(0))
        self.assertIsNone(jobs._active)
        await task

    async def test_fastapi_models_and_http_409(self):
        api = FastAPI()
        for module, router in [(painter, 'painter_router'), (h3, 'minimax_h3_router'),
                               (ideogram, 'ideogram_router'), (faceswap, 'faceswap_router'),
                               (fusion, 'fusion_router')]:
            api.include_router(getattr(module, router))
        schema = api.openapi()
        self.assertIn('requestBody', schema['paths']['/api/minimax-h3/generate']['post'])
        body = self.h3_fixture()
        gate = await self.hold('Painter')
        async def request(method, path, payload=None):
            messages = []
            async def receive():
                return {'type': 'http.request', 'body': json.dumps(payload).encode(), 'more_body': False}
            async def send(message):
                messages.append(message)
            scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
                     'method': method, 'scheme': 'http', 'path': path, 'raw_path': path.encode(),
                     'query_string': b'', 'root_path': '', 'server': ('fixture', 80),
                     'client': ('fixture', 1), 'headers': [(b'content-type', b'application/json')]}
            await asyncio.wait_for(api(scope, receive, send), 5)
            return messages[0]['status'], json.loads(b''.join(m.get('body', b'') for m in messages))
        status, payload = await request('POST', '/api/minimax-h3/generate', body.model_dump())
        self.assertEqual(status, 409)
        self.assertIn('Painter', payload['detail'])
        status, _ = await request('GET', '/api/minimax-h3/jobs/missing')
        self.assertEqual(status, 404)  # reads remain available
        gate.set()


if __name__ == '__main__':
    unittest.main()
