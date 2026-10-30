"""Local editor bridge. Graph compilation stays in the real ComfyUI frontend."""

import copy
import hashlib
import json
import logging
import time

from aiohttp import web
from server import PromptServer
from .reference_video import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
from .reference_cache import register as register_reference_cache, get_stats as reference_cache_stats

try:
    register_reference_cache()
except Exception as error:
    logging.getLogger(__name__).warning('Reference cache unavailable; native execution remains active: %s', error)

WEB_DIRECTORY = "./web"
CLIENT_TTL = 12


class BridgeError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


class BridgeState:
    def __init__(self):
        self.revision = 0
        self.workflow = None
        self.workflow_id = None
        self.digest = None
        self.client_id = None
        self.last_seen = 0
        self.commands = {}
        self.execution = {"promptId": None, "status": "idle", "node": None,
                          "progress": None, "outputs": {}, "error": None}
        self.events = []

    def connected(self):
        return bool(self.client_id and time.time() - self.last_seen < CLIENT_TTL)

    def public_command(self, command):
        return {k: copy.deepcopy(v) for k, v in command.items()
                if k not in {"workflow", "backup", "fingerprint"}}

    def state(self):
        now = time.time()
        for command in self.commands.values():
            if command["status"] == "pending" and now - command["createdAt"] > 90:
                command.update(status="failed", error={"code": "client_timeout",
                               "message": "专业画布未及时接收操作，请重新打开专业画布。"})
            if command["status"] == "running" and now - command["startedAt"] > 180:
                command.update(status="uncertain", error={"code": "acknowledgement_timeout",
                               "message": "未收到执行回执，请检查 ComfyUI 队列后再操作，避免重复生成。"})
        return {"revision": self.revision, "workflow": copy.deepcopy(self.workflow),
                "workflowId": self.workflow_id, "clientConnected": self.connected(),
                "lastSeen": self.last_seen or None, "clientId": self.client_id,
                "clientTtl": CLIENT_TTL,
                "pending": [self.public_command(c) for c in self.commands.values()],
                "execution": copy.deepcopy(self.execution), "events": copy.deepcopy(self.events)}

    def client(self, data):
        client_id = data.get("clientId")
        if not isinstance(client_id, str) or not 1 <= len(client_id) <= 120:
            raise BridgeError(400, "invalid_client", "缺少有效的专业画布会话。")
        if self.connected() and client_id != self.client_id:
            raise BridgeError(409, "editor_in_use", "已有专业画布在编辑；首版只支持一个编辑会话。")
        self.client_id = client_id
        self.last_seen = time.time()
        if "workflow" in data:
            if data.get("revision") != self.revision:
                raise BridgeError(409, "revision_mismatch", "画布已更新，已保留本地快照，请重新同步。")
            self.publish(data["workflow"], data.get("workflowId", self.workflow_id))
        events = data.get("events", [])
        if not isinstance(events, list):
            raise BridgeError(400, "invalid_events", "执行事件需要数组格式。")
        for event in events[:100]:
            self.record_event(event)
        return self.state()

    def publish(self, workflow, workflow_id):
        if not isinstance(workflow, dict) or not isinstance(workflow.get("nodes"), list):
            raise BridgeError(400, "invalid_workflow", "需要完整的 ComfyUI 工作流，包含 nodes 数组。")
        digest = hashlib.sha256(json.dumps(workflow, sort_keys=True, ensure_ascii=False,
                                          separators=(",", ":")).encode()).hexdigest()
        if digest != self.digest or workflow_id != self.workflow_id:
            self.workflow = copy.deepcopy(workflow)
            self.workflow_id = workflow_id
            self.digest = digest
            self.revision += 1

    def submit(self, data):
        op_id = data.get("opId")
        if not isinstance(op_id, str) or not 1 <= len(op_id) <= 120:
            raise BridgeError(400, "invalid_operation", "每次操作必须提供唯一 opId。")
        fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                                separators=(",", ":")).encode()).hexdigest()
        if op_id in self.commands:
            old = self.commands[op_id]
            if old["fingerprint"] != fingerprint:
                raise BridgeError(409, "operation_reused", "此操作编号已用于其他内容。")
            return self.public_command(old), 200
        if data.get("type") not in {"load", "execute", "interrupt"}:
            raise BridgeError(400, "invalid_command", "不支持此画布操作。")
        if not self.connected():
            raise BridgeError(503, "client_disconnected", "专业画布尚未连接，请保持专业画布页面打开。")
        if data.get("revision") != self.revision:
            raise BridgeError(409, "revision_mismatch", "专业画布已发生变化，请先同步后重试。")
        if any(c["status"] in {"pending", "running"} for c in self.commands.values()):
            raise BridgeError(409, "operation_in_progress", "上一次画布操作尚未完成。")
        if data["type"] != "interrupt":
            workflow = data.get("workflow")
            if not isinstance(workflow, dict) or not isinstance(workflow.get("nodes"), list):
                raise BridgeError(400, "invalid_workflow", "操作需要完整的 ComfyUI 工作流。")
        command = {"opId": op_id, "type": data["type"], "revision": self.revision,
                   "workflow": copy.deepcopy(data.get("workflow")),
                   "workflowId": data.get("workflowId", self.workflow_id),
                   "promptId": data.get("promptId"), "status": "pending", "result": None,
                   "error": None, "createdAt": time.time(), "clientId": self.client_id,
                   "fingerprint": fingerprint, "backup": copy.deepcopy(self.workflow)}
        self.commands[op_id] = command
        # ponytail: retain the last 32 completed operations for idempotent retries.
        while len(self.commands) > 32:
            oldest = next(iter(self.commands))
            if self.commands[oldest]["status"] in {"pending", "running"}:
                break
            del self.commands[oldest]
        return self.public_command(command), 202

    def take(self, client_id):
        if client_id != self.client_id or not self.connected():
            raise BridgeError(409, "editor_in_use", "此页面不是当前专业画布会话。")
        self.state()
        result = []
        for command in self.commands.values():
            if command["status"] != "pending" or command["clientId"] != client_id:
                continue
            if command["revision"] != self.revision:
                command.update(status="failed", error={"code": "revision_mismatch",
                               "message": "操作排队后专业画布已变化；已保留当前图，请先同步。"})
                continue
            command.update(status="running", startedAt=time.time())
            result.append({k: copy.deepcopy(v) for k, v in command.items()
                           if k not in {"backup", "fingerprint"}})
            break
        return {"commands": result, "revision": self.revision}

    def finish(self, data):
        if data.get("clientId") != self.client_id:
            raise BridgeError(409, "editor_in_use", "此页面不是当前专业画布会话。")
        command = self.commands.get(data.get("opId"))
        if not command:
            raise BridgeError(404, "operation_missing", "找不到此画布操作。")
        if command["status"] in {"completed", "failed"}:
            return self.state()
        self.last_seen = time.time()
        if "workflow" in data:
            self.publish(data["workflow"], command["workflowId"])
        if data.get("error"):
            command.update(status="failed", error=data["error"], result=data.get("result"))
        else:
            command.update(status="completed", result=data.get("result", {}), error=None)
            prompt_id = command["result"].get("promptId")
            if prompt_id and self.execution["promptId"] != prompt_id:
                self.execution = {"promptId": prompt_id, "status": "queued", "node": None,
                                  "progress": None, "outputs": {}, "error": None}
        command["finishedAt"] = time.time()
        return self.state()

    def record_event(self, event):
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            return
        kind = event["type"]
        data = event.get("data")
        data = data if isinstance(data, dict) else {"node": data}
        prompt_id = data.get("prompt_id") or event.get("promptId")
        entry = {"type": kind, "promptId": prompt_id, "data": copy.deepcopy(data), "at": time.time()}
        self.events.append(entry)
        self.events = self.events[-80:]
        if kind == "status":
            self.execution["queueRemaining"] = data.get("status", {}).get("exec_info", {}).get("queue_remaining")
            return
        if not prompt_id:
            return
        if kind == "execution_start" or self.execution["promptId"] is None:
            self.execution = {"promptId": prompt_id, "status": "running", "node": None,
                              "progress": None, "outputs": {}, "error": None}
        if prompt_id != self.execution["promptId"]:
            return
        if kind == "executing":
            self.execution["node"] = data.get("node")
        elif kind == "progress":
            self.execution["progress"] = {"value": data.get("value"), "max": data.get("max"),
                                          "node": data.get("node")}
        elif kind == "executed":
            self.execution["outputs"][str(data.get("node"))] = data.get("output", {})
        elif kind == "execution_success":
            self.execution["status"] = "completed"
        elif kind in {"execution_error", "execution_interrupted"}:
            self.execution["status"] = "failed" if kind == "execution_error" else "interrupted"
            self.execution["error"] = data


