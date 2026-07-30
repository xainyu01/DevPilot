"""Minimal LangGraph state graph and its run lifecycle coordinator."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from packages.contracts import (
    AgentState,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalScope,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    Checkpoint,
    RunContext,
    RunEvent,
    RunEventType,
    RunRequest,
    RunResult,
    RunStatus,
    TokenUsage,
    ToolCall,
    ToolResult,
    ToolResultBlock,
)
from packages.model_gateway import ModelGateway
from packages.model_gateway.errors import (
    AdapterNotImplementedError,
    ModelAdapterError,
    UnsupportedCapabilityError,
)
from packages.tool_runtime import ToolRuntime

from .checkpoints import CheckpointStore

EventSubscriber = Callable[[RunEvent], None]


@dataclass
class _RunControl:
    pause_requested: bool = False
    pause_reason: str = "paused by caller"
    cancel_requested: bool = False

    def guard(self) -> bool:
        if self.cancel_requested:
            return False
        if self.pause_requested:
            interrupt({"kind": "pause", "reason": self.pause_reason})
        return True


class _EventEmitter:
    def __init__(
        self,
        thread_id: str,
        run_id: str,
        event_sink: EventSubscriber | None = None,
    ) -> None:
        self.thread_id = thread_id
        self.run_id = run_id
        self.event_sink = event_sink
        self.sequence = 0
        self.events: list[RunEvent] = []
        self.subscribers: list[asyncio.Queue[RunEvent]] = []

    def emit(
        self,
        event_type: RunEventType,
        *,
        status: RunStatus,
        node: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> RunEvent:
        self.sequence += 1
        event = RunEvent(
            sequence=self.sequence,
            thread_id=self.thread_id,
            run_id=self.run_id,
            type=event_type,
            status=status,
            node=node,
            data=data or {},
        )
        self.events.append(event)
        if self.event_sink is not None:
            self.event_sink(event)
        for subscriber in self.subscribers:
            subscriber.put_nowait(event)
        return event


@dataclass
class _RunHandle:
    request: RunRequest
    context: RunContext
    control: _RunControl = field(default_factory=_RunControl)
    emitter: _EventEmitter = field(init=False)
    task: asyncio.Task[RunResult] | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)
    result: RunResult | None = None
    status: RunStatus = RunStatus.PENDING
    resume_value: Any = None
    event_sink: EventSubscriber | None = None

    def __post_init__(self) -> None:
        self.emitter = _EventEmitter(
            self.request.thread_id,
            self.request.run_id,
            event_sink=self.event_sink,
        )


def _route_after_node(state: AgentState) -> str:
    status = state.get("status", RunStatus.RUNNING)
    if status == RunStatus.CANCELLED:
        return "cancelled"
    if status == RunStatus.FAILED:
        return "failed"
    return "continue"


def _route_after_plan(state: AgentState) -> str:
    result = _route_after_node(state)
    if result != "continue":
        return result
    return "tools" if state.get("tool_calls") else "continue"


def _route_after_model(state: AgentState) -> str:
    result = _route_after_node(state)
    if result != "continue":
        return result
    return "tools" if state.get("tool_calls") else "verify"


def _route_after_verify(state: AgentState) -> str:
    result = _route_after_node(state)
    if result != "continue":
        return result
    verification = state.get("verification", {})
    return "finalize" if verification.get("satisfied") else "model"


def build_agent_graph(
    gateway: ModelGateway,
    *,
    control: _RunControl | None = None,
    emitter: _EventEmitter | None = None,
    checkpointer: InMemorySaver | None = None,
    tool_runtime: ToolRuntime | None = None,
) -> Any:
    """Build and compile the Agent graph with optional B2 tool execution.

    The dependency objects are injected into node closures so the graph remains
    deterministic in unit tests and contains no FastAPI or database imports.
    """

    run_control = control or _RunControl()

    def emit(
        event_type: RunEventType,
        *,
        state: AgentState,
        node: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        if emitter is not None:
            emitter.emit(
                event_type,
                status=state.get("status", RunStatus.RUNNING),
                node=node,
                data=data,
            )

    def guard(state: AgentState) -> dict[str, Any]:
        if not run_control.guard():
            return {"status": RunStatus.CANCELLED, "cancel_requested": True}
        return {}

    async def node_wrapper(
        name: str,
        state: AgentState,
        operation: Callable[[AgentState], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        emit(RunEventType.NODE_STARTED, state=state, node=name)
        update = {**guard(state)}
        if update.get("status") != RunStatus.CANCELLED:
            update.update(await operation(state))
        next_state = {**state, **update}
        emit(RunEventType.NODE_COMPLETED, state=next_state, node=name, data=update)
        return update

    async def load_context(state: AgentState) -> dict[str, Any]:
        return await node_wrapper(
            "load_context",
            state,
            lambda _: _return_update(context_loaded=True, status=RunStatus.RUNNING),
        )

    async def normalize_input(state: AgentState) -> dict[str, Any]:
        async def operation(current: AgentState) -> dict[str, Any]:
            request = _request_from_state(current, tool_runtime)
            try:
                gateway.validate(request)
            except UnsupportedCapabilityError as exc:
                return {
                    "status": RunStatus.FAILED,
                    "error": exc.error,
                    "normalized": False,
                }
            return {"normalized": True, "status": RunStatus.RUNNING}

        return await node_wrapper("normalize_input", state, operation)

    async def plan(state: AgentState) -> dict[str, Any]:
        async def operation(current: AgentState) -> dict[str, Any]:
            plan_items = ["load_context", "normalize_input", "plan"]
            plan_items.extend(
                [
                    "call_model",
                    "route_model_output",
                    "execute_tools",
                    "verify",
                    "finalize",
                ]
            )
            emit(RunEventType.PLAN_CREATED, state=current, node="plan", data={"steps": plan_items})
            return {"plan": plan_items, "status": RunStatus.RUNNING}

        return await node_wrapper("plan", state, operation)

    async def call_model(state: AgentState) -> dict[str, Any]:
        async def operation(current: AgentState) -> dict[str, Any]:
            iteration = int(current.get("iteration", 0))
            max_iterations = int(current.get("max_iterations", 20))
            if iteration >= max_iterations:
                return {
                    "status": RunStatus.FAILED,
                    "stop_reason": "max_iterations_exceeded",
                    "error": {
                        "code": "agent_budget_exceeded",
                        "message": f"model iteration limit {max_iterations} reached",
                    },
                }
            started = float(current.get("started_monotonic", time.monotonic()))
            max_wall_time = float(current.get("max_wall_time_seconds", 900))
            if time.monotonic() - started >= max_wall_time:
                return {
                    "status": RunStatus.FAILED,
                    "stop_reason": "wall_time_exceeded",
                    "error": {
                        "code": "agent_budget_exceeded",
                        "message": f"wall time limit {max_wall_time:g}s reached",
                    },
                }
            request = _request_from_state(current, tool_runtime)
            output: list[str] = []
            final_usage = TokenUsage()
            model_tool_calls = []
            finish_reason = None
            stop_reason = None
            response_metadata: dict[str, Any] = {}
            async for event in gateway.stream(request):
                output.append(event.text)
                if event.usage is not None:
                    final_usage = event.usage
                if event.tool_call_complete and event.tool_call is not None:
                    model_tool_calls.append(event.tool_call)
                finish_reason = event.finish_reason or finish_reason
                stop_reason = event.stop_reason or stop_reason
                response_metadata.update(event.response_metadata)
                if event.text:
                    emit(
                        RunEventType.MODEL_DELTA,
                        state=current,
                        node="call_model",
                        data={"text": event.text, "index": event.index},
                    )
                if run_control.cancel_requested:
                    return {"status": RunStatus.CANCELLED, "cancel_requested": True}
                if run_control.pause_requested:
                    run_control.guard()
            text = "".join(output)
            calls = [
                ToolCall(
                    call_id=call.call_id,
                    name=call.name,
                    arguments=call.arguments,
                )
                for call in model_tool_calls
            ]
            tool_call_count = int(current.get("tool_call_count", 0)) + len(calls)
            max_tool_calls = int(current.get("max_tool_calls", 60))
            if tool_call_count > max_tool_calls:
                return {
                    "status": RunStatus.FAILED,
                    "stop_reason": "max_tool_calls_exceeded",
                    "error": {
                        "code": "agent_budget_exceeded",
                        "message": f"tool call limit {max_tool_calls} exceeded",
                    },
                }
            cumulative_usage = _add_usage(
                TokenUsage.model_validate(current.get("token_usage", {})),
                final_usage,
            )
            max_tokens = int(current.get("max_tokens", 200_000))
            if cumulative_usage.total_tokens > max_tokens:
                return {
                    "status": RunStatus.FAILED,
                    "stop_reason": "max_tokens_exceeded",
                    "token_usage": cumulative_usage,
                    "usage": cumulative_usage,
                    "error": {
                        "code": "agent_budget_exceeded",
                        "message": f"token limit {max_tokens} exceeded",
                    },
                }
            assistant_message = ChatMessage(
                role="assistant",
                content=[{"type": "text", "text": text}],
                tool_calls=model_tool_calls,
            )
            response = ChatResponse(
                provider=request.provider,
                model=request.model,
                message=assistant_message,
                tool_calls=model_tool_calls,
                usage=final_usage,
                finish_reason=finish_reason,
                stop_reason=stop_reason or "unknown",
                response_metadata=response_metadata,
            )
            emit(
                RunEventType.MODEL_OUTPUT,
                state=current,
                node="call_model",
                data={
                    "text": text,
                    "tool_calls": [call.model_dump(mode="json") for call in model_tool_calls],
                    "iteration": iteration + 1,
                    "usage": final_usage.model_dump(mode="json"),
                },
            )
            return {
                "messages": [*current.get("messages", []), response.message],
                "response": response,
                "tool_calls": calls,
                "iteration": iteration + 1,
                "tool_call_count": tool_call_count,
                "token_usage": cumulative_usage,
                "usage": cumulative_usage,
                "status": RunStatus.RUNNING,
            }

        return await node_wrapper("call_model", state, operation)

    async def execute_tools(state: AgentState) -> dict[str, Any]:
        async def operation(current: AgentState) -> dict[str, Any]:
            if tool_runtime is None:
                return {
                    "status": RunStatus.FAILED,
                    "error": {
                        "code": "tool_runtime_unavailable",
                        "message": "tool calls were requested but no ToolRuntime is configured",
                    },
                }
            results = [ToolResult.model_validate(item) for item in current.get("tool_results", [])]
            messages = list(current.get("messages", []))
            history = list(current.get("tool_history", []))
            no_progress = int(current.get("consecutive_no_progress", 0))
            for raw_call in current.get("tool_calls", []):
                call = ToolCall.model_validate(raw_call)
                if any(
                    result.call_id == call.call_id and result.status == "succeeded"
                    for result in results
                ):
                    continue
                context = tool_runtime.default_context(
                    actor_id=str(current.get("metadata", {}).get("actor_id", "agent")),
                    session_id=current["thread_id"],
                    run_id=current["run_id"],
                    capabilities=set(
                        current.get("metadata", {}).get(
                            "capabilities", {"workspace.read"}
                        )
                    ),
                )
                emit(
                    RunEventType.TOOL_REQUESTED,
                    state=current,
                    node="execute_tools",
                    data={"call_id": call.call_id, "tool_name": call.name},
                )
                result = await tool_runtime.execute(call, context=context)
                if result.status == "pending_approval" and result.approval_request is not None:
                    approval = result.approval_request
                    emit(
                        RunEventType.APPROVAL_REQUIRED,
                        state=current,
                        node="execute_tools",
                        data={"approval": approval.model_dump(mode="json")},
                    )
                    resumed = interrupt(
                        {"kind": "approval", "approval": approval.model_dump(mode="json")}
                    )
                    if isinstance(resumed, dict):
                        decision = ApprovalDecision.model_validate(
                            {"request_id": approval.request_id, **resumed}
                        )
                        tool_runtime.decide_approval(
                            decision,
                            actor_id=decision.decided_by,
                            session_id=current["thread_id"],
                            run_id=current["run_id"],
                        )
                        result = await tool_runtime.execute(call, context=context)
                results.append(result)
                emit(
                    RunEventType.TOOL_OUTPUT,
                    state=current,
                    node="execute_tools",
                    data={
                        "call_id": call.call_id,
                        "tool_name": call.name,
                        "status": result.status,
                        "output": result.output,
                        "error": result.error,
                    },
                )
                content = result.output or (result.error or {}).get("message", "tool did not run")
                messages.append(
                    ChatMessage(
                        role="tool",
                        name=call.name,
                        content=[
                            ToolResultBlock(
                                tool_call_id=call.call_id,
                                content=content,
                                is_error=result.status != "succeeded",
                            )
                        ],
                    )
                )
                fingerprint = json.dumps(
                    {
                        "name": call.name,
                        "arguments": call.arguments,
                        "status": result.status,
                        "output": result.output,
                        "error": result.error,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                history.append(fingerprint)
                if len(history) >= 3 and len(set(history[-3:])) == 1:
                    return {
                        "tool_results": results,
                        "messages": messages,
                        "tool_calls": [],
                        "tool_history": history,
                        "status": RunStatus.FAILED,
                        "stop_reason": "repeated_tool_call",
                        "error": {
                            "code": "agent_no_progress",
                            "message": "same tool call and result repeated three times",
                        },
                    }
                no_progress = no_progress + 1 if result.status != "succeeded" else 0
                if no_progress >= 3:
                    return {
                        "tool_results": results,
                        "messages": messages,
                        "tool_calls": [],
                        "tool_history": history,
                        "consecutive_no_progress": no_progress,
                        "status": RunStatus.FAILED,
                        "stop_reason": "consecutive_no_progress",
                        "error": {
                            "code": "agent_no_progress",
                            "message": "three consecutive tool calls made no progress",
                        },
                    }
            return {
                "tool_results": results,
                "messages": messages,
                "tool_calls": [],
                "tool_history": history,
                "consecutive_no_progress": no_progress,
                "status": RunStatus.RUNNING,
            }

        return await node_wrapper("execute_tools", state, operation)

    async def verify(state: AgentState) -> dict[str, Any]:
        async def operation(current: AgentState) -> dict[str, Any]:
            response = current.get("response")
            text = response.text.strip() if isinstance(response, ChatResponse) else ""
            if text:
                return {
                    "verification": {
                        "satisfied": True,
                        "reason": "model returned a final response without pending tool calls",
                    },
                    "status": RunStatus.RUNNING,
                }
            no_progress = int(current.get("consecutive_no_progress", 0)) + 1
            if no_progress >= 3:
                return {
                    "verification": {
                        "satisfied": False,
                        "reason": "model returned no final text",
                    },
                    "consecutive_no_progress": no_progress,
                    "status": RunStatus.FAILED,
                    "stop_reason": "consecutive_no_progress",
                    "error": {
                        "code": "agent_no_progress",
                        "message": "model returned no text or tool calls three times",
                    },
                }
            feedback = ChatMessage.from_text(
                "system",
                "No usable final response or tool call was produced. Continue the task or "
                "return a concise final answer.",
            )
            return {
                "verification": {
                    "satisfied": False,
                    "reason": "model returned no final text",
                },
                "messages": [*current.get("messages", []), feedback],
                "consecutive_no_progress": no_progress,
                "status": RunStatus.RUNNING,
            }

        return await node_wrapper("verify", state, operation)

    async def finalize(state: AgentState) -> dict[str, Any]:
        async def operation(current: AgentState) -> dict[str, Any]:
            response = current.get("response")
            final_text = response.text if isinstance(response, ChatResponse) else None
            if not current.get("verification", {}).get("satisfied"):
                return {
                    "final_text": None,
                    "status": RunStatus.FAILED,
                    "stop_reason": "verification_failed",
                    "error": {
                        "code": "verification_failed",
                        "message": "server-side verification did not accept the model output",
                    },
                }
            return {
                "final_text": final_text,
                "status": RunStatus.COMPLETED,
                "stop_reason": "completed",
            }

        return await node_wrapper("finalize", state, operation)

    async def cancelled(state: AgentState) -> dict[str, Any]:
        return {"status": RunStatus.CANCELLED, "final_text": None}

    async def failed(state: AgentState) -> dict[str, Any]:
        return {"status": RunStatus.FAILED, "final_text": None}

    graph = StateGraph(AgentState)
    graph.add_node("load_context", load_context)
    graph.add_node("normalize_input", normalize_input)
    graph.add_node("plan", plan)
    graph.add_node("execute_tools", execute_tools)
    graph.add_node("call_model", call_model)
    graph.add_node("verify", verify)
    graph.add_node("finalize", finalize)
    graph.add_node("cancelled", cancelled)
    graph.add_node("failed", failed)
    graph.add_edge(START, "load_context")
    graph.add_conditional_edges(
        "load_context",
        _route_after_node,
        {"continue": "normalize_input", "cancelled": "cancelled", "failed": "failed"},
    )
    graph.add_conditional_edges(
        "normalize_input",
        _route_after_node,
        {"continue": "plan", "cancelled": "cancelled", "failed": "failed"},
    )
    graph.add_conditional_edges(
        "plan",
        _route_after_plan,
        {
            "continue": "call_model",
            "tools": "execute_tools",
            "cancelled": "cancelled",
            "failed": "failed",
        },
    )
    graph.add_conditional_edges(
        "execute_tools",
        _route_after_node,
        {"continue": "call_model", "cancelled": "cancelled", "failed": "failed"},
    )
    graph.add_conditional_edges(
        "call_model",
        _route_after_model,
        {
            "tools": "execute_tools",
            "verify": "verify",
            "cancelled": "cancelled",
            "failed": "failed",
        },
    )
    graph.add_conditional_edges(
        "verify",
        _route_after_verify,
        {
            "finalize": "finalize",
            "model": "call_model",
            "cancelled": "cancelled",
            "failed": "failed",
        },
    )
    graph.add_edge("finalize", END)
    graph.add_edge("cancelled", END)
    graph.add_edge("failed", END)
    return graph.compile(checkpointer=checkpointer or InMemorySaver())


async def _return_update(**values: Any) -> dict[str, Any]:
    return values


def _request_from_state(
    state: AgentState,
    tool_runtime: ToolRuntime | None,
) -> ChatRequest:
    capabilities = set(state.get("metadata", {}).get("capabilities", {"workspace.read"}))
    tools = []
    if tool_runtime is not None:
        tools = [
            definition.model_dump(mode="json")
            for definition in tool_runtime.registry.definitions()
            if set(definition.required_capabilities).issubset(capabilities)
        ]
    return ChatRequest(
        provider=state["provider"],
        model=state["model"],
        messages=state["messages"],
        tools=tools,
        metadata=state.get("metadata", {}),
    )


def _add_usage(first: TokenUsage, second: TokenUsage) -> TokenUsage:
    return TokenUsage(
        input_tokens=first.input_tokens + second.input_tokens,
        output_tokens=first.output_tokens + second.output_tokens,
        total_tokens=first.total_tokens + second.total_tokens,
    )


class AgentRuntime:
    """Run and control LangGraph executions with explicit lifecycle events."""

    def __init__(
        self,
        gateway: ModelGateway,
        *,
        checkpoint_store: CheckpointStore | None = None,
        graph_checkpointer: InMemorySaver | None = None,
        tool_runtime: ToolRuntime | None = None,
        run_repository: Any | None = None,
    ) -> None:
        self.gateway = gateway
        self.checkpoint_store = checkpoint_store or CheckpointStore()
        self.graph_checkpointer = graph_checkpointer or InMemorySaver()
        self.tool_runtime = tool_runtime
        self.run_repository = run_repository
        self._handles: dict[tuple[str, str], _RunHandle] = {}

    def _new_handle(self, request: RunRequest) -> _RunHandle:
        context = RunContext(
            thread_id=request.thread_id,
            run_id=request.run_id,
            provider=str(request.provider),
            model=request.model,
            metadata=request.metadata,
        )
        event_sink = None
        if self.run_repository is not None:
            start_run = getattr(self.run_repository, "start_run", None)
            if start_run is not None:
                start_run(context)
            event_sink = getattr(self.run_repository, "save_event", None)
        handle = _RunHandle(request=request, context=context, event_sink=event_sink)
        self._handles[(request.thread_id, request.run_id)] = handle
        handle.status = RunStatus.RUNNING
        handle.emitter.emit(RunEventType.RUN_STARTED, status=RunStatus.RUNNING)
        return handle

    def _state_from_request(self, request: RunRequest) -> AgentState:
        return {
            "thread_id": request.thread_id,
            "run_id": request.run_id,
            "provider": str(request.provider),
            "model": request.model,
            "metadata": request.metadata,
            "messages": request.messages,
            "tool_calls": request.tool_calls,
            "tool_results": [],
            "tool_history": [],
            "iteration": 0,
            "max_iterations": request.max_iterations,
            "max_tool_calls": request.max_tool_calls,
            "max_tokens": request.max_tokens,
            "max_wall_time_seconds": request.max_wall_time_seconds,
            "started_monotonic": time.monotonic(),
            "tool_call_count": len(request.tool_calls),
            "consecutive_no_progress": 0,
            "workspace_snapshot": {},
            "acceptance_criteria": request.acceptance_criteria,
            "verification": {},
            "token_usage": TokenUsage(),
            "usage": TokenUsage(),
            "stop_reason": None,
            "status": RunStatus.RUNNING,
            "cancel_requested": False,
        }

    def _config(self, thread_id: str, run_id: str) -> dict[str, Any]:
        # LangGraph's checkpointer keys a top-level graph by thread_id.  Use a
        # private composite key so independent runs in one user thread cannot
        # resume each other's graph state; the public contracts retain both IDs.
        return {"configurable": {"thread_id": f"{thread_id}:{run_id}"}}

    async def _save_graph_checkpoint(
        self,
        handle: _RunHandle,
        graph: Any,
        status: RunStatus,
        state: AgentState | dict[str, Any],
    ) -> Checkpoint:
        snapshot = await graph.aget_state(
            self._config(handle.request.thread_id, handle.request.run_id)
        )
        values = dict(snapshot.values) if snapshot.values else dict(state)
        values["status"] = status
        return self.checkpoint_store.save(
            thread_id=handle.request.thread_id,
            run_id=handle.request.run_id,
            state=values,
            next_nodes=tuple(snapshot.next),
            status=status,
            sequence=handle.emitter.sequence,
        )

    async def _execute(self, handle: _RunHandle, *, resume: bool) -> RunResult:
        control = handle.control
        graph = build_agent_graph(
            self.gateway,
            control=control,
            emitter=handle.emitter,
            checkpointer=self.graph_checkpointer,
            tool_runtime=self.tool_runtime,
        )
        config = self._config(handle.request.thread_id, handle.request.run_id)
        input_value: AgentState | Command
        if resume:
            input_value = Command(resume=handle.resume_value or {"action": "resume"})
        else:
            input_value = self._state_from_request(handle.request)
        try:
            result = await graph.ainvoke(input_value, config=config)
            snapshot = await graph.aget_state(config)
            state = dict(snapshot.values or result)
            if "__interrupt__" in result:
                handle.status = RunStatus.PAUSED
                interrupt_value = result["__interrupt__"][0].value
                pending_approval = None
                if (
                    isinstance(interrupt_value, dict)
                    and interrupt_value.get("kind") == "approval"
                    and isinstance(interrupt_value.get("approval"), dict)
                ):
                    pending_approval = ApprovalRequest.model_validate(
                        interrupt_value["approval"]
                    )
                reason = (
                    pending_approval.reason
                    if pending_approval is not None
                    else control.pause_reason
                )
                state["status"] = RunStatus.PAUSED
                state["pause_reason"] = reason
                state["pending_approval"] = pending_approval
                checkpoint = await self._save_graph_checkpoint(
                    handle, graph, RunStatus.PAUSED, state
                )
                handle.emitter.emit(
                    RunEventType.RUN_PAUSED,
                    status=RunStatus.PAUSED,
                    data={"reason": reason, "checkpoint_id": checkpoint.ref.checkpoint_id},
                )
                output = RunResult(
                    context=handle.context,
                    status=RunStatus.PAUSED,
                    events=list(handle.emitter.events),
                    checkpoint=checkpoint,
                    pending_approval=pending_approval,
                    tool_results=_tool_results_from_state(state),
                    usage=TokenUsage.model_validate(state.get("token_usage", {})),
                    stop_reason=state.get("stop_reason"),
                )
            else:
                status = RunStatus(state.get("status", RunStatus.FAILED))
                if status == RunStatus.COMPLETED:
                    handle.status = status
                    checkpoint = await self._save_graph_checkpoint(handle, graph, status, state)
                    handle.emitter.emit(
                        RunEventType.RUN_COMPLETED,
                        status=status,
                        data={"text": state.get("final_text")},
                    )
                    output = RunResult(
                        context=handle.context,
                        status=status,
                        final_text=state.get("final_text"),
                        events=list(handle.emitter.events),
                        checkpoint=checkpoint,
                        tool_results=_tool_results_from_state(state),
                        usage=TokenUsage.model_validate(state.get("token_usage", {})),
                        stop_reason=state.get("stop_reason"),
                    )
                elif status == RunStatus.CANCELLED:
                    handle.status = status
                    checkpoint = await self._save_graph_checkpoint(handle, graph, status, state)
                    handle.emitter.emit(RunEventType.RUN_CANCELLED, status=status)
                    output = RunResult(
                        context=handle.context,
                        status=status,
                        events=list(handle.emitter.events),
                        checkpoint=checkpoint,
                        tool_results=_tool_results_from_state(state),
                        usage=TokenUsage.model_validate(state.get("token_usage", {})),
                        stop_reason=state.get("stop_reason"),
                    )
                else:
                    handle.status = RunStatus.FAILED
                    checkpoint = await self._save_graph_checkpoint(
                        handle, graph, RunStatus.FAILED, state
                    )
                    error = state.get("error")
                    handle.emitter.emit(
                        RunEventType.RUN_FAILED,
                        status=RunStatus.FAILED,
                        data={"error": _json_value(error)},
                    )
                    output = RunResult(
                        context=handle.context,
                        status=RunStatus.FAILED,
                        events=list(handle.emitter.events),
                        checkpoint=checkpoint,
                        error=error,
                        tool_results=_tool_results_from_state(state),
                        usage=TokenUsage.model_validate(state.get("token_usage", {})),
                        stop_reason=state.get("stop_reason"),
                    )
        except asyncio.CancelledError:
            handle.status = RunStatus.CANCELLED
            state = self.checkpoint_store.get(handle.request.thread_id, handle.request.run_id)
            checkpoint = self.checkpoint_store.save(
                thread_id=handle.request.thread_id,
                run_id=handle.request.run_id,
                state=state.state if state else self._state_from_request(handle.request),
                status=RunStatus.CANCELLED,
                sequence=handle.emitter.sequence,
            )
            handle.emitter.emit(RunEventType.RUN_CANCELLED, status=RunStatus.CANCELLED)
            output = RunResult(
                context=handle.context,
                status=RunStatus.CANCELLED,
                events=list(handle.emitter.events),
                checkpoint=checkpoint,
                tool_results=_tool_results_from_checkpoint(
                    self.checkpoint_store.get(handle.request.thread_id, handle.request.run_id)
                ),
                stop_reason="cancelled",
            )
        except (AdapterNotImplementedError, ModelAdapterError) as exc:
            handle.status = RunStatus.FAILED
            checkpoint = self.checkpoint_store.save(
                thread_id=handle.request.thread_id,
                run_id=handle.request.run_id,
                state={**self._state_from_request(handle.request), "status": RunStatus.FAILED},
                status=RunStatus.FAILED,
                sequence=handle.emitter.sequence,
            )
            handle.emitter.emit(
                RunEventType.RUN_FAILED,
                status=RunStatus.FAILED,
                data={"error": str(exc)},
            )
            output = RunResult(
                context=handle.context,
                status=RunStatus.FAILED,
                events=list(handle.emitter.events),
                checkpoint=checkpoint,
                error={"code": "model_adapter_error", "message": str(exc)},
                tool_results=[],
                stop_reason="model_adapter_error",
            )
        except Exception as exc:
            handle.status = RunStatus.FAILED
            checkpoint = self.checkpoint_store.save(
                thread_id=handle.request.thread_id,
                run_id=handle.request.run_id,
                state={**self._state_from_request(handle.request), "status": RunStatus.FAILED},
                status=RunStatus.FAILED,
                sequence=handle.emitter.sequence,
            )
            handle.emitter.emit(
                RunEventType.RUN_FAILED,
                status=RunStatus.FAILED,
                data={"error": str(exc)},
            )
            output = RunResult(
                context=handle.context,
                status=RunStatus.FAILED,
                events=list(handle.emitter.events),
                checkpoint=checkpoint,
                error={"code": "runtime_error", "message": str(exc)},
                tool_results=[],
                stop_reason="runtime_error",
            )
        handle.result = output
        handle.done.set()
        return output

    async def run(self, request: RunRequest) -> RunResult:
        key = (request.thread_id, request.run_id)
        handle = self._handles.get(key)
        if handle is not None:
            if handle.result is not None:
                return handle.result
            await handle.done.wait()
            return handle.result or RunResult(context=handle.context, status=RunStatus.FAILED)
        handle = self._new_handle(request)
        handle.task = asyncio.current_task()
        return await self._execute(handle, resume=False)

    async def stream(self, request: RunRequest) -> AsyncIterator[RunEvent]:
        """Run once and yield lifecycle/model events as they are produced."""
        key = (request.thread_id, request.run_id)
        handle = self._handles.get(key)
        if handle is None:
            handle = self._new_handle(request)
            queue: asyncio.Queue[RunEvent] = asyncio.Queue()
            for event in handle.emitter.events:
                queue.put_nowait(event)
            handle.emitter.subscribers.append(queue)
            handle.task = asyncio.create_task(self._execute(handle, resume=False))
            while True:
                event = await queue.get()
                yield event
                if event.type in {
                    RunEventType.RUN_PAUSED,
                    RunEventType.RUN_CANCELLED,
                    RunEventType.RUN_COMPLETED,
                    RunEventType.RUN_FAILED,
                }:
                    break
            await handle.task
            return
        while not handle.done.is_set():
            await asyncio.sleep(0)
        for event in handle.emitter.events:
            yield event

    async def pause(self, thread_id: str, run_id: str, reason: str = "paused by caller") -> bool:
        handle = self._handles.get((thread_id, run_id))
        if handle is None or handle.status in {
            RunStatus.COMPLETED,
            RunStatus.CANCELLED,
            RunStatus.FAILED,
        }:
            return False
        handle.control.pause_requested = True
        handle.control.pause_reason = reason
        return True

    async def resume(
        self,
        thread_id: str,
        run_id: str,
        value: dict[str, Any] | None = None,
    ) -> RunResult:
        handle = self._handles.get((thread_id, run_id))
        if handle is None or handle.status != RunStatus.PAUSED:
            raise ValueError(f"No paused run found for {thread_id}/{run_id}")
        handle.control.pause_requested = False
        handle.resume_value = value
        handle.status = RunStatus.RUNNING
        handle.done.clear()
        handle.result = None
        handle.emitter.emit(RunEventType.RUN_RESUMED, status=RunStatus.RUNNING)
        handle.task = asyncio.current_task()
        return await self._execute(handle, resume=True)

    async def approve(
        self,
        thread_id: str,
        run_id: str,
        request_id: str,
        *,
        approved: bool,
        scope: ApprovalScope = ApprovalScope.ONCE,
        decided_by: str = "user",
        command_pattern: str | None = None,
    ) -> RunResult:
        """Record a human decision and resume the paused graph in one operation."""
        if self.tool_runtime is None:
            raise ValueError("tool runtime is not configured")
        decision = ApprovalDecision(
            request_id=request_id,
            approved=approved,
            scope=scope,
            decided_by=decided_by,
            command_pattern=command_pattern,
        )
        request = self.tool_runtime.decide_approval(
            decision,
            actor_id=decided_by,
            session_id=thread_id,
            run_id=run_id,
        )
        handle = self._handles.get((thread_id, run_id))
        if handle is not None:
            handle.emitter.emit(
                RunEventType.APPROVAL_DECIDED,
                status=handle.status,
                node="execute_tools",
                data={
                    "request_id": request.request_id,
                    "approved": approved,
                    "scope": scope.value,
                },
            )
        return await self.resume(
            thread_id,
            run_id,
            value=decision.model_dump(mode="json"),
        )

    async def cancel(self, thread_id: str, run_id: str) -> bool:
        handle = self._handles.get((thread_id, run_id))
        if handle is None or handle.status in {
            RunStatus.COMPLETED,
            RunStatus.CANCELLED,
            RunStatus.FAILED,
        }:
            return False
        if handle.status == RunStatus.PAUSED:
            checkpoint = self.checkpoint_store.get(thread_id, run_id)
            state = (
                checkpoint.state
                if checkpoint is not None
                else self._state_from_request(handle.request)
            )
            handle.status = RunStatus.CANCELLED
            cancelled_checkpoint = self.checkpoint_store.save(
                thread_id=thread_id,
                run_id=run_id,
                state=state,
                status=RunStatus.CANCELLED,
                sequence=handle.emitter.sequence,
            )
            handle.emitter.emit(RunEventType.RUN_CANCELLED, status=RunStatus.CANCELLED)
            handle.result = RunResult(
                context=handle.context,
                status=RunStatus.CANCELLED,
                events=list(handle.emitter.events),
                checkpoint=cancelled_checkpoint,
            )
            handle.done.set()
            return True
        handle.control.cancel_requested = True
        if handle.task is not None and handle.task is not asyncio.current_task():
            handle.task.cancel()
        return True

    def checkpoint(self, thread_id: str, run_id: str) -> Checkpoint | None:
        return self.checkpoint_store.get(thread_id, run_id)


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _tool_results_from_state(state: dict[str, Any]) -> list[ToolResult]:
    return [ToolResult.model_validate(item) for item in state.get("tool_results", [])]


def _tool_results_from_checkpoint(checkpoint: Checkpoint | None) -> list[ToolResult]:
    return _tool_results_from_state(checkpoint.state) if checkpoint is not None else []
