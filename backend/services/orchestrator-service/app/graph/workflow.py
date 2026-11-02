"""LangGraph workflow for the validation and autonomous repair loop."""

from collections.abc import Callable
from datetime import datetime, timezone
import inspect
import logging
from time import perf_counter
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from app.graph.nodes import (
	aggregate_findings,
	analyze,
	bump_retry,
	compare_results,
	explain_and_prioritize,
	finalize,
	report_and_recommend,
	reanalyze,
	repair,
	should_retry,
	understand_project,
	validate,
	_analysis_checks,
)
from app.graph.state import WorkflowState

logger = logging.getLogger(__name__)


def _track_stage(stage: str, node: Callable[..., Any]) -> Callable[..., Any]:
	async def tracked(state: WorkflowState, config: RunnableConfig | None = None) -> dict:
		run_id = state.get("run_id") or state.get("analysis_id") or state.get("project_id")
		started = perf_counter()
		started_at = datetime.now(timezone.utc).isoformat()
		logger.info(
			"workflow_node_start run_id=%s node=%s",
			run_id,
			stage,
			extra={"workflow_run_id": run_id, "workflow_node": stage},
		)
		callback = (config or {}).get("configurable", {}).get("on_stage")
		if callable(callback):
			callback(stage, "running")
		try:
			update = node(state)
			if inspect.isawaitable(update):
				update = await update
		except Exception:
			duration_ms = round((perf_counter() - started) * 1000, 2)
			if callable(callback):
				callback(stage, "failed")
			logger.exception(
				"workflow_node_failed run_id=%s node=%s duration_ms=%s",
				run_id,
				stage,
				duration_ms,
				extra={
					"workflow_run_id": run_id,
					"workflow_node": stage,
					"duration_ms": duration_ms,
				},
			)
			raise

		duration_ms = round((perf_counter() - started) * 1000, 2)
		completed_at = datetime.now(timezone.utc).isoformat()
		stage_updates = update.get("stage_history", [])
		stage_status = stage_updates[-1].get("status", "completed") if stage_updates else "completed"
		timed_stage = {
			"stage": stage,
			"status": stage_status,
			"started_at": started_at,
			"completed_at": completed_at,
			"duration_ms": duration_ms,
		}
		if stage_updates:
			stage_updates = [
				*stage_updates[:-1],
				{
					**stage_updates[-1],
					**timed_stage,
				},
			]
		else:
			stage_updates = [*state.get("stage_history", []), timed_stage]
		update = {**update, "stage_history": stage_updates}
		if stage == "finalize" and isinstance(update.get("final_report"), dict):
			update["final_report"] = {
				**update["final_report"],
				"stage_history": stage_updates,
			}
		logger.info(
			"workflow_node_end run_id=%s node=%s status=%s duration_ms=%s",
			run_id,
			stage,
			stage_status,
			duration_ms,
			extra={
				"workflow_run_id": run_id,
				"workflow_node": stage,
				"workflow_node_status": stage_status,
				"duration_ms": duration_ms,
			},
		)
		if callable(callback):
			callback(stage, stage_status)
		return update

	return tracked


def _after_prioritize(state: WorkflowState) -> str:
	checks = _analysis_checks(state.get("project_context"))
	if checks is None or checks["automated_repair"]:
		return "repair"
	return "validate" if checks["final_validation"] else "finalize"


def _after_repair(state: WorkflowState) -> str:
	return "reanalyze"


def _after_reanalyze(state: WorkflowState) -> str:
	checks = _analysis_checks(state.get("project_context"))
	if state.get("repair_attempts"):
		return "validate"
	return "validate" if checks is None or checks["final_validation"] else "compare"


def _after_validation(state: WorkflowState) -> str:
	if should_retry(state) == "retry":
		return "retry"
	if state.get("repair_attempts") and any(
		stage.get("stage") == "reanalyze"
		for stage in state.get("stage_history", [])
	):
		return "compare"
	return "finalize"


def build_workflow():
	"""Build the analysis graph used by the orchestrator service.

	A failed validation is routed through the bounded repair loop. Successful
	validation, or an exhausted retry budget, always reaches final reporting.
	"""
	graph = StateGraph(WorkflowState)

	graph.add_node("understand", _track_stage("understand", understand_project))
	graph.add_node("analyze", _track_stage("analyze", analyze))
	graph.add_node("aggregate", _track_stage("aggregate", aggregate_findings))
	graph.add_node("explain_prioritize", _track_stage("explain_prioritize", explain_and_prioritize))
	graph.add_node("report_recommend", _track_stage("report_recommend", report_and_recommend))
	graph.add_node("repair", _track_stage("repair", repair))
	graph.add_node("validate", _track_stage("validate", validate))
	graph.add_node("retry", _track_stage("retry", bump_retry))
	graph.add_node("reanalyze", _track_stage("reanalyze", reanalyze))
	graph.add_node("compare", _track_stage("compare", compare_results))
	graph.add_node("finalize", _track_stage("finalize", finalize))

	graph.add_edge(START, "understand")
	graph.add_edge("understand", "analyze")
	graph.add_edge("analyze", "aggregate")
	graph.add_edge("aggregate", "explain_prioritize")
	graph.add_edge("explain_prioritize", "report_recommend")
	graph.add_conditional_edges(
		"report_recommend",
		_after_prioritize,
		{"repair": "repair", "validate": "validate", "finalize": "finalize"},
	)
	graph.add_conditional_edges(
		"repair",
		_after_repair,
		{"reanalyze": "reanalyze"},
	)
	graph.add_conditional_edges(
		"reanalyze",
		_after_reanalyze,
		{"validate": "validate", "compare": "compare"},
	)
	graph.add_conditional_edges(
		"validate",
		_after_validation,
		{"retry": "retry", "compare": "compare", "finalize": "finalize"},
	)
	graph.add_edge("retry", "repair")
	graph.add_edge("compare", "finalize")
	graph.add_edge("finalize", END)

	return graph.compile()
