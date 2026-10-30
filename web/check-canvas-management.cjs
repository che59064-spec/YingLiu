// Canvas management uses isolated documents and fake DOM/HTTP; no browser or engine starts.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {webcrypto}=require('node:crypto');
const source=fs.readFileSync(__dirname+'/app.js','utf8').split('boot().catch')[0];
const html=fs.readFileSync(__dirname+'/index.html','utf8'),css=fs.readFileSync(__dirname+'/layout.css','utf8');
for(const id of ['canvas-scroll-left','canvas-scroll-right','canvas-actions'])assert.match(html,new RegExp('id="'+id+'"'));
assert.match(css,/\.canvas-tabs\s*\{[^}]*scrollbar-width:none/);
assert.match(css,/\.canvas-tabs::\-webkit-scrollbar\s*\{[^}]*display:none/);
const elements=new Map(),storage=new Map(),events={},toasts=[];let renderedTabs=[];
const document={activeElement:null};
function element(selector){
 if(elements.has(selector))return elements.get(selector);
 const classes=new Set(),listeners={};
 const el={id:selector.startsWith('#')?selector.slice(1):'',value:'',textContent:'',style:{},dataset:{},hidden:false,open:false,disabled:false,offsetWidth:28,offsetHeight:34,scrollWidth:0,clientWidth:0,scrollLeft:0,listeners,
  classList:{add(...names){names.forEach(name=>classes.add(name));},remove(...names){names.forEach(name=>classes.delete(name));},contains:name=>classes.has(name),toggle(name,force){const active=force??!classes.has(name);active?classes.add(name):classes.delete(name);return active;}},
  setAttribute(name,value){this[name]=value;},removeAttribute(name){delete this[name];},
  addEventListener(name,handler){listeners[name]=handler;},querySelector:element,querySelectorAll:selectAll,
  getBoundingClientRect(){return{left:this.left||100,top:50,bottom:84,width:120,height:34};},
  closest(selector){if(selector==='[data-canvas-id]'&&this.dataset.canvasId)return this;if(selector==='#canvas-tabs'&&this.dataset.canvasId)return element('#canvas-tabs');return null;},
  focus(){document.activeElement=this;},blur(){if(document.activeElement===this)document.activeElement=null;},select(){this.focus();},scrollIntoView(){this.scrolled=true;},
  scrollBy(options){this.lastScroll=options;this.scrollLeft=Math.max(0,Math.min(this.scrollWidth-this.clientWidth,this.scrollLeft+options.left));listeners.scroll?.();},
  showModal(){this.open=true;},close(){this.open=false;},showPopover(){throw new Error('InvalidStateError: element has no popover attribute');},hidePopover(){this.popoverOpen=false;},matches(selector){return selector===':popover-open'&&!!this.popoverOpen;},
  click(){if(!this.disabled)return this.onclick?.({target:this,preventDefault(){}});}
 };
 Object.defineProperty(el,'innerHTML',{get(){return this.markup||'';},set(value){this.markup=value;if(selector==='#canvas-tabs')renderedTabs=[...value.matchAll(/data-canvas-id="([^"]+)"/g)].map(match=>{const tab=element('tab:'+match[1]);tab.dataset.canvasId=match[1];return tab;});}});
 elements.set(selector,el);return el;
}
function selectAll(selector){if(selector==='[data-canvas-id]'||selector==='.canvas-tab')return renderedTabs;if(selector==='button:not(:disabled)')return [element('[data-canvas-rename]'),element('[data-canvas-delete]')].filter(button=>!button.disabled);return[];}
document.querySelector=element;document.querySelectorAll=selectAll;document.addEventListener=(name,handler)=>events[name]=handler;
document.body={insertAdjacentHTML(_place,markup){for(const match of markup.matchAll(/\bid="([^"]+)"/g))element('#'+match[1]);element('#canvas-tab-menu').hidden=true;}};
const context={window:{innerWidth:1280,innerHeight:720,addEventListener(name,handler){events['window:'+name]=handler;},matchMedia:()=>({matches:false})},document,location:{origin:'http://127.0.0.1:8770'},URL,Blob,AbortController,crypto:webcrypto,console,
 setTimeout:()=>0,clearTimeout(){},requestAnimationFrame:callback=>callback(),localStorage:{setItem:(key,value)=>storage.set(key,value),getItem:key=>storage.get(key)||null,removeItem:key=>storage.delete(key)}};
vm.createContext(context);
vm.runInContext(source+`\nwindow.check={state,activateCanvas,renderCanvasTabs,updateCanvasOverflow,renameCanvas,reorderCanvasTabs,deleteCanvas,bindCanvasTabs,canvasDeleteBlocked,projectStorageKey};renderCards=()=>{};renderInspector=()=>{};renderJobs=()=>{};applyViewport=()=>{};drawConnections=()=>{};renderMedia=()=>{};toast=message=>window.recordToast(message);`,context);
context.window.recordToast=message=>toasts.push(message);
const c=context.window.check,copy=value=>JSON.parse(JSON.stringify(value));let docs,order,active,calls,failPath;
function canvas(id,name){return{id,name,revision:0,cards:[],edges:[],viewport:{x:0,y:0,zoom:1}};}
function index(){return{items:order.map(id=>({id,name:docs[id].name,revision:docs[id].revision})),active_id:active};}
function setup(){
 docs={a:canvas('a','画布甲'),b:canvas('b','画布乙'),c:canvas('c','画布丙')};order=['a','b','c'];active='a';calls=[];failPath=null;storage.clear();
 c.state.canvasesAvailable=true;c.state.canvasBusy=false;c.state.importing=false;c.state.jobs=[];c.state.canvases=index().items;c.activateCanvas(copy(docs.a));
 context.mockApi=async(path,options={})=>{
  const body=options.body?JSON.parse(options.body):undefined;calls.push({path,method:options.method,body});if(path===failPath)throw new Error('写入失败');
  if(path==='/api/canvases/order'){assert.deepEqual(new Set(body.ids),new Set(order));order=[...body.ids];return copy(index());}
  const rename=path.match(/^\/api\/canvases\/([^/]+)\/name$/);
  if(rename){const target=docs[rename[1]];assert.equal(body.expectedRevision,target.revision);target.name=body.name;target.revision++;return copy(target);}
  const id=path.replace('/api/canvases/',''),target=docs[id];if(!target)throw new Error('Unexpected path '+path);
  if(options.method==='PUT'){assert.equal(body.revision,target.revision);docs[id]={...body,revision:target.revision+1};return copy(docs[id]);}
  if(options.method==='DELETE'){assert.equal(body.expectedRevision,target.revision);delete docs[id];order=order.filter(value=>value!==id);if(active===id)active=order[0];return{...copy(index()),deleted_id:id,active_canvas:copy(docs[active])};}
  return copy(target);
 };
 vm.runInContext('api=mockApi;',context);element('#canvas-scroll-left').hidden=element('#canvas-scroll-right').hidden=true;
}
async function checks(){
 setup();const tabs=element('#canvas-tabs'),left=element('#canvas-scroll-left'),right=element('#canvas-scroll-right');
 tabs.scrollWidth=1000;tabs.clientWidth=300;tabs.scrollLeft=0;c.updateCanvasOverflow();assert.equal(left.hidden,false);assert.equal(left.disabled,true);assert.equal(right.disabled,false);
 tabs.scrollLeft=700;c.updateCanvasOverflow();assert.equal(left.disabled,false);assert.equal(right.disabled,true);tabs.scrollWidth=200;c.updateCanvasOverflow();assert.equal(left.hidden,true);assert.equal(right.hidden,true);

 setup();const original=c.state.project;original.cards.push({id:'unsaved',type:'image',url:'/assets/a.png'});c.state.dirty=true;
 assert.equal(await c.reorderCanvasTabs(['c','a','b']),true);assert.deepEqual(copy(c.state.canvases.map(item=>item.id)),['c','a','b']);assert.equal(c.state.project,original);assert.equal(c.state.project.cards[0].id,'unsaved');assert.equal(c.state.dirty,true);assert.deepEqual(calls.map(call=>call.path),['/api/canvases/order']);
 failPath='/api/canvases/order';assert.equal(await c.reorderCanvasTabs(['b','a','c']),false);assert.deepEqual(copy(c.state.canvases.map(item=>item.id)),['c','a','b']);assert.equal(c.state.canvasBusy,false);

 setup();c.state.dirty=true;c.state.selected='keep-selection';let releaseMapping;context.mapping=new Promise(resolve=>releaseMapping=resolve);vm.runInContext("pendingBridgeWrite=mapping.then(()=>{state.project.cards.push({id:'late',type:'image',url:'/assets/late.png'});state.dirty=true;});",context);
 const renaming=c.renameCanvas('a','新的名称');await Promise.resolve();assert.equal(calls.length,0,'pending bridge changes must be saved before renaming');releaseMapping();assert.equal(await renaming,true);vm.runInContext('pendingBridgeWrite=null;',context);
 assert.equal(docs.a.cards[0].id,'late');assert.equal(docs.a.name,'新的名称');assert.equal(c.state.project.name,'新的名称');assert.equal(c.state.selected,'keep-selection');assert.equal(c.state.project.revision,2);assert.equal(calls[0].method,'PUT');assert.match(calls[1].path,/\/name$/);
 const activeProject=c.state.project;assert.equal(await c.renameCanvas('b','非当前画布'),true);assert.equal(c.state.project,activeProject);assert.equal(c.state.project.name,'新的名称');assert.equal(docs.b.name,'非当前画布');
 assert.equal(await c.renameCanvas('b','   '),false);assert.equal(await c.renameCanvas('b','字'.repeat(81)),false);

 setup();storage.set('creation-canvas-project-v1:a','draft');storage.set('creation-canvas-project-v1:a:state','marker');assert.equal(await c.deleteCanvas('a'),true);
 assert.equal(c.state.project.id,'b');assert.equal(c.state.project.name,'画布乙');assert.equal(storage.has('creation-canvas-project-v1:a'),false);assert.equal(storage.has('creation-canvas-project-v1:a:state'),false);assert.equal(storage.get('creation-canvas-active-v1'),'b');assert.deepEqual(copy(c.state.canvases.map(item=>item.id)),['b','c']);assert.equal(calls.filter(call=>call.method!=='DELETE').length,0,'delete response already contains the next canvas');
 const kept=c.state.project;assert.equal(await c.deleteCanvas('c'),true);assert.equal(c.state.project,kept);const before=calls.length;assert.equal(await c.deleteCanvas('b'),false);assert.equal(calls.length,before,'the final canvas cannot be removed');
 setup();c.state.jobs=[{id:'job',canvas_id:'b',status:'queued'}];assert.equal(await c.deleteCanvas('b'),false);assert.equal(calls.length,0);assert.match(c.canvasDeleteBlocked('b'),/排队/);
 setup();failPath='/api/canvases/a';const old=c.state.project;storage.set('creation-canvas-project-v1:a','keep');assert.equal(await c.deleteCanvas('a'),false);assert.equal(c.state.project,old);assert.equal(storage.get('creation-canvas-project-v1:a'),'keep');assert.equal(c.state.canvases.length,3);

 setup();c.bindCanvasTabs();tabs.scrollWidth=1000;tabs.clientWidth=300;tabs.scrollLeft=0;c.updateCanvasOverflow();right.click();assert.equal(tabs.lastScroll.left,210);assert.equal(tabs.lastScroll.behavior,'smooth');context.window.matchMedia=()=>({matches:true});left.click();assert.equal(tabs.lastScroll.behavior,'auto');
 const actions=element('#canvas-actions'),menu=element('#canvas-tab-menu');menu.hidden=true;
 actions.click();assert.equal(menu.hidden,false,'canvas management opens in browsers that implement popovers');assert.equal(actions['aria-expanded'],'true');assert.ok(menu.style.left&&menu.style.top,'menu is positioned beside the button');
 actions.click();assert.equal(menu.hidden,true);assert.equal(actions['aria-expanded'],'false');
 const a=renderedTabs.find(tab=>tab.dataset.canvasId==='a'),b=renderedTabs.find(tab=>tab.dataset.canvasId==='b');
 tabs.listeners.contextmenu({target:b,clientX:120,clientY:90,preventDefault(){}});element('[data-canvas-rename]').click();assert.equal(element('#canvas-rename-dialog').open,true);assert.equal(element('#canvas-rename-input').value,'画布乙');element('#canvas-rename-input').value='标签改名';await element('#canvas-rename-form').onsubmit({preventDefault(){}});assert.equal(docs.b.name,'标签改名');assert.equal(element('#canvas-rename-dialog').open,false);
 const transfer={setData(){},effectAllowed:'',dropEffect:''};tabs.listeners.dragstart({target:a,dataTransfer:transfer,preventDefault(){}});tabs.listeners.dragover({target:b,dataTransfer:transfer,clientX:250,preventDefault(){}});tabs.listeners.drop({target:b,preventDefault(){}});await new Promise(setImmediate);assert.deepEqual(order,['b','a','c']);assert.ok(calls.some(call=>call.path==='/api/canvases/order'));
 assert.equal(calls.some(call=>call.path.includes('/comfy/')),false,'canvas management never submits or interrupts generation');
 console.log('Canvas management passed: arrow overflow without scrollbars, persistent drag order, rename after pending saves, safe delete and task protection.');
}
checks().catch(error=>{console.error(error);process.exitCode=1;});
