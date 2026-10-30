// Offline checks: no service, ComfyUI or model is started.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {webcrypto}=require('node:crypto');
const source=fs.readFileSync(__dirname+'/app.js','utf8').split('boot().catch')[0];
const elements=new Map(),element=key=>{if(!elements.has(key))elements.set(key,{value:'',textContent:'',innerHTML:'',dataset:{},style:{},classList:{add(){},remove(){},toggle(){}},setAttribute(){},showModal(){}});return elements.get(key);};
const context={window:{},location:{origin:'http://127.0.0.1:8767'},crypto:webcrypto,URL,Blob,AbortController,console,setTimeout:()=>0,clearTimeout(){},requestAnimationFrame(){},localStorage:{setItem(){}},document:{querySelector:element,querySelectorAll:()=>[]}};
vm.createContext(context);
vm.runInContext(source+`\nwindow.check={state,continuityOptions,continuationReference,continuationHtml,renderQuickControls,setContinuation,continuationPayload,runCard,referencePayload,refreshBridge,setMode};renderCards=()=>{};renderInspector=()=>{};renderJobs=()=>{};toast=()=>{};selectCard=id=>{state.selected=id;};refreshJobs=async()=>{};projectChanged=()=>{state.dirty=true;};`,context);
const c=context.window.check,plain=value=>JSON.parse(JSON.stringify(value));
const id='a'.repeat(32)+'.mp4', result='/api/comfy/view?filename=part1.mp4&type=output';
const workflow={nodes:[{id:136,type:'MiniMaxH3ReferenceToVideo'}]};
const card={id:'h3',type:'workflow',name:'动作迁移',workflow,values:{duration:5},fields:[],x:0,y:0};
const reference={id:'motion',type:'video',name:'原动作',url:'/assets/'+'b'.repeat(32)+'.mp4',x:0,y:0};
c.state.project={id:'canvas-a',cards:[card,reference],mediaArchive:[{assetId:id,id,url:'/assets/'+id,sourceUrl:result,type:'video',name:'第一段',source:'generated'}],edges:[{id:'ref',from:'motion',to:'h3'}],viewport:{x:0,y:0,zoom:1}};
c.state.selected='h3';c.state.connected=true;
c.state.media=[{assetId:id,id,url:'/assets/'+id,sourceUrl:result,type:'video',name:'第一段',source:'generated'},
  {url:'/assets/'+'d'.repeat(32)+'.mp4',type:'video',name:'其他画布全局素材'}, {url:'https://example.com/private.mp4',type:'video',name:'外部'}];
c.state.jobs=[{id:'job1',instance_id:'h3',canvas_id:'canvas-a',status:'completed',outputs:[{url:result,type:'video',name:'第一段'}]},
 {id:'foreign',instance_id:'h3',canvas_id:'canvas-b',outputs:[{url:'/assets/'+'e'.repeat(32)+'.mp4',type:'video',name:'其他画布作品'}]},
 {id:'foreign-old',instance_id:'other',outputs:[{url:'/assets/'+'f'.repeat(32)+'.mp4',type:'video',name:'其他旧作品'}]}];
const options=c.continuityOptions();assert.equal(options.length,2,'same canvas output and reference remain selectable, duplicate output is collapsed');
assert.doesNotMatch(c.continuationHtml(card),/其他画布|其他旧作品/);
assert.match(c.renderQuickControls(card),/接续上一段/,'continuation is visible on the selected card');
assert.match(c.continuationHtml(card),/作品 · 第一段/);assert.doesNotMatch(c.continuationHtml(card),/example\.com/);
const before=plain(c.referencePayload(card));
assert.equal(c.setContinuation('h3',options.find(item=>item.label.includes('第一段')).identity),true);
assert.deepEqual(plain(card.continuation),{type:'video',assetId:id,url:'/assets/'+id,name:'第一段'});
assert.deepEqual(plain(c.referencePayload(card)),before,'continuity must not alter original motion reference edges');
const persisted=plain(card.continuation);c.state.media=[];c.state.jobs=[];c.state.project.mediaArchive=[];
assert.deepEqual(plain(c.continuationPayload(card)),persisted,'saved reference survives removing old cards and missing shelf response');
assert.match(c.continuationHtml(card),/当前画布列表暂未显示/);
assert.equal(c.setContinuation('h3',''),true);assert.equal(c.continuationPayload(card),null);
assert.equal(c.setContinuation('h3','not-a-real-video'),false);assert.equal(card.continuation,undefined);
assert.equal(c.setContinuation('different',''),false,'another canvas/card cannot be changed via a stale selector');
assert.equal(c.continuationHtml({type:'workflow',workflow:{nodes:[{type:'Other'}]}}),'');
const apiCard={id:'api',type:'workflow',workflow:{'1':{class_type:'MiniMaxH3ReferenceToVideo'}},continuation:persisted};
c.state.project.cards.push(apiCard);c.state.selected='api';
assert.match(c.continuationHtml(apiCard),/不支持接续，请清除选择/);
assert.equal(c.setContinuation('api','other'),false);assert.equal(c.setContinuation('api',''),true);
assert.equal(c.continuationHtml(apiCard),'');c.state.selected='h3';
c.state.jobs=[{id:'old',instance_id:'h3',outputs:[{url:'/assets/'+'c'.repeat(32)+'.mp4',type:'video',name:'当前画布旧作品'}]}];
assert.match(c.continuationHtml(card),/当前画布旧作品/);c.state.jobs=[];
const canvasA=c.state.project;c.state.project={id:'canvas-b',cards:[{...card,id:'other'}],edges:[],mediaArchive:[]};c.state.selected='other';
assert.equal(c.continuityOptions().length,0,'switching canvas does not carry previous candidates');
assert.equal(c.setContinuation('h3',options[0].identity),false,'stale selector cannot modify the previous canvas');
c.state.project=canvasA;c.state.selected='h3';
assert.equal(c.continuationReference({type:'video',url:'https://external.example/file.mp4'}),null);
assert.equal(c.continuationReference({type:'video',url:'/api/ai-provider'}),null);

