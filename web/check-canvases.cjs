// No services or models: document isolation, switching, jobs and delayed callbacks.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {webcrypto}=require('node:crypto');
const source=fs.readFileSync(__dirname+'/app.js','utf8').split('boot().catch')[0];
const elements=new Map(),storage=new Map();
function element(key){if(!elements.has(key))elements.set(key,{value:'画布甲',innerHTML:'',textContent:'',style:{},dataset:{},open:false,classList:{add(){},remove(){},toggle(){},contains(){return false;}},setAttribute(){},getBoundingClientRect:()=>({width:1000,height:720}),blur(){},focus(){},close(){this.open=false;},addEventListener(){}});return elements.get(key);}
const context={window:{},location:{origin:'http://127.0.0.1:8769'},URL,Blob,AbortController,crypto:webcrypto,console,setTimeout:()=>0,clearTimeout(){},requestAnimationFrame(){},localStorage:{setItem:(k,v)=>storage.set(k,v),getItem:k=>storage.get(k)||null},document:{activeElement:null,querySelector:element,querySelectorAll:()=>[],createElement:()=>({click(){}})}};
vm.createContext(context);
vm.runInContext(source+`
window.check={state,changeCanvas,activateCanvas,saveProject,projectChanged,projectStorageKey,renderCanvasTabs,importResults,addWorkflow,uploadFiles,runCard,submitJoin,joinPayload,boot,resolveProjectDraft,restoreRecoveredDrafts,refreshBridge,safeStorageSave};
renderCards=()=>{};renderInspector=()=>{};renderJobs=()=>{};applyViewport=()=>{};drawConnections=()=>{};renderMedia=()=>{};refreshMedia=async()=>{};toast=message=>window.lastToast=message;
`,context);
const c=context.window.check,copy=value=>JSON.parse(JSON.stringify(value));
const workflow=()=>({id:'source',type:'workflow',name:'动作工作流',workflow_id:'w',fields:[],values:{},workflow:{nodes:[]},x:20,y:30});
const canvas=(id,name)=>({id,name,revision:0,cards:[workflow()],edges:[],viewport:{x:15,y:25,zoom:.8}});
let docs,active,calls,failSave,releaseGet;
function setup(){
  storage.clear();c.state.recoveryDrafts=[];
  docs={a:canvas('a','画布甲'),b:canvas('b','画布乙')};active='a';calls=[];failSave=false;releaseGet=null;
  c.state.canvasesAvailable=true;c.state.canvases=Object.values(docs).map(({id,name})=>({id,name}));c.state.jobs=[];c.state.canvasBusy=false;c.state.importing=false;c.activateCanvas(copy(docs.a));
  context.mockApi=async(path,options={})=>{
    calls.push({path,method:options.method,body:options.body&&JSON.parse(options.body)});
    if(path==='/api/canvases/active'){active=JSON.parse(options.body).id;return{active_id:active};}
    if(path==='/api/canvases'&&options.method==='POST'){const newCanvas={...canvas('c','画布 3'),cards:[]};docs.c=newCanvas;return copy(newCanvas);}
    const id=path.replace('/api/canvases/','');
    if(docs[id]){if(options.method==='PUT'){if(failSave)throw Object.assign(new Error('写入失败'),{status:failSave===409?409:500});const body=JSON.parse(options.body);assert.equal(body.revision,docs[id].revision);docs[id]={...body,revision:docs[id].revision+1};return copy(docs[id]);}if(releaseGet!==null)await new Promise(resolve=>{releaseGet=resolve;});return copy(docs[id]);}
    throw new Error('Unexpected request '+path);
  };
  vm.runInContext('api=mockApi;',context);
}
async function checks(){
  setup();const original=c.state.project;c.state.bridgeActive='source';element('#project-name').value='动作 · 甲';original.cards[0].x=777;c.projectChanged();
  assert.equal(await c.changeCanvas('b'),true);assert.equal(active,'b');assert.equal(docs.a.name,'动作 · 甲');assert.equal(docs.a.cards[0].x,777);assert.equal(c.state.project.id,'b');assert.equal(c.state.bridgeActive,null);assert.equal(c.state.mode,'simple');
  assert.ok(calls.findIndex(call=>call.path==='/api/canvases/a'&&call.method==='PUT')<calls.findIndex(call=>call.path==='/api/canvases/b'));
  assert.equal(calls.some(call=>call.path.includes('/comfy/')),false,'switching must not send a graph or interrupt the engine');
  assert.equal(await c.changeCanvas(null,{create:true}),true);assert.equal(c.state.project.id,'c');assert.equal(c.state.project.cards.length,0,'new canvases stay empty');
  await c.changeCanvas('a');assert.equal(c.state.project.name,'动作 · 甲');assert.equal(c.state.project.cards[0].x,777);assert.deepEqual(copy(c.state.project.viewport),{x:15,y:25,zoom:.8});
  assert.ok(storage.has('creation-canvas-project-v1:a'));assert.ok(storage.has('creation-canvas-project-v1:b'));assert.equal(storage.has('creation-canvas-project-v1'),false,'multi-canvas drafts must not overwrite legacy backup');

  setup();c.state.project.cards[0].x=99;c.projectChanged();failSave=true;
  assert.equal(await c.changeCanvas('b'),false);assert.equal(active,'a');assert.equal(c.state.project.id,'a');assert.equal(c.state.project.cards[0].x,99);assert.equal(c.state.canvasBusy,false);assert.equal(calls.some(call=>call.path==='/api/canvases/active'),false);assert.equal(JSON.parse(storage.get(c.projectStorageKey())).cards[0].x,99);

  setup();releaseGet=false;const switching=c.changeCanvas('b');await Promise.resolve();assert.equal(c.state.canvasBusy,true);assert.equal(await c.changeCanvas('a'),false);releaseGet();releaseGet=null;await switching;assert.equal(c.state.project.id,'b');

  setup();const job={id:'video-1',canvas_id:'a',instance_id:'source',status:'completed',outputs:[{url:'/assets/result.mp4',type:'video'}]};c.state.jobs=[job];
  await c.changeCanvas('b');assert.equal(c.state.project.cards.length,1,'same card ID in another canvas must not receive results');
  await c.changeCanvas('a');assert.equal(c.state.project.cards.length,2);assert.equal(c.state.project.cards[1].fromJob,'video-1');assert.equal(c.state.project.edges[0].result,true);
  c.importResults([job]);assert.equal(c.state.project.cards.length,2,'results are imported once');

  setup();let releaseWorkflow;context.mockApi=async()=>{await new Promise(resolve=>releaseWorkflow=resolve);return{workflow:{nodes:[]},fields:[]};};vm.runInContext('api=mockApi;',context);
  const adding=c.addWorkflow('w');c.activateCanvas(copy(docs.b));releaseWorkflow();await adding;assert.equal(c.state.project.cards.length,1,'late workflow must not attach to the new canvas');
  setup();let releaseAsset;context.mockApi=async()=>{await new Promise(resolve=>releaseAsset=resolve);return{id:'asset',type:'image',url:'/assets/ref.png'};};vm.runInContext('api=mockApi;',context);
  const uploading=c.uploadFiles([{name:'人物.png',type:'image/png'}]);c.activateCanvas(copy(docs.b));releaseAsset();await uploading;assert.equal(c.state.project.cards.length,1,'late uploaded references must not attach to another canvas');

  setup();c.state.connected=true;let submitted;context.mockApi=async(path,options)=>{if(path==='/api/run'){submitted=JSON.parse(options.body);return{job_id:'run-a'};}throw new Error(path);};vm.runInContext('api=mockApi;refreshJobs=async()=>{};',context);
  await c.runCard('source');assert.equal(submitted.canvas_id,'a');assert.equal(c.state.jobs[0].canvas_id,'a');
  c.state.project.cards.push({id:'clip1',type:'video',url:'/assets/1.mp4'},{id:'clip2',type:'video',url:'/assets/2.mp4'},{id:'join',type:'join',name:'连片',ranges:{}});c.state.project.edges=[{from:'clip1',to:'join'},{from:'clip2',to:'join'}];assert.equal(c.joinPayload(c.state.project.cards.at(-1)).canvas_id,'a');

  c.state.canvasesAvailable=false;c.renderCanvasTabs();assert.equal(element('#new-canvas').disabled,true);assert.equal(await c.changeCanvas(null,{create:true}),false);

  // A mapping request already in progress must finish before saving or leaving its canvas.
  for(const switchAway of [true,false]){
    setup();c.state.connected=true;c.state.bridgeActive='source';c.state.bridgeHash='previous';const graph={nodes:[{id:9,type:'EditedNode',widgets_values:['保留新的专业图']} ]};
    const storageApi=context.mockApi;let releaseMapping;
    context.mockApi=async(path,options={})=>{
      if(path==='/api/comfy/bridge')return{workflowId:'source',workflow:graph,revision:8,clientConnected:true};
      if(path==='/api/prepare-workflow'){await new Promise(resolve=>releaseMapping=resolve);return{fields:[{id:'new-prompt',type:'textarea',value:'专业图中的新描述',editable:true}]};}
      return storageApi(path,options);
    };vm.runInContext('api=mockApi;',context);
    const refreshing=c.refreshBridge();await new Promise(setImmediate);assert.equal(typeof releaseMapping,'function');
    const finishing=switchAway?c.changeCanvas('b'):c.saveProject(false);await Promise.resolve();assert.equal(calls.some(call=>call.method==='PUT'),false,'saving must wait for the pending mapping write');
    releaseMapping();await Promise.all([refreshing,finishing]);assert.deepEqual(copy(docs.a.cards[0].workflow),graph);assert.equal(docs.a.cards[0].values['new-prompt'],'专业图中的新描述');
    assert.equal(c.state.project.id,switchAway?'b':'a');if(switchAway)assert.deepEqual(copy(c.state.project.cards[0].workflow),{nodes:[]},'mapping must never leak into another canvas with the same card ID');
  }

  // Old services keep their single project editable until the new backend is opened.
  vm.runInContext('bindEvents=()=>{};restoreUi=()=>{};renderCatalog=()=>{};refreshStatus=async()=>{};startEngine=async()=>{};pollConnection=async()=>{};',context);
  function bootApi(){const storageApi=context.mockApi;context.mockApi=async(path,options={})=>{if(path==='/api/catalog')return{items:[]};if(path==='/api/settings')return{};if(path==='/api/project')return copy(docs[active]);if(path==='/api/canvases'&&options.method!=='POST')return{items:Object.values(docs).map(({id,name})=>({id,name})),active_id:active};return storageApi(path,options);};vm.runInContext('api=mockApi;',context);}

  // A successful server GET must not erase an unsaved draft from a failed PUT.
  setup();c.state.project.cards[0].x=909;c.projectChanged();failSave=true;await c.saveProject(false);
  assert.equal(JSON.parse(storage.get(c.projectStorageKey()+':state')).dirty,true);bootApi();await c.boot();
  assert.equal(c.state.project.cards[0].x,909);assert.equal(c.state.dirty,true);assert.equal(docs.a.cards[0].x,20,'a restored draft must not auto-overwrite the server');assert.equal(JSON.parse(storage.get(c.projectStorageKey())).cards[0].x,909);
  failSave=false;await c.saveProject(false);assert.equal(docs.a.cards[0].x,909);assert.equal(JSON.parse(storage.get(c.projectStorageKey()+':state')).dirty,false,'successful saves clear the unsaved marker');

  // A changed server revision keeps both copies and exposes a working recovery action.
  setup();docs.a.revision=1;c.state.project.revision=1;c.state.project.name='冲突前草稿';element('#project-name').value='冲突前草稿';c.state.project.cards[0].x=777;c.projectChanged();docs.a.revision=3;docs.a.cards[0].x=111;failSave=409;await c.saveProject(false);failSave=false;bootApi();await c.boot();
  assert.equal(c.state.project.cards[0].x,111);assert.equal(c.state.project.revision,3);assert.equal(c.state.recoveryDrafts.length,1);assert.equal(c.state.recoveryDrafts[0].project.cards[0].x,777);assert.match(element('#save-state').textContent,/恢复草稿/);assert.equal(typeof element('#save-state').onclick,'function');
  assert.equal(JSON.parse(storage.get('creation-canvas-recoveries-v1'))[0].project.cards[0].x,777,'archive must exist before the normal backup is replaced');
  failSave=true;await c.restoreRecoveredDrafts();assert.equal(c.state.recoveryDrafts.length,1);assert.match(element('#save-state').textContent,/恢复草稿/);assert.equal(c.state.recoveryDrafts[0].project.cards[0].x,777);
  failSave=false;await c.restoreRecoveredDrafts();assert.equal(c.state.recoveryDrafts.length,0);assert.equal(docs.c.cards[0].x,777);assert.equal(docs.a.cards[0].x,111,'recovery creates another canvas and preserves the server version');assert.equal(c.state.project.id,'a');

  // Old drafts without a marker have unknown save status, so differing content is preserved.
  setup();const unmarked=copy(docs.a);unmarked.cards[0].x=888;storage.set('creation-canvas-project-v1:a',JSON.stringify(unmarked));storage.delete('creation-canvas-project-v1:a:state');bootApi();await c.boot();assert.equal(c.state.project.cards[0].x,20);assert.equal(c.state.recoveryDrafts[0].project.cards[0].x,888);

  // Storage quota failures must not claim that the current edits have a browser backup.
  setup();c.state.project.cards[0].x=606;c.projectChanged();const setItem=context.localStorage.setItem,logger=context.console;
  context.console={...console,warn(){}};context.localStorage.setItem=()=>{throw new Error('QuotaExceeded');};failSave=true;assert.equal(c.safeStorageSave(),false);await c.saveProject(true);
  assert.equal(c.state.project.cards[0].x,606);assert.match(element('#save-state').textContent,/导出备份/);assert.match(context.window.lastToast,/当前窗口/);assert.doesNotMatch(context.window.lastToast,/浏览器草稿已保留/);
  context.localStorage.setItem=setItem;context.console=logger;

  // If only the marker write fails, a new dirty document must never inherit an old clean marker.
  setup();const backupKey=c.projectStorageKey(),cleanBackup=storage.get(backupKey),writes=[];c.state.project.cards[0].x=919;c.state.dirty=true;
  context.console={...console,warn(){}};context.localStorage.setItem=(key,value)=>{writes.push(key);if(key===backupKey+':state')throw new Error('marker quota');setItem(key,value);};
  assert.equal(c.safeStorageSave(),false);assert.deepEqual(writes,[backupKey+':state']);assert.equal(storage.get(backupKey),cleanBackup);assert.equal(JSON.parse(storage.get(backupKey+':state')).dirty,false);
  // If the second write fails, the existing backup is marked dirty rather than silently discarded.
  context.localStorage.setItem=(key,value)=>{if(key===backupKey)throw new Error('document quota');setItem(key,value);};assert.equal(c.safeStorageSave(),false);assert.equal(storage.get(backupKey),cleanBackup);assert.equal(JSON.parse(storage.get(backupKey+':state')).dirty,true);
  context.localStorage.setItem=setItem;context.console=logger;

  setup();
  const legacy={...canvas(undefined,'原有单画布'),cards:[workflow()]};delete legacy.id;
  context.mockApi=async path=>{if(path==='/api/catalog')return{items:[]};if(path==='/api/settings')return{};if(path==='/api/project')return copy(legacy);throw Object.assign(new Error('旧后台'),{status:404});};vm.runInContext('api=mockApi;',context);
  await c.boot();assert.equal(c.state.project.name,'原有单画布');assert.equal(c.state.project.cards.length,1);assert.equal(c.state.canvasesAvailable,false);assert.equal(element('#new-canvas').disabled,true);

  // A failed document read may recover only the requested canvas's draft.
  storage.set('creation-canvas-project-v1:b',JSON.stringify({...docs.b,name:'乙的浏览器草稿'}));storage.set('creation-canvas-project-v1',JSON.stringify({...legacy,name:'别的旧稿'}));
  context.mockApi=async path=>{if(path==='/api/catalog')return{items:[]};if(path==='/api/settings')return{};if(path==='/api/canvases')return{items:[{id:'a',name:'甲'},{id:'b',name:'乙'}],active_id:'b'};throw new Error('暂时无法读取');};vm.runInContext('api=mockApi;',context);
  await c.boot();assert.equal(c.state.project.id,'b');assert.equal(c.state.project.name,'乙的浏览器草稿');assert.equal(c.state.dirty,true);
  storage.delete('creation-canvas-project-v1:b');await assert.rejects(c.boot(),/未能读取本机画布/);assert.equal(c.state.canvasBusy,true,'unreadable documents stay protected from accidental overwrites');
  console.log('多画布检查通过：独立保存与任务归属、迟到回调隔离、延迟专业图写入、失败草稿重启恢复、版本冲突另存及重试、旧草稿保护、存储失败提示和旧后台兼容。');
}
checks().catch(error=>{console.error(error);process.exitCode=1;});
