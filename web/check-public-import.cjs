// Public release regression: VM + fake localhost responses, never a live service/model.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {webcrypto}=require('node:crypto');
const source=fs.readFileSync(__dirname+'/app.js','utf8').split('boot().catch')[0];
const lossless=fs.readFileSync(__dirname+'/lossless-json.js','utf8');
const settingsSource=fs.readFileSync(__dirname+'/settings.js','utf8');
const html=fs.readFileSync(__dirname+'/index.html','utf8');
const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(match=>match[1]);
assert.equal(new Set(ids).size,ids.length,'HTML IDs must be unique');
for(const id of ['import-workflow','workflow-input','workflow-import-status','settings-dialog','app-settings','prompt-settings','engine-settings-link'])
  assert.ok(ids.includes(id),'missing release control '+id);
for(const match of settingsSource.matchAll(/el\('([^']+)'\)/g))
  assert.ok(ids.includes(match[1]),'settings.js references missing control '+match[1]);
const scripts=[...html.matchAll(/<script\b[^>]*src="([^"?]+)(?:\?[^"]*)?"[^>]*>/g)].map(match=>match[1]);
assert.ok(scripts.indexOf('/lossless-json.js')<scripts.indexOf('/app.js'),'lossless parser loads before app');
assert.ok(scripts.indexOf('/app.js')<scripts.indexOf('/settings.js'),'settings can use app API helper');
for(const name of ['/lossless-json.js','/app.js','/settings.js']) {
  assert.ok(scripts.includes(name));assert.ok(fs.existsSync(__dirname+name));
  assert.match(html,new RegExp('<script[^>]*src="'+name.replaceAll('.','\\.')+'[^>]*\\bdefer\\b'));
}
assert.ok(fs.existsSync(__dirname+'/settings.css'));
assert.match(html,/<input\b[^>]*id="ai-key"[^>]*type="password"/);

function harness(){
  const elements=new Map(),storage=new Map(),downloads=[],blobs=new Map(),calls=[],toasts=[];
  function element(key){
    if(!elements.has(key)){
      const classes=new Set(),listeners=new Map();
      elements.set(key,{value:'',textContent:'',innerHTML:'',hidden:false,open:false,dataset:{},style:{},disabled:false,
        clickCount:0,checked:false,listeners,
        classList:{add(...names){names.forEach(name=>classes.add(name));},remove(...names){names.forEach(name=>classes.delete(name));},contains:name=>classes.has(name),toggle(name,force){const active=force??!classes.has(name);if(active)classes.add(name);else classes.delete(name);return active;}},
        setAttribute(name,value){this[name]=value;},removeAttribute(name){delete this[name];},
        addEventListener(name,callback){listeners.set(name,callback);},querySelector:selector=>element(key+' '+selector),querySelectorAll:()=>[],
        getBoundingClientRect:()=>({left:0,top:0,width:1100,height:760}),
        click(){this.clickCount++;if(key==='a')downloads.push({href:this.href,name:this.download});},
        reportValidity:()=>true,showModal(){this.open=true;},close(){this.open=false;listeners.get('close')?.();},
        blur(){},focus(){},closest:()=>null,contains:()=>false,scrollIntoView(){}});
    }
    return elements.get(key);
  }
  class LocalURL extends URL{
    static createObjectURL(blob){const id='blob:offline-'+blobs.size;blobs.set(id,blob);return id;}
    static revokeObjectURL(){}
  }
  const context={window:{addEventListener(){},matchMedia:()=>({matches:true})},crypto:webcrypto,URL:LocalURL,Blob,AbortController,
    location:{origin:'http://127.0.0.1:8771'},console,setTimeout:()=>0,clearTimeout(){},requestAnimationFrame(){},
    localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)},
    document:{activeElement:null,querySelector:element,querySelectorAll:()=>[],getElementById:id=>element('#'+id),
      createElement:tag=>element(tag),addEventListener(){}}};
  vm.createContext(context);vm.runInContext(lossless,context);
  const codec=context.window.CanvasJSON,copy=value=>codec.parse(codec.stringify(value));
  let route=()=>{throw new Error('Unexpected offline request');},textReads=0;
  const response=(document,status=200)=>({ok:status>=200&&status<300,status,headers:{get:()=> 'application/json'},
    text:async()=>{textReads++;return typeof document==='string'?document:codec.stringify(document);},
    json:async()=>{throw new Error('response.json() would round 64-bit seeds');}});
  context.fetch=async(path,options={})=>{calls.push({path,...options});return route(path,options,response);};
  context.recordToast=message=>toasts.push(message);
  vm.runInContext(source+`
window.check={state,api,clone,normalizeProject,projectContent,importFile,exportProject,activateCanvas,addWorkflow,bindEvents,fieldHtml};
renderCards=()=>{};renderInspector=()=>{};renderJobs=()=>{};renderCatalog=()=>{};applyViewport=()=>{};drawConnections=()=>{};
selectCard=id=>state.selected=id;setMode=()=>{};cancelPromptOptimization=()=>{};clearMediaDrop=()=>{};
bindGlassLighting=()=>{};bindMediaEvents=()=>{};bindPromptOptimizer=()=>{};bindCanvasTabs=()=>{};bindNodeMenu=()=>{};
toast=recordToast;
`,context);
  const c=context.window.check;
  function activate(project){c.state.canvasesAvailable=true;c.state.canvases=[{id:project.id,name:project.name}];c.activateCanvas(copy(project));}
  return{c,context,codec,copy,element,calls,toasts,downloads,blobs,response,activate,
    setRoute:fn=>route=fn,textReads:()=>textReads};
}
const oldCanvas=()=>({id:'old',name:'原有画布',revision:3,cards:[{id:'original',type:'image',name:'已有素材',url:'/assets/keep.png',x:20,y:30}],edges:[],viewport:{x:15,y:25,zoom:1}});
const importCanvas=()=>({id:'foreign',name:'导入画布',revision:77,cards:[{id:'new-media',type:'video',name:'导入素材',url:'/assets/import.mp4',x:80,y:90}],edges:[],viewport:{x:110,y:95,zoom:1}});
const file=(text,name='示例.json')=>({name,size:Buffer.byteLength(text),text:async()=>text});

