"""HTTP entrypoint for workflow execution and A2A coordination."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import perf_counter

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from app.a2a.dispatcher import A2ADispatcher
from app.config import settings
from app.graph.workflow import build_workflow
from app.persistence.run_store import RunStore
from app.schemas.requests import StartAnalysisRequest
from shared.schemas.a2a import A2ATaskRequest

@asynccontextmanager
async def lifespan(_app: FastAPI):
	recover_interrupted_workflows()
	yield


app = FastAPI(title="Coding Validation Orchestrator", version="1.0.0", lifespan=lifespan)
run_store = RunStore(settings.run_store_path)
records: dict[str, dict] = {}
dispatcher = A2ADispatcher(records)
logger = logging.getLogger(__name__)
app_logger = logging.getLogger("app")
app_logger.setLevel(logging.INFO)
if not app_logger.handlers:
	app_handler = logging.StreamHandler()
	app_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
	app_logger.addHandler(app_handler)
	app_logger.propagate = False
workflow_tasks: dict[str, asyncio.Task[None]] = {}
WORKFLOW_NOT_FOUND = "workflow not found"


def _record(project_id: str) -> dict:
	return records.get(project_id) or run_store.get(project_id) or {}


def _schedule_workflow(request: StartAnalysisRequest, workflow_id: str) -> None:
	task = asyncio.create_task(_run_workflow(request, workflow_id))
	workflow_tasks[workflow_id] = task
	task.add_done_callback(lambda completed: workflow_tasks.pop(workflow_id, None))


async def _run_workflow(request: StartAnalysisRequest, workflow_id: str) -> None:
	project_id = request.project_id
	workflow_started = perf_counter()
	logger.info(
		"workflow_start run_id=%s project_id=%s",
		workflow_id,
		project_id,
		extra={"workflow_run_id": workflow_id, "project_id": project_id},
	)
	record = _record(workflow_id)
	record.update({
		"project_id": project_id,
		"workflow_id": workflow_id,
		"status": "working",
		"progress": "understand",
		"error": None,
	})
	records[workflow_id] = record
	run_store.upsert(workflow_id, record)

	def publish_stage(stage: str, stage_status: str) -> None:
		record.update({
			"project_id": project_id,
			"workflow_id": workflow_id,
			"status": "working",
			"progress": stage,
			"stage_status": stage_status,
		})
		records[workflow_id] = record
		run_store.upsert(workflow_id, record)

	try:
		workflow = build_workflow()
		result = await workflow.ainvoke(
			{
				"project_id": project_id,
				"run_id": workflow_id,
				"project_files": {item.path: item.content for item in request.files},
				"project_context": request.project_context,
				"tools": request.tools,
			},
			config={"configurable": {"on_stage": publish_stage}},
		)
		workflow_duration_seconds = round(perf_counter() - workflow_started, 3)
		result["workflow_duration_seconds"] = workflow_duration_seconds
		final_report = result.get("final_report")
		if isinstance(final_report, dict):
			final_report = {
				**final_report,
				"workflow_duration_seconds": workflow_duration_seconds,
			}
			workflow_summary = final_report.get("workflow_summary")
			if isinstance(workflow_summary, dict):
				final_report["workflow_summary"] = {
					**workflow_summary,
					"workflow_duration_seconds": workflow_duration_seconds,
				}
			result["final_report"] = final_report
		record.update({
			"status": "completed",
			"progress": "finalize",
			"stage_status": "completed",
			"result": result,
			"validation_status": result.get("validation_status", "unknown"),
			"retry_count": result.get("retry_count", 0),
		})
		logger.info(
			"workflow_end run_id=%s status=completed validation_status=%s duration_seconds=%s",
			workflow_id,
			result.get("validation_status", "unknown"),
			workflow_duration_seconds,
			extra={
				"workflow_run_id": workflow_id,
				"workflow_status": "completed",
				"validation_status": result.get("validation_status", "unknown"),
				"workflow_duration_seconds": workflow_duration_seconds,
			},
		)
	except asyncio.CancelledError:
		record.update({
			"status": "cancelled",
			"stage_status": "cancelled",
			"validation_status": "inconclusive",
			"error": "Workflow was cancelled before a final validation result was recorded.",
		})
		records[workflow_id] = record
		run_store.upsert(workflow_id, record)
		raise
	except Exception:  # noqa: BLE001 - convert workflow failure to status
		workflow_duration_seconds = round(perf_counter() - workflow_started, 3)
		logger.exception(
			"workflow_end run_id=%s status=failed node=%s duration_seconds=%s",
			workflow_id,
			record.get("progress"),
			workflow_duration_seconds,
			extra={
				"workflow_run_id": workflow_id,
				"workflow_status": "failed",
				"failed_stage": record.get("progress"),
				"workflow_duration_seconds": workflow_duration_seconds,
			},
		)
		record.update({
			"status": "failed",
			"stage_status": "failed",
			"failed_stage": record.get("progress"),
			"error": "Workflow execution failed. Check configured agent availability and service logs.",
		})
	records[workflow_id] = record
	run_store.upsert(workflow_id, record)


def _has_active_workflow(project_id: str) -> bool:
	active_statuses = {"submitted", "working", "running", "pending"}
	known_records = [*records.values(), *run_store.all()]
	return any(
		record.get("project_id") == project_id
		and record.get("status") in active_statuses
		for record in known_records
	)


def recover_interrupted_workflows() -> int:
	active_statuses = {"submitted", "working", "running", "pending"}
	recovered = 0
	for record in run_store.all():
		if record.get("status") not in active_statuses:
			continue
		workflow_id = record.get("workflow_id") or record.get("run_id") or record.get("project_id")
		record.update({
			"status": "failed",
			"stage_status": "failed",
			"failed_stage": record.get("progress"),
			"error": "Orchestrator restarted before this workflow finished. Start a new analysis to retry.",
		})
		records[workflow_id] = record
		run_store.upsert(workflow_id, record)
		recovered += 1
	if recovered:
		logger.warning("Marked %s interrupted workflow(s) as failed during startup", recovered)
	return recovered


@app.get("/health")
async def health() -> dict:
	return {"status": "ok", "service": settings.service_name}


@app.get("/.well-known/agent-card.json")
async def agent_card() -> dict:
	return {
		"name": "coding-validation-orchestrator",
		"description": "Coordinates code analysis, repair, sandbox validation and reporting.",
		"skills": [
			{"id": "workflow.start", "description": "Start a validation workflow"},
			{"id": "workflow.status", "description": "Read workflow progress and result"},
			{"id": "agent.result", "description": "Receive an agent result"},
		],
	}


@app.post("/workflow/start", responses={409: {"description": "Workflow is already running"}})
async def start_workflow(request: StartAnalysisRequest) -> dict:
	workflow_id = request.workflow_id or request.project_id
	if _record(workflow_id).get("status") in {"submitted", "working"} or _has_active_workflow(request.project_id):
		raise HTTPException(status_code=409, detail="a workflow is already running for this project")
	records[workflow_id] = {
		"project_id": request.project_id,
		"workflow_id": workflow_id,
		"run_id": workflow_id,
		"status": "submitted",
		"progress": "queued",
	}
	run_store.upsert(workflow_id, records[workflow_id])
	_schedule_workflow(request, workflow_id)
	return records[workflow_id]


@app.get("/workflow/status/{workflow_id}", responses={404: {"description": "Workflow not found"}})
async def workflow_status(workflow_id: str) -> dict:
	record = _record(workflow_id)
	if not record:
		raise HTTPException(status_code=404, detail=WORKFLOW_NOT_FOUND)
	return record


@app.post("/workflow/cancel/{workflow_id}", responses={404: {"description": "Workflow not found"}})
async def cancel_workflow(workflow_id: str) -> dict:
	record = _record(workflow_id)
	if not record:
		raise HTTPException(status_code=404, detail=WORKFLOW_NOT_FOUND)
	if record.get("status") in {"completed", "failed", "cancelled"}:
		return record

	task = workflow_tasks.get(workflow_id)
	if task is not None and not task.done():
		task.cancel()
	record.update({
		"status": "cancelled",
		"stage_status": "cancelled",
		"validation_status": "inconclusive",
		"error": "Workflow was cancelled before a final validation result was recorded.",
	})
	records[workflow_id] = record
	run_store.upsert(workflow_id, record)
	return record


@app.get("/workflow/stream/{workflow_id}", responses={404: {"description": "Workflow not found"}})
async def workflow_stream(workflow_id: str) -> StreamingResponse:
	if not _record(workflow_id):
		raise HTTPException(status_code=404, detail=WORKFLOW_NOT_FOUND)

	async def events() -> AsyncIterator[str]:
		last_snapshot = None
		last_heartbeat = asyncio.get_running_loop().time()
		while True:
			current = _record(workflow_id)
			snapshot = json.dumps(current, default=str)
			now = asyncio.get_running_loop().time()
			if snapshot != last_snapshot:
				yield f"data: {snapshot}\n\n"
				last_snapshot = snapshot
				last_heartbeat = now
			elif now - last_heartbeat >= 15:
				yield ": keep-alive\n\n"
				last_heartbeat = now
			if current.get("status") in {"completed", "failed", "cancelled"}:
				break
			await asyncio.sleep(1)

	return StreamingResponse(events(), media_type="text/event-stream")


@app.post("/a2a/v1/tasks", responses={409: {"description": "Workflow is already running"}})
async def a2a_task(task: A2ATaskRequest) -> dict:
	if task.task_type == "workflow.start":
		request = StartAnalysisRequest.model_validate(task.payload)
		workflow_id = request.workflow_id or request.project_id
		if _record(workflow_id).get("status") in {"submitted", "working"} or _has_active_workflow(request.project_id):
			raise HTTPException(status_code=409, detail="a workflow is already running for this project")
		response, request = dispatcher.submit_workflow(task)
		dispatcher.records[response.task_id]["workflow_id"] = workflow_id
		records[workflow_id] = {
			"project_id": request.project_id,
			"workflow_id": workflow_id,
			"run_id": workflow_id,
			"status": "submitted",
			"progress": "queued",
		}
		run_store.upsert(workflow_id, records[workflow_id])
		_schedule_workflow(request, workflow_id)
		return response.model_dump()
	if task.task_type == "workflow.status":
		return dispatcher.status(task).model_dump()
	return dispatcher.agent_result(task).model_dump()
