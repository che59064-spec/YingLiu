import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

// Each tab has its own lease. This does not open another websocket or reuse its id.
const clientId = crypto.randomUUID();
let revision = 0;
let workflowId = null;
let initial = true;
let applying = false;
let dirty = true;
let busy = false;
let lastSnapshot = "";
let lastError = "";
let activePrompt = null;
let passthrough = null;
let outbox = [];
let unacknowledged = null;
let tickTimer;
let heartbeatTimer;
let heartbeatBusy = false;
let paused = false;

function errorInfo(error) {
    return { code: error?.code || "frontend_error", message: error?.message || String(error),
             details: error?.response || null };
}

async function request(path, data) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    try {
        const response = await api.fetchApi(`/creation-canvas/${path}`, { signal: controller.signal,
            ...(data === undefined ? { cache: "no-store" }
                : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) }) });
        const result = await response.json();
        if (!response.ok) {
            const error = new Error(result.error?.message || `HTTP ${response.status}`);
            error.code = result.error?.code;
            error.status = response.status;
            error.state = result.state;
            throw error;
        }
        return result;
    } catch (error) {
        if (controller.signal.aborted) throw new Error("专业同步请求超时，正在重新连接。");
        throw error;
    } finally {
        clearTimeout(timeout);
    }
}

function preserveMetadata(current, original) {
    if (!original || typeof original !== "object") return current;
    const merged = { ...original, ...current };
    // Retain unknown per-node metadata while honoring real node deletions and port edits.
    if (Array.isArray(current.nodes) && Array.isArray(original.nodes)) {
        const originals = new Map(original.nodes.map(node => [String(node.id), node]));
        merged.nodes = current.nodes.map(node => ({ ...originals.get(String(node.id)), ...node }));
    }
    if (current.definitions && original.definitions) {
        merged.definitions = { ...original.definitions, ...current.definitions };
        if (Array.isArray(current.definitions.subgraphs)) {
            const originals = new Map((original.definitions.subgraphs || []).map(graph => [String(graph.id), graph]));
            merged.definitions.subgraphs = current.definitions.subgraphs.map(graph =>
                preserveMetadata(graph, originals.get(String(graph.id))));
        }
    }
    return merged;
}

function snapshot() {
    // rootGraph is essential when the professional editor is inside a subgraph.
    const graph = app.rootGraph || app.graph;
    return graph?.serialize ? preserveMetadata(graph.serialize(), passthrough) : null;
}

function backup(workflow, reason) {
    if (!workflow) return;
    try {
        localStorage.setItem("creation-canvas:last-backup", JSON.stringify({
            at: new Date().toISOString(), reason, revision, workflowId, workflow
        }));
    } catch (error) {
        console.warn("[创作画布] 本地快照存储失败，操作前的图仍保留在桥接操作记录中。", error);
    }
}

function warn(error) {
    const message = error?.message || String(error);
    if (message === lastError) return;
    lastError = message;
    console.warn("[创作画布]", message);
}

async function applyCommand(command) {
    const before = snapshot();
    const beforePassthrough = passthrough;
    const beforeId = workflowId;
    const workflows = app.extensionManager?.workflow;
    const beforeWorkflow = workflows?.activeWorkflow;
    applying = true;
    let loaded = false;
    const acknowledgement = { clientId, opId: command.opId };
    try {
        if (command.type === "interrupt") {
            const response = await api.fetchApi("/interrupt", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify(command.promptId ? { prompt_id: command.promptId } : {})
            });
            if (!response.ok) throw new Error(`停止任务失败：HTTP ${response.status}`);
            acknowledgement.result = { interrupted: true, promptId: command.promptId || activePrompt };
        } else {
            backup(before, `before-${command.type}`);
            if (typeof workflows?.createNewTemporary !== "function")
                throw new Error("当前 ComfyUI 不支持独立临时工作流，请更新前端后再加载，避免覆盖已有文件。");
            const document = structuredClone(command.workflow);
            // Match ComfyUI's own Duplicate action: a new document has a fresh
            // root identity. Node IDs and every subgraph remain unchanged.
            document.id = crypto.randomUUID();
            const kind = JSON.stringify(document.nodes).match(/MiniMaxH3|Video|H3/)
                ? "视频卡片" : "图片卡片";
            const name = `创作画布·${kind}·${crypto.randomUUID().slice(0, 8)}.json`;
            const temporary = workflows.createNewTemporary(name, document);
            passthrough = structuredClone(document);
            // The fourth argument binds the graph to a new unsaved workflow,
            // never to the original professional tab or its persisted filename.
            const accepted = await app.loadGraphData(document, true, true, temporary);
            if (accepted === false)
                throw new Error("ComfyUI 未能完整加载工作流，已停止操作并尝试恢复原图。");
            if (workflows.activeWorkflow?.path !== temporary.path || !temporary.isTemporary)
                throw new Error("工作流文件身份未能安全切换，已停止操作并尝试恢复原图。");
            workflowId = command.workflowId;
            loaded = true;
            acknowledgement.result = { loaded: true, temporary: true,
                workflowName: temporary.fullFilename || name, workflowPath: temporary.path };
            if (command.type === "execute") {
                const graph = app.rootGraph || app.graph;
                const prompt = await app.graphToPrompt(graph);
                if (!prompt.output || !Object.keys(prompt.output).length)
                    throw new Error("工作流没有可执行的输出节点，请在专业画布检查。 ");
                const queued = await api.queuePrompt(0, prompt);
                if (!queued.prompt_id) throw new Error("ComfyUI 未返回任务编号。 ");
                activePrompt = queued.prompt_id;
                acknowledgement.result = { ...acknowledgement.result, promptId: queued.prompt_id,
                    number: queued.number, nodeErrors: queued.node_errors || {} };
            }
            acknowledgement.workflow = snapshot();
        }
    } catch (error) {
        acknowledgement.error = errorInfo(error);
        acknowledgement.result = { loaded };
        if (loaded) acknowledgement.workflow = snapshot();
        // A failed conversion keeps the loaded graph visible for repair. A failed
        // load restores the user's previous unsaved graph instead of leaving half a graph.
        if (!loaded && command.type !== "interrupt" && before) {
            try {
                passthrough = beforePassthrough;
                const restored = await app.loadGraphData(before, true, true, beforeWorkflow || null);
                if (restored === false) throw new Error("ComfyUI 未能恢复原图；本地快照已保留。");
                workflowId = beforeId;
                acknowledgement.result.restored = true;
            } catch (restoreError) {
                acknowledgement.error.restoreError = errorInfo(restoreError);
                warn(restoreError);
            }
        }
        warn(error);
    } finally {
        applying = false;
        dirty = true;
    }
    // Retrying this acknowledgement must never queue the same prompt a second time.
    unacknowledged = acknowledgement;
    await flushAcknowledgement();
}