async function isolatedCanvasImport(){
  const h=harness(),docs={old:oldCanvas()};let active='old';h.activate(docs.old);
  h.c.state.project.cards[0].name='尚未保存的改动';h.c.state.dirty=true;
  h.setRoute((path,options,response)=>{
    const body=options.body&&h.codec.parse(options.body);
    if(path==='/api/canvases'&&options.method==='POST'){docs.new={...oldCanvas(),id:'new',name:body.name,revision:0,cards:[]};return response(docs.new);}
    if(path==='/api/canvases/active'){active=body.id;return response({active_id:active});}
    const id=path.replace('/api/canvases/','');
    if(docs[id]&&options.method==='PUT'){assert.equal(body.revision,docs[id].revision);docs[id]={...body,revision:body.revision+1};return response(docs[id]);}
    throw new Error('Unexpected route '+path);
  });
  await h.c.importFile(file(h.codec.stringify(importCanvas())));
  assert.equal(active,'new');assert.equal(h.c.state.project.id,'new');
  assert.equal(docs.old.cards[0].id,'original');assert.equal(docs.old.cards[0].name,'尚未保存的改动');
  assert.equal(docs.new.cards[0].id,'new-media');assert.equal(docs.new.importMetadata.sourceRevision,77);
  const oldWrites=h.calls.filter(call=>call.path==='/api/canvases/old'&&call.method==='PUT');
  assert.equal(oldWrites.length,1);assert.equal(h.codec.parse(oldWrites[0].body).cards[0].id,'original');
  assert.ok(h.calls.findIndex(call=>call.path==='/api/canvases/old')<h.calls.findIndex(call=>call.path==='/api/canvases'&&call.method==='POST'));
  assert.equal(h.c.state.importing,false);

  // A failed new-document write never replaces or selects the original canvas.
  const failed=harness();failed.activate(oldCanvas());failed.setRoute((path,options,response)=>{
    if(path==='/api/canvases'&&options.method==='POST')return response({...oldCanvas(),id:'new',revision:0,cards:[]});
    if(path==='/api/canvases/new')return response({error:'offline write failure'},500);
    throw new Error('Unexpected route '+path);
  });
  await failed.c.importFile(file(failed.codec.stringify(importCanvas())));
  assert.equal(failed.c.state.project.id,'old');assert.equal(failed.c.state.project.cards[0].id,'original');
  assert.equal(failed.calls.some(call=>call.path==='/api/canvases/active'),false);
  assert.match(failed.toasts.at(-1),/无法导入/);

  const unsaved=harness();unsaved.activate(oldCanvas());
  unsaved.c.state.project.cards[0].name='必须保留的未保存改动';unsaved.c.state.dirty=true;
  unsaved.setRoute((path,_options,response)=>{
    assert.equal(path,'/api/canvases/old');return response({error:'original save failed'},500);
  });
  await unsaved.c.importFile(file(unsaved.codec.stringify(importCanvas())));
  assert.equal(unsaved.c.state.project.id,'old');assert.equal(unsaved.c.state.project.cards[0].name,'必须保留的未保存改动');
  assert.equal(unsaved.c.state.dirty,true);assert.equal(unsaved.calls.some(call=>call.path==='/api/canvases'),false);
  assert.match(unsaved.toasts.at(-1),/当前画布未能保存/);
}