STATE = BridgeState()
ROUTES = PromptServer.instance.routes


def error_response(error):
    return web.json_response({"error": {"code": error.code, "message": error.message},
                              "state": STATE.state()}, status=error.status)


async def body(request):
    try:
        data = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise BridgeError(400, "invalid_json", "请求需要有效的 JSON。")
    if not isinstance(data, dict):
        raise BridgeError(400, "invalid_json", "请求需要 JSON 对象。")
    return data


@ROUTES.get("/creation-canvas/state")
async def get_state(request):
    return web.json_response(STATE.state(), headers={"Cache-Control": "no-store"})


@ROUTES.get("/creation-canvas/acceleration")
async def get_acceleration(request):
    return web.json_response(reference_cache_stats(), headers={"Cache-Control": "no-store"})


@ROUTES.post("/creation-canvas/command")
async def post_command(request):
    try:
        command, status = STATE.submit(await body(request))
        return web.json_response({"command": command, "revision": STATE.revision}, status=status)
    except BridgeError as error:
        return error_response(error)


@ROUTES.post("/creation-canvas/client")
async def post_client(request):
    try:
        return web.json_response(STATE.client(await body(request)))
    except BridgeError as error:
        return error_response(error)


@ROUTES.get("/creation-canvas/commands")
async def get_commands(request):
    try:
        return web.json_response(STATE.take(request.query.get("clientId")),
                                 headers={"Cache-Control": "no-store"})
    except BridgeError as error:
        return error_response(error)


@ROUTES.post("/creation-canvas/result")
async def post_result(request):
    try:
        return web.json_response(STATE.finish(await body(request)))
    except BridgeError as error:
        return error_response(error)