async function flushAcknowledgement() {
    if (!unacknowledged) return;
    const state = await request("result", unacknowledged);
    revision = state.revision;
    workflowId = state.workflowId;
    unacknowledged = null;
    lastSnapshot = "";
}

async function tick() {
    if (paused || busy || !app.graph) return;
    busy = true;
    try {
        if (initial) {
            const state = await request("state");
            revision = state.revision;
            initial = false;
            // Loading the extension never loads an external workflow automatically.
        }
        await flushAcknowledgement();
        const workflow = snapshot();
        const serialized = JSON.stringify(workflow);
        const events = outbox.slice();
        const data = { clientId, revision, events };
        if (!applying && workflow && (dirty || serialized !== lastSnapshot)) {
            data.workflow = workflow;
            data.workflowId = workflowId;
        }
        const state = await request("client", data);
        revision = state.revision;
        outbox.splice(0, events.length);
        if (data.workflow) { lastSnapshot = serialized; dirty = false; }
        const { commands } = await request(`commands?clientId=${encodeURIComponent(clientId)}`);
        for (const command of commands) await applyCommand(command);
        lastError = "";
    } catch (error) {
        if (error.code === "revision_mismatch" && error.state) {
            backup(snapshot(), "revision-conflict");
            revision = error.state.revision;
            dirty = true;
        }
        warn(error);
    } finally {
        busy = false;
        if (!paused) tickTimer = setTimeout(tick, 1500);
    }
}

async function keepAlive() {
    if (paused || !busy || heartbeatBusy || initial) return;
    heartbeatBusy = true;
    try {
        // Loading a large workflow can await model/media scans for longer than
        // the lease. Renew without publishing the graph while it is incomplete.
        await request("client", { clientId, revision });
    } catch (error) { warn(error); }
    finally { heartbeatBusy = false; }
}

app.registerExtension({
    name: "CreationCanvas.LocalEditorBridge",
    async setup() {
        api.addEventListener("graphChanged", () => { if (!applying) dirty = true; });
        for (const type of ["status", "execution_start", "executing", "progress", "progress_state",
                            "executed", "execution_success", "execution_error", "execution_interrupted", "execution_cached"]) {
            api.addEventListener(type, event => {
                const detail = event.detail;
                if (detail?.prompt_id) activePrompt = detail.prompt_id;
                outbox.push({ type, data: detail, promptId: detail?.prompt_id || activePrompt });
                outbox = outbox.slice(-100);
            });
        }
        const resume = () => {
            paused = false;
            clearTimeout(tickTimer);
            clearInterval(heartbeatTimer);
            tickTimer = setTimeout(tick, 1200);
            heartbeatTimer = setInterval(keepAlive, 3000);
        };
        resume();
        window.addEventListener("pagehide", () => {
            paused = true;
            clearTimeout(tickTimer);
            clearInterval(heartbeatTimer);
        });
        window.addEventListener("pageshow", event => { if (event.persisted) resume(); });
    },
    afterConfigureGraph() { if (!applying) { passthrough = null; workflowId = null; dirty = true; } }
});