async function workflowImportAnd64Bit(){
  const h=harness();h.activate(oldCanvas());
  const raw='{"prompt":{"8":{"class_type":"Custom","inputs":{"seed":18446744073709551615,"prompt":"保持人物一致"}}},"client_id":"ignored-metadata"}';
  const workflow=h.codec.parse(raw).prompt;
  const fields=[{id:'8:seed',key:'seed',type:'number',value:18446744073709551615n,editable:false},
    {id:'8:prompt',key:'prompt',role:'prompt',type:'textarea',value:'保持人物一致',editable:true,advanced:false}];
  let count=0;
  h.setRoute((path,options,response)=>{
    if(path==='/api/import-workflow'){
      assert.equal(options.body,raw,'preserve the exact file, including existing wrappers and seeds');
      assert.equal(decodeURIComponent(options.headers['X-Workflow-Name']),'中文工作流');
      return response({id:'imported',duplicate:count++>0,warnings:['此节点需要自行安装。']},201);
    }
    if(path==='/api/catalog')return response({items:[{id:'imported',name:'自定义流程',category:'我的导入',kind:'image'}]});
    if(path==='/api/workflows/imported')return response({id:'imported',name:'自定义流程',workflow,fields,warnings:['此节点需要自行安装。'],compatibility:{format:'api'}});
    throw new Error('Unexpected route '+path);
  });
  await h.c.importFile(file(raw,'中文工作流.json'),true);
  const card=h.c.state.project.cards.at(-1);
  assert.equal(card.workflow['8'].inputs.seed,18446744073709551615n);
  assert.equal(card.fields[0].value,18446744073709551615n);
  assert.equal(Object.hasOwn(card.values,'8:seed'),false,'read-only seeds do not become editable values');
  assert.equal(h.c.clone(card).workflow['8'].inputs.seed,18446744073709551615n);
  assert.match(h.c.projectContent(h.c.state.project),/18446744073709551615/);
  assert.match(h.element('#workflow-import-status').textContent,/自行安装/);
  assert.ok(h.textReads()>=3,'actual API helper reads raw response text');
  await h.c.importFile(file(raw,'中文工作流.json'),true);
  assert.match(h.element('#workflow-import-status').textContent,/已存在/);
  assert.equal(h.c.state.project.cards.length,3,'duplicate library import may be reused as another card');
  h.element('#ai-key').value='test-key-never-exported';h.c.exportProject();
  const exported=await h.blobs.get(h.downloads.at(-1).href).text();
  assert.match(exported,/"seed": 18446744073709551615/);
  assert.equal(h.codec.parse(exported).cards.at(-1).workflow['8'].inputs.seed,18446744073709551615n);
  assert.equal(exported.includes('test-key-never-exported'),false);
}

