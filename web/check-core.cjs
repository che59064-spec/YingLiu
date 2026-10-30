// Run with: node web/check-core.cjs
// Checks document preservation, field permissions, result references and media trust boundaries.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
let source = fs.readFileSync(__dirname + '/app.js', 'utf8');
source = source.slice(0, source.lastIndexOf('boot().catch'));
const elements=new Map();
const element=key=>{if(!elements.has(key))elements.set(key,{value:'原项目',textContent:'',style:{},dataset:{},setAttribute(){},classList:{add(){},remove(){},toggle(){}}});return elements.get(key);};
const context = {window:{},location:{origin:'http://127.0.0.1:8765'},crypto:webcrypto,URL,Blob,AbortController,console,setTimeout:()=>0,clearTimeout(){},localStorage:{setItem(){}},document:{querySelector:element,querySelectorAll:()=>[],createElement:()=>({click(){}})}};
vm.createContext(context);
vm.runInContext(source + '\nwindow.check={api,pollConnection,normalizeProject,descField,modelOf,jobPercent,kindOf,referencePayload,saveProject,importFile,importResults,removeCard,refreshStatus,startEngine,applyViewport,worldPoint,state};renderCards=()=>{};renderInspector=()=>{};renderJobs=()=>{};applyViewport=()=>{};toast=()=>{};', context);
const c=context.window.check;
const raw={nodes:[{id:1,type:'UnknownNode',widgets_values:['keep this'],extra:{preserve:true}}],definitions:{subgraphs:[{id:'nested',nodes:[]}]},unknown:{unchanged:[1,2,3]}};
const project=c.normalizeProject({cards:[{id:'card-1',type:'workflow',workflow:raw,fields:[],values:{},x:10,y:20},{id:'asset-1',type:'image',url:'/api/assets/image.png',x:0,y:0}],edges:[{id:'edge',from:'asset-1',to:'card-1',fieldId:'nodes/1:image'}],viewport:{zoom:1,x:0,y:0}});
assert.equal(JSON.stringify(project.cards[0].workflow),JSON.stringify(raw));
assert.equal(project.edges[0].fieldId,'nodes/1:image');
assert.equal(c.normalizeProject({cards:[{id:'x',type:'image',url:'https://external.example/image.png',x:'bad',y:0}],edges:[]}).cards[0].url,'');
assert.equal(c.descField({fields:[{id:'p',type:'textarea',key:'prompt',label:'画面描述',advanced:false}]}).id,'p');
assert.equal(c.descField({fields:[{id:'cached',type:'textarea',key:'prompt',label:'画面描述',advanced:false,editable:false},{id:'real',type:'textarea',key:'value',label:'中文镜头提示词',advanced:false,editable:true}]}).id,'real');
assert.equal(c.descField({fields:[{id:'ratio',type:'text',key:'aspect_ratio',label:'画面比例'},{id:'scene',type:'textarea',key:'value',label:'中文镜头提示词'}]}).id,'scene');
assert.equal(c.modelOf({name:'H3 多图参考生视频',models:['qwen3vl_32b_minimax_h3.safetensors']}),'MiniMax H3 · 本机运行');
assert.equal(c.jobPercent({progress:.42}),42);
assert.equal(c.kindOf({kind:'image',name:'千问_参考图编辑'}),'edit');
context.window.devicePixelRatio=2;
element('#viewport').getBoundingClientRect=()=>({left:300,top:60,width:900,height:700});
for(const zoom of [.25,.66,1,1.5,2]){
  c.state.project.viewport={x:110.15,y:95.2,zoom};c.applyViewport();
  const style=element('#canvas-world').style;
  assert.equal(style.transform,'none','text must not be rescaled from a cached transform layer');
  const screenX=300+(parseFloat(style.left)+140)*zoom,screenY=60+(parseFloat(style.top)+85)*zoom;
  const point=c.worldPoint(screenX,screenY);
  assert.ok(Math.abs(point.x-140)<.001&&Math.abs(point.y-85)<.001,'card and connection coordinates must survive zoom');
}
c.state.project=project;
const refs=c.referencePayload(project.cards[0]);
assert.equal(refs[0].fieldId,'nodes/1:image');
assert.equal(refs[0].url,'/api/assets/image.png');
async function persistenceChecks(){
  let serverProject={id:'original',name:'原项目',revision:7,cards:[{id:'card-1',type:'workflow',workflow:raw,fields:[],values:{},x:0,y:0}],edges:[],viewport:{x:0,y:0,zoom:1}},newCanvas,active='original';
  const incoming={...serverProject,id:'foreign',name:'导入画布',cards:[{...serverProject.cards[0],id:'imported-card'}]},exported=JSON.stringify(incoming),revisions=[];let releaseOld;
  c.state.canvasesAvailable=true;c.state.canvases=[{id:serverProject.id,name:serverProject.name}];
  c.state.project=JSON.parse(JSON.stringify(serverProject));c.state.bridgeActive='card-1';c.state.bridgeHash='old-session';
  context.mockApi=async(path,options={})=>{
    const body=options.body&&JSON.parse(options.body);
    if(path==='/api/canvases/original'&&options.method==='PUT'){
      revisions.push(['original',body.revision]);
      assert.equal(body.revision,serverProject.revision,'must write using current server revision');
      if(revisions.length===1)await new Promise(resolve=>{releaseOld=resolve;});
      serverProject={...body,revision:serverProject.revision+1};return{revision:serverProject.revision};
    }
    if(path==='/api/canvases'&&options.method==='POST'){
      newCanvas={id:'imported',name:body.name,revision:0,cards:[],edges:[],viewport:{x:0,y:0,zoom:1}};return newCanvas;
    }
    if(path==='/api/canvases/imported'&&options.method==='PUT'){
      revisions.push(['imported',body.revision]);assert.equal(body.revision,newCanvas.revision);
      newCanvas={...body,revision:newCanvas.revision+1};return newCanvas;
    }
    if(path==='/api/canvases/active'){active=body.id;return{active_id:active};}
    throw new Error('Unexpected API '+path);
  };
  vm.runInContext('api=mockApi;',context);
  const saving=c.saveProject(false);
  const importing=c.importFile({name:'导入画布.json',text:async()=>exported});
  await Promise.resolve();releaseOld();await Promise.all([saving,importing]);await c.saveProject(false);
  assert.deepEqual(revisions,[['original',7],['imported',0]]);assert.equal(serverProject.revision,8);assert.equal(c.state.project.revision,1);
  assert.equal(active,'imported');assert.equal(c.state.project.id,'imported');assert.equal(newCanvas.cards[0].id,'imported-card');
  assert.equal(c.state.project.importMetadata.sourceRevision,7);assert.equal(c.state.bridgeActive,null);assert.equal(c.state.bridgeHash,null);
  assert.equal(serverProject.cards[0].id,'card-1');

  // A late response may finish its captured document, never a replacement project.
  let releaseLate;const old=c.state.project;c.state.dirty=true;element('#project-name').value='old request';
  context.mockApi=async()=>{await new Promise(resolve=>{releaseLate=resolve;});return{revision:10};};
  vm.runInContext('api=mockApi;',context);
  const late=c.saveProject(false);const replacement={name:'replacement',revision:30,cards:[],edges:[]};c.state.project=replacement;releaseLate();await late;
  assert.equal(old.revision,10);assert.equal(replacement.revision,30);

  c.state.project={name:'outputs',revision:0,cards:[{id:'source',type:'workflow',name:'图片',x:0,y:0}],edges:[],viewport:{x:0,y:0,zoom:1}};
  const jobs=[{id:'job1',instance_id:'source',status:'completed',outputs:[{url:'/api/assets/result.png',type:'image'}]}];
  c.importResults(jobs);assert.equal(c.state.project.cards.length,2);
  c.removeCard(c.state.project.cards.find(card=>card.outputKey).id);assert.equal(c.state.project.cards.length,1);
  c.state.project=c.normalizeProject(JSON.parse(JSON.stringify(c.state.project)));c.importResults(jobs);
  assert.equal(c.state.project.cards.length,1,'dismissed outputs must stay removed after save/reload');
}
async function startupChecks(){
  const frame=element('#comfy-frame');let reloads=0;
  Object.defineProperty(frame,'src',{configurable:true,set(value){assert.equal(value,'http://127.0.0.1:8188');reloads++;}});
  let status={connected:false,bridgeConnected:false,startup:{status:'starting'}};
  const calls=[];
  context.mockApi=async(path,options={})=>{
    calls.push([path,options]);
    if(path==='/api/comfy/status')return status;
    if(path==='/api/comfy/start')return{status:'starting',url:'http://127.0.0.1:8188'};
    if(path==='/api/settings')return{};
    if(path==='/api/comfy/bridge')return{clientConnected:false};
    throw new Error('Unexpected API '+path);
  };
  vm.runInContext('api=mockApi;',context);
  c.state.connected=false;c.state.starting=false;c.state.selected=null;
  await c.refreshStatus();assert.equal(c.state.starting,true);assert.equal(element('#reconnect-engine').disabled,true);
  status.startup={status:'error',message:'启动进程已退出',log:'data/comfy-engine.log'};
  await c.refreshStatus();assert.equal(c.state.starting,false);assert.equal(element('#reconnect-engine').disabled,false);
  assert.match(element('#connection-detail').textContent,/启动进程已退出/);assert.equal(element('#reconnect-engine').textContent,'重试启动');
  status={connected:true,url:'http://127.0.0.1:8188',bridgeConnected:false,startup:{status:'ready'}};
  await c.refreshStatus();assert.equal(reloads,1);
  await c.refreshStatus();assert.equal(reloads,1,'waiting for bridge must not repeatedly reload its iframe');
  status={connected:false,startup:{status:'starting'}};await c.refreshStatus();
  status={connected:true,url:'http://127.0.0.1:8188',bridgeConnected:false,startup:{status:'ready'}};
  await c.refreshStatus();assert.equal(reloads,2,'same-port engine recovery must reload a failed iframe');
  calls.length=0;await c.startEngine({automatic:true});
  assert.equal(calls.filter(([path])=>path==='/api/comfy/start').length,1);
  assert.equal(calls.filter(([path])=>path==='/api/settings').length,0,'automatic startup must preserve auto settings');
  assert.equal(reloads,2,'automatic checks should preserve a live professional page');
  c.state.dirty=false;calls.length=0;await c.startEngine();
  assert.equal(calls.filter(([path])=>path==='/api/comfy/start').length,1);assert.equal(reloads,3,'explicit reconnect should reload once');
  await c.refreshStatus();assert.equal(reloads,3);
  console.log('创作画布核心检查通过：原图保留、参数与引用、项目保存、结果移除、自动启动、失败重试与同端口重连。');
}
async function connectionChecks(){
  let statusCalls=0,release;
  context.mockApi=async path=>{assert.equal(path,'/api/comfy/status');statusCalls++;return new Promise(resolve=>{release=resolve;});};
  vm.runInContext('api=mockApi;',context);
  const first=c.refreshStatus(),second=c.refreshStatus();
  release({connected:true,url:'http://127.0.0.1:8188',bridgeConnected:false});
  await Promise.all([first,second]);assert.equal(statusCalls,1,'status checks must not overlap');
  context.mockApi=async()=>{throw new Error('service stopped');};vm.runInContext('api=mockApi;',context);
  await c.refreshStatus();assert.equal(c.state.connected,false);assert.equal(c.state.bridgeConnected,false);
  assert.equal(element('#reconnect-engine').disabled,false,'a stopped service must not lock the reconnect button');
  assert.equal(element('#reconnect-engine').textContent,'重新连接服务');
  const sent=[];context.window.top=context.window;context.window.chrome={webview:{postMessage:message=>sent.push(message)}};
  await c.startEngine();assert.equal(sent.length,1);assert.equal(sent[0].type,'service');assert.equal(sent[0].action,'recover');
  context.window.top={};await c.startEngine();assert.equal(sent.length,1,'an embedded page must not trigger desktop recovery');
  context.window.top=context.window;
  delete context.window.chrome;
  const liveFrame=element('#comfy-frame');let frameReloads=0;
  Object.defineProperty(liveFrame,'src',{configurable:true,set(){frameReloads++;}});
  context.mockApi=async()=>({connected:true,url:'http://127.0.0.1:8188',bridgeConnected:true});vm.runInContext('api=mockApi;',context);
  await c.refreshStatus();assert.equal(c.state.connected,true);assert.equal(c.state.bridgeConnected,true);
  assert.equal(frameReloads,0,'canvas service recovery must preserve a live professional editor');
  let timeout;
  context.setTimeout=callback=>{timeout=callback;return 1;};
  context.fetch=(_path,options)=>new Promise((_resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new Error('aborted'))));
  const request=c.api('/api/health',{timeoutMs:5});timeout();
  await assert.rejects(request,/本地服务响应超时/);
  console.log('断线恢复检查通过：超时释放、状态并发合并、重连按钮可重试、专业编辑器不被服务恢复覆盖。');
}
persistenceChecks().then(startupChecks).then(connectionChecks).catch(error=>{console.error(error);process.exitCode=1;});