async function main(){
  card.continuation=persisted;let calls=[];
  context.mockApi=async(path,opts)=>{calls.push({path,body:JSON.parse(opts.body)});return{job_id:'new-job',status:'queued'};};
  vm.runInContext('api=mockApi',context);await c.runCard('h3');
  assert.equal(calls.length,1);assert.deepEqual(calls[0].body.continuation,persisted);
  assert.deepEqual(calls[0].body.references,before);
  c.state.jobs=[];card.continuation={type:'video',url:'https://evil.example/x.mp4'};calls=[];
  await c.runCard('h3');assert.equal(calls.length,0);assert.match(card.error,/上一段视频地址无效/);
  c.state.jobs=[];delete card.continuation;calls=[];await c.runCard('h3');
  assert.equal(calls.length,1);assert.ok(!('continuation' in calls[0].body),'ordinary generation remains unchanged');
  // A temporary execution graph must never be mistaken for the selected editable card.
  c.state.jobs=[];c.state.bridgeActive='h3';c.state.bridgeHash='previous';c.state.bridgeTemporary=null;
  const originalWorkflow=plain(card.workflow);calls=[];
  context.mockApi=async(path)=>{calls.push(path);return {clientConnected:true,workflowId:'continuation-job-123',workflow:{nodes:[{id:136,type:'MiniMaxH3ReferenceToVideo',temporary:true}]},revision:12};};vm.runInContext('api=mockApi',context);
  await c.refreshBridge();
  assert.equal(c.state.bridgeActive,null);assert.equal(c.state.bridgeHash,null);assert.equal(c.state.bridgeTemporary,'continuation-job-123');
  assert.deepEqual(plain(card.workflow),originalWorkflow);assert.deepEqual(calls,['/api/comfy/bridge']);
  assert.match(element('#bridge-label').textContent,/接续运行图/);
  await c.refreshBridge();assert.match(element('#bridge-label').textContent,/接续运行图/,'hint persists on later polls');
  let automaticLoads=0;context.mockLoad=async()=>{automaticLoads++;};vm.runInContext('loadCardIntoEngine=mockLoad',context);
  await c.setMode('professional');assert.equal(automaticLoads,0,'switching views must not overwrite the temporary graph');
  // A delayed response from a previous canvas cannot clear the new canvas binding.
  let release;const previous=c.state.project;c.state.bridgeActive='h3';c.state.bridgeHash='keep';c.state.bridgeTemporary=null;
  context.mockApi=async()=>new Promise(resolve=>{release=resolve;});vm.runInContext('api=mockApi',context);
  const pending=c.refreshBridge();await Promise.resolve();assert.equal(typeof release,'function');
  c.state.project={id:'canvas-b',cards:[{id:'other',type:'workflow',workflow:{nodes:[]}}],edges:[]};c.state.bridgeActive='other';c.state.bridgeHash='other-hash';
  release({clientConnected:true,workflowId:'continuation-old',workflow:{nodes:[]}});await pending;
  assert.equal(c.state.bridgeActive,'other');assert.equal(c.state.bridgeHash,'other-hash');
  // A non-continuation foreign workflow is preserved under the pre-existing rules.
  c.state.project=previous;c.state.bridgeActive='h3';c.state.bridgeHash='keep';
  context.mockApi=async()=>({clientConnected:true,workflowId:'unrelated',workflow:{nodes:[]}});vm.runInContext('api=mockApi',context);
  await c.refreshBridge();assert.equal(c.state.bridgeActive,'h3');assert.equal(c.state.bridgeHash,'keep');
  console.log('Continuity UI passed: explicit selection, stable references, safe URLs, original references, temporary bridge isolation and late-canvas protection.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
