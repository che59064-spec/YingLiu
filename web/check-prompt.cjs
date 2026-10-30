// Run with node web/check-prompt.cjs. No service or cloud model is called.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {webcrypto}=require('node:crypto');
const source=fs.readFileSync(__dirname+'/app.js','utf8').split('boot().catch')[0];
const elements=new Map(),element=key=>{if(!elements.has(key))elements.set(key,{value:'',textContent:'',innerHTML:'',open:false,dataset:{},style:{},classList:{add(){},remove(){},toggle(){}},setAttribute(){},showModal(){this.open=true;},close(){this.open=false;}});return elements.get(key);};
const context={window:{},location:{origin:'http://127.0.0.1:8767'},crypto:webcrypto,URL,Blob,AbortController,console,setTimeout:()=>0,clearTimeout(){},requestAnimationFrame(){},localStorage:{setItem(){}},document:{querySelector:element,querySelectorAll:()=>[]}};
vm.createContext(context);
vm.runInContext(source+'\nwindow.check={state,promptOptimizer,optimizePrompt,applyPromptOptimization,cancelPromptOptimization,undoPromptOptimization,selectCard,promptSourcesHtml};renderCards=()=>{};renderInspector=()=>{};renderSingleCard=()=>{};toast=()=>{};',context);
const c=context.window.check;
const raw={nodes:[{id:1,type:'PrimitiveStringMultiline',widgets_values:['原图文字'],unknown:{keep:true}}]};
const field={id:'nodes/0:value',key:'value',label:'画面描述',role:'prompt',type:'textarea',editable:true};
function fixture(){
  c.cancelPromptOptimization();
  c.state.project={name:'测试项目',revision:0,cards:[{id:'a',type:'workflow',workflow:JSON.parse(JSON.stringify(raw)),fields:[field],values:{[field.id]:'保留黄色角色，只跟随动作'}},{id:'b',type:'workflow',workflow:JSON.parse(JSON.stringify(raw)),fields:[field],values:{[field.id]:'另一个画面'}},{id:'ref',type:'image',url:'/assets/private.png',assetId:'secret-asset-id',name:'角色.png'}],edges:[{id:'ref-edge',from:'ref',to:'a'}]};
  c.selectCard('a');return c.state.project.cards[0];
}
function setApi(handler){context.mockApi=handler;vm.runInContext('api=mockApi;',context);}
async function main(){
  let card=fixture(),postCount=0;
  setApi(async(path,options={})=>{
    if(!options.method)return{configured:true,model:'agnes-text'};
    postCount++;const payload=JSON.parse(options.body);assert.equal(path,'/api/prompt-optimizer');assert.equal(options.timeoutMs,240000);
    assert.deepEqual(payload.references,[{type:'image',name:'角色.png',assetId:'secret-asset-id',url:'/assets/private.png'}]);
    return{prompt:'Optimized identity and motion',translation:'完整的中文角色与动作对照',model:'agnes-text',profile:'h3_ref',media:[{type:'video',start:5,end:10,frames:12}],sources:[{title:'官方',url:'https://example.org/guide'}]};
  });
  await c.optimizePrompt('a');assert.equal(card.values[field.id],'保留黄色角色，只跟随动作','preview must not apply automatically');
  assert.equal(element('#prompt-result').value,'Optimized identity and motion');
  assert.equal(element('#prompt-translation').value,'完整的中文角色与动作对照');
  assert.match(element('#prompt-media').textContent,/5–10 秒.*12 帧/);
  c.cancelPromptOptimization();assert.equal(card.values[field.id],'保留黄色角色，只跟随动作','cancel must preserve original');
  await c.optimizePrompt('a');element('#prompt-result').value='人工确认后的优化';assert.equal(c.applyPromptOptimization(),true);
  assert.equal(card.promptOptimization.translation,'完整的中文角色与动作对照');
  assert.equal(card.promptOptimization.translationFor,'Optimized identity and motion');
  assert.equal(card.values[field.id],'人工确认后的优化');assert.equal(c.state.project.cards[1].values[field.id],'另一个画面');assert.deepEqual(JSON.parse(JSON.stringify(card.workflow)),raw,'applying must not change raw workflow');
  assert.equal(c.undoPromptOptimization('a'),true);assert.equal(card.values[field.id],'保留黄色角色，只跟随动作');
  assert.equal(card.promptOptimization,undefined);

  async function deferredChange(change){
    card=fixture();let release,calls=0;
    setApi(async(_path,options={})=>{if(!options.method)return{configured:true,model:'agnes-text'};calls++;return new Promise(resolve=>{release=resolve;});});
    const pending=c.optimizePrompt('a');await Promise.resolve();await c.optimizePrompt('a');assert.equal(calls,1,'one paid request at a time');
    change(card);release({prompt:'迟到结果'});await pending;assert.equal(c.applyPromptOptimization(),false);return card;
  }
  card=await deferredChange(old=>{old.values[field.id]='请求期间手动改写';});assert.equal(card.values[field.id],'请求期间手动改写');
  card=await deferredChange(()=>{c.selectCard('b');c.selectCard('a');});assert.equal(card.values[field.id],'保留黄色角色，只跟随动作','switching away and back invalidates a pending preview');
  const replacement={name:'导入项目',revision:4,cards:[],edges:[]};await deferredChange(()=>{c.state.project=replacement;});assert.equal(c.state.project,replacement,'late result must not cross documents');
  card=fixture();let release;
  setApi(async(_path,options={})=>!options.method?{configured:true}:new Promise(resolve=>{release=resolve;}));
  const cancelled=c.optimizePrompt('a');for(let turn=0;turn<8&&!release;turn++)await Promise.resolve();assert.equal(typeof release,'function');c.cancelPromptOptimization();release({prompt:'取消后的结果'});await cancelled;
  assert.equal(card.values[field.id],'保留黄色角色，只跟随动作');assert.equal(element('#prompt-dialog').open,false,'late response must not reopen cancelled dialog');
  card=fixture();setApi(async(_path,options={})=>{if(!options.method)return{configured:true};throw new Error('服务暂时不可用');});await c.optimizePrompt('a');
  assert.equal(card.values[field.id],'保留黄色角色，只跟随动作');assert.equal(c.promptOptimizer.busy,false);assert.equal(element('#prompt-apply').disabled,true);
  setApi(async()=>({configured:false,message:'Agnes 尚未连接'}));await c.optimizePrompt('a');assert.match(element('#prompt-status').textContent,/尚未连接/);
  assert.equal(c.promptSourcesHtml([{url:'javascript:alert(1)',title:'bad'}]),'');
  console.log('提示优化检查通过：中文对照与媒体范围、预览取消不改原文、仅采用引擎提示词、撤销、阻止重复请求、迟到结果与失败保护。');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