async function dedicatedEntryAndInvalidFiles(){
  const h=harness();h.activate(oldCanvas());h.c.bindEvents();
  h.element('#import-workflow').onclick();assert.equal(h.element('#workflow-input').clickCount,1);
  const input=h.element('#workflow-input');
  input.value='chosen-file';input.files=[file(h.codec.stringify(importCanvas()))];
  input.onchange({target:input});await new Promise(resolve=>setImmediate(resolve));
  assert.equal(input.value,'');assert.equal(h.calls.length,0);assert.match(h.toasts.at(-1),/顶部/);
  for(const invalid of ['[]','null','{"broken":']){
    await h.c.importFile(file(invalid),true);assert.match(h.toasts.at(-1),/无法导入/);
  }
  await h.c.importFile({name:'huge.json',size:33*1024*1024,text:async()=>{throw new Error('oversized file should not be read');}},true);
  assert.match(h.toasts.at(-1),/32 MB/);assert.equal(h.calls.length,0);
  const normalized=h.c.normalizeProject({cards:[{id:'safe',type:'workflow',name:'test',workflow:{},fields:[null,{},
    {id:'n',key:'width',type:'number',min:'" autofocus onfocus="bad',max:{bad:1},step:'bad',value:512}],models:{bad:1},warnings:[null,'保留'],
    outputs:[null,{url:'https://evil.example/remote.png'},{url:'/assets/local.png'}]}],edges:[],
    mediaArchive:[{url:'https://evil.example/file.mp4'}]});
  assert.equal(normalized.cards[0].fields.length,1);assert.equal('min' in normalized.cards[0].fields[0],false);
  assert.equal(normalized.cards[0].outputs.length,1);assert.equal(normalized.mediaArchive.length,0);
  assert.equal(normalized.cards[0].models.length,0);assert.equal(normalized.cards[0].warnings.length,1);
  const rendered=h.c.fieldHtml({id:'test',type:'workflow',values:{}},{id:'api_key',key:'api_key',sensitive:true,editable:false,value:'private-example-value'});
  assert.match(rendered,/type="password"/);assert.equal(rendered.includes('private-example-value'),false);
}

async function settingsConnections(){
  const h=harness();h.activate(oldCanvas());
  let config={provider:'openai',baseUrl:'https://example.test/v1',model:'vision-model',responseFormat:'prompt',vision:true,hasKey:true,configured:true};
  let tests=0;
  h.setRoute((path,options,response)=>{
    if(path==='/api/ai-provider'){
      if(options.method==='PUT'){const body=JSON.parse(options.body);config={...config,...body,hasKey:body.clearKey?false:true};delete config.apiKey;delete config.clearKey;}
      return response(config);
    }
    if(path==='/api/ai-provider/test'){tests++;return response({message:'offline connection accepted'});}
    if(path==='/api/settings')return response({comfy_url:'auto',comfy_root:'',comfy_python:'',comfy_port:8188,auto_start:true});
    throw new Error('Unexpected settings route '+path);
  });
  vm.runInContext(settingsSource,h.context);
  await h.context.window.CanvasSettings.open();
  assert.equal(h.element('#settings-dialog').open,true);assert.equal(h.element('#ai-key').value,'');
  assert.equal(h.element('#ai-provider').value,'openai');assert.equal(h.element('#ai-model').value,'vision-model');
  h.element('#ai-key').value='test-placeholder-key';await h.element('#ai-save').onclick();
  assert.equal(tests,0,'saving must never perform a connection/model request');
  assert.equal(h.element('#ai-key').value,'','saved key is cleared rather than displayed');
  await h.element('#ai-test').onclick();assert.equal(tests,1,'only explicit test invokes test endpoint');
  await h.element('#ai-clear-key').onclick();
  assert.equal(JSON.parse(h.calls.filter(call=>call.path==='/api/ai-provider'&&call.method==='PUT').at(-1).body).clearKey,true);
  h.element('#ai-key').value='temporary-entry';h.element('#settings-close').onclick();
  assert.equal(h.element('#ai-key').value,'');assert.equal(h.element('#settings-dialog').open,false);
}

(async()=>{
  await isolatedCanvasImport();await workflowImportAnd64Bit();await dedicatedEntryAndInvalidFiles();await settingsConnections();
  console.log('Public import: isolated canvases, dedicated entry, duplicates, 64-bit API/clone/export, malformed files and settings passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
