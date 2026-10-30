// No browser or dependencies: exercise the actual extension with native APIs mocked.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

async function run() {
    const source = fs.readFileSync(path.join(__dirname, "../web/creation_canvas_bridge.js"), "utf8")
        .replace(/^import .*;\r?\n/gm, "");
    const oldGraph = { nodes: [{ id: 1, type: "UnsavedNode", widgets_values: ["未保存内容"] }], links: [] };
    const newGraph = { nodes: [{ id: 2, type: "UnknownNode", secretMetadata: { keep: true } }],
        definitions: { subgraphs: [{ id: "child", nodes: [{ id: 3, type: "TestNode" }] }] },
        customMetadata: "保留未知字段" };
    let current = structuredClone(oldGraph);
    let loads = 0, queues = 0, operation = null, ackFailures = 0, lastAck = null, revision = 0;
    let loadFailures = 0;
    const oldWorkflow = { path: "workflows/H3-original.json", isTemporary: false, fullFilename: "H3-original.json" };
    const opened = [oldWorkflow];
    const workflows = {
        activeWorkflow: oldWorkflow,
        createNewTemporary: (name, document) => {
            assert.match(name, /^创作画布·(?:图片|视频)卡片·.+\.json$/);
            assert.ok(document.id, "New card documents need their own root identity");
            const temporary = { path: `workflows/${name}`, fullFilename: name, isTemporary: true };
            opened.push(temporary);
            return temporary;
        }
    };
    let extension;
    const listeners = new Map();
    const app = {
        graph: {}, rootGraph: { serialize: () => structuredClone(current) },
        extensionManager: { workflow: workflows },
        registerExtension: value => { extension = value; },
        loadGraphData: async (workflow, clean, restoreView, identity) => {
            loads++;
            current = structuredClone(workflow);
            if (loadFailures-- > 0) return false;
            assert.ok(identity, "Loading must explicitly bind a workflow identity");
            workflows.activeWorkflow = identity;
            // Simulate a serializer that does not know arbitrary source metadata.
            delete current.customMetadata;
            delete current.nodes[0].secretMetadata;
            extension.afterConfigureGraph();
            return true;
        },
        graphToPrompt: async graph => {
            assert.equal(graph, app.rootGraph);
            return { output: { 2: { class_type: "UnknownNode", inputs: {} } }, workflow: current };
        }
    };
    const api = {
        addEventListener: (type, fn) => listeners.set(type, fn),
        queuePrompt: async (index, prompt) => {
            assert.equal(index, 0);
            assert.ok(prompt.output);
            queues++;
            return { prompt_id: "real-native-prompt", number: 5, node_errors: {} };
        },
        fetchApi: async (url, options) => {
            const data = options?.body ? JSON.parse(options.body) : null;
            let result;
            if (url.endsWith("/state")) result = { revision, workflowId: null };
            else if (url.endsWith("/client")) result = { revision: ++revision, workflowId: "image" };
            else if (url.includes("/commands?")) { result = { commands: operation ? [operation] : [] }; operation = null; }
            else if (url.endsWith("/result")) {
                lastAck = data;
                if (ackFailures-- > 0) throw new Error("temporary network failure");
                result = { revision: ++revision, workflowId: "image" };
            } else throw new Error(`Unexpected request ${url}`);
            return { ok: true, json: async () => result };
        }
    };
    const context = vm.createContext({ app, api, crypto: require("node:crypto").webcrypto,
        structuredClone, AbortController, console: { warn() {} }, localStorage: { setItem() {} },
        setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1, clearInterval() {},
        window: { addEventListener() {} } });
    vm.runInContext(source + "\nglobalThis.runTick = tick;", context);
    await extension.setup();
    await context.runTick();
    assert.equal(revision, 1, "The initial handshake must complete before exercising commands");
    assert.equal(loads, 0, "Startup must preserve the user's current unsaved graph");
    assert.equal(workflows.activeWorkflow, oldWorkflow);
    assert.ok(listeners.has("graphChanged"));
    operation = { opId: "execute-once", type: "execute", workflow: newGraph, workflowId: "image" };
    ackFailures = 1;
    await context.runTick();
    assert.equal(queues, 1);
    assert.equal(lastAck.result.promptId, "real-native-prompt");
    assert.equal(lastAck.result.temporary, true);
    assert.notEqual(workflows.activeWorkflow.path, oldWorkflow.path);
    assert.equal(workflows.activeWorkflow.isTemporary, true);
    assert.equal(opened[0], oldWorkflow, "The original tab must remain open");
    assert.equal(lastAck.workflow.customMetadata, "保留未知字段");
    assert.equal(lastAck.workflow.nodes[0].secretMetadata.keep, true);
    assert.equal(lastAck.workflow.definitions.subgraphs[0].id, "child");
    await context.runTick();
    assert.equal(queues, 1, "Lost acknowledgements must not queue duplicate generations");
    assert.equal(loads, 1);
    const beforeFailure = structuredClone(current);
    const identityBeforeFailure = workflows.activeWorkflow;
    operation = { opId: "load-failure", type: "load", workflow: oldGraph, workflowId: "other" };
    loadFailures = 1;
    await context.runTick();
    assert.equal(lastAck.result.loaded, false);
    assert.equal(lastAck.result.restored, true);
    assert.equal(workflows.activeWorkflow, identityBeforeFailure, "Failed loads must restore the original file identity");
    assert.deepEqual(current.nodes, beforeFailure.nodes);
    assert.equal(queues, 1, "A failed native load must never queue a generation");
    console.log("Frontend bridge checks passed: safe startup, isolated unsaved identities, failed-load restoration, native compilation, subgraphs, metadata, idempotent acknowledgement.");
}
run().catch(error => { console.error(error); process.exitCode = 1; });
