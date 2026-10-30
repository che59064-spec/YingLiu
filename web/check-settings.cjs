// Execute the real settings controller against a fake DOM and fake local APIs.
// No browser, network, engine or AI request is started.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/settings.js','utf8');
const copy=value=>JSON.parse(JSON.stringify(value));

function fixture() {
  const elements=new Map(),calls=[];
  function node(id,dataset={}) {
    const handlers=new Map(),classes=new Set();
    const element={id,dataset,value:'',textContent:'',placeholder:'',checked:false,disabled:false,hidden:false,open:false,
      attributes:{},valid:true,
      classList:{toggle(name,on){if(on??!classes.has(name))classes.add(name);else classes.delete(name);},contains:name=>classes.has(name)},
      reportValidity(){return this.valid;},
      setAttribute(name,value){this.attributes[name]=value;},
      addEventListener(type,fn){handlers.set(type,[...(handlers.get(type)||[]),fn]);},
      fire(type){for(const fn of handlers.get(type)||[])fn({target:this});},
      showModal(){this.open=true;},close(){this.open=false;this.fire('close');}};
    Object.defineProperty(element,'innerHTML',{get(){return '';},set(){throw Error('Settings must never write innerHTML');}});
    elements.set(id,element);return element;
  }
  for(const id of ['settings-dialog','settings-status','ai-provider','ai-base','ai-model','ai-format','ai-vision','ai-key','ai-key-state',
    'ai-clear-key','ai-save','ai-test','ai-settings-panel','engine-settings-panel','setup-engine-url','setup-engine-root','setup-engine-python',
    'setup-engine-port','setup-engine-auto','app-settings','prompt-settings','engine-settings-link','connection-dialog','settings-close','engine-save'])node(id);
  const tabs=[node('tab-ai',{settingsTab:'ai'}),node('tab-engine',{settingsTab:'engine'})];
  const controls=['ai-provider','ai-base','ai-model','ai-format','ai-vision','ai-key','setup-engine-url','setup-engine-root',
    'setup-engine-python','setup-engine-port','setup-engine-auto','ai-save','engine-save','ai-test'].map(id=>elements.get(id));
  const dialog=elements.get('settings-dialog');
  dialog.querySelectorAll=selector=>selector==='[data-settings-tab]'?tabs:
    selector==='input,select,button[data-save],#ai-test'?controls:[];
  const f={elements,calls,tabs,dialog,controls,key:'saved-backend-key-never-returned',
    config:{provider:'openai',label:'兼容接口',baseUrl:'https://provider.example/v1',model:'custom-model',
      responseFormat:'prompt',vision:false,configured:true,hasKey:true,keySource:'local'},
    engine:{comfy_url:'http://127.0.0.1:8188',auto_start:false,comfy_port:8188},
    saveGate:null,testGate:null,failNext:null,el:id=>elements.get(id)};
  const publicConfig=()=>({...copy(f.config),apiKey:'unexpected-response-key-must-not-be-displayed'});
  const window={CanvasApp:{state:{project:{cards:[{name:'private canvas content'}]}},refreshStatus:async()=>{}}};
  const context={window,console,document:{querySelector:selector=>selector==='#settings-dialog'?dialog:null,getElementById:id=>elements.get(id)},
    api:async(path,options={})=>{
      const method=options.method||'GET',body=options.body?JSON.parse(options.body):null;
      calls.push({path,method,body,options});
      if(f.failNext){const error=f.failNext;f.failNext=null;throw error;}
      if(path==='/api/ai-provider'&&method==='GET')return publicConfig();
      if(path==='/api/settings'&&method==='GET')return copy(f.engine);
      if(path==='/api/ai-provider'&&method==='PUT'){
        if(f.saveGate)await f.saveGate;
        const same=body.provider===f.config.provider&&body.baseUrl===f.config.baseUrl;
        if(body.clearKey)f.key='';else if(body.apiKey)f.key=body.apiKey;else if(!same)f.key='';
        f.config={...f.config,...body,hasKey:!!f.key,configured:!!body.model};delete f.config.apiKey;delete f.config.clearKey;
        return publicConfig();
      }
      if(path==='/api/ai-provider/test'&&method==='POST'){
        if(f.testGate)await f.testGate;
        return{ok:true,message:'连接成功'};
      }
      if(path==='/api/settings'&&method==='PUT'){f.engine={...f.engine,...body};return copy(f.engine);}
      throw Error('Unexpected request: '+method+' '+path);
    }};
  vm.createContext(context);vm.runInContext(source,context);
  f.window=window;return f;
}

function assertNoSecretInDisplay(f) {
  for(const node of f.elements.values())for(const key of ['textContent','placeholder']){
    assert.doesNotMatch(String(node[key]),/saved-backend-key|unexpected-response-key|typed-test-secret/);
  }
}

async function checks() {
  const f=fixture();
  assert.equal(f.calls.length,0,'loading the controller never contacts a provider');
  await f.window.CanvasSettings.open();
  assert.equal(f.dialog.open,true);
  assert.deepEqual(f.calls.map(({path,method})=>[path,method]),[['/api/ai-provider','GET'],['/api/settings','GET']]);
  assert.equal(f.el('ai-key').value,'','an existing secret must not be filled into the DOM');
  assert.match(f.el('ai-key').placeholder,/留空保留/);assertNoSecretInDisplay(f);

  f.calls.length=0;f.el('ai-key').value='typed-test-secret';
  await f.el('ai-save').onclick();
  assert.equal(f.calls.length,1);assert.equal(f.calls[0].path,'/api/ai-provider');assert.equal(f.calls[0].method,'PUT');
  assert.equal(f.calls[0].body.apiKey,'typed-test-secret');assert.equal(f.key,'typed-test-secret');
  assert.equal(f.el('ai-key').value,'');assertNoSecretInDisplay(f);

  f.calls.length=0;let releaseSave,releaseTest;
  f.saveGate=new Promise(resolve=>{releaseSave=resolve;});f.testGate=new Promise(resolve=>{releaseTest=resolve;});
  f.el('ai-key').value='typed-test-secret';
  const testing=f.el('ai-test').onclick();
  for(const control of f.controls)assert.equal(control.disabled,true,'a pending save locks all editable controls');
  assert.equal(f.el('ai-clear-key').disabled,true);
  await f.el('ai-test').onclick();await f.el('ai-save').onclick();
  assert.equal(f.calls.length,1,'repeated clicks while busy cannot create another save or test');
  releaseSave();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(f.calls.length,2);assert.equal(f.calls[1].path,'/api/ai-provider/test');
  assert.equal(f.calls.filter(call=>call.path==='/api/ai-provider/test').length,1,'one manual click makes one test request');
  assert.deepEqual(f.calls[1].body,{});
  assert.equal(f.calls[1].options.body,'{}','connection tests never receive canvas, media, prompt or API keys');
  assert.equal(f.el('ai-key').value,'');
  for(const control of f.controls)assert.equal(control.disabled,true,'controls stay locked through the test');
  releaseTest();await testing;f.saveGate=null;f.testGate=null;
  for(const control of f.controls)assert.equal(control.disabled,false);
  assert.equal(f.calls.some(call=>call.path.includes('prompt-optimizer')||call.path.includes('/api/run')||call.path.includes('/api/assets')),false);

  f.calls.length=0;f.el('ai-key').value='unfinished-secret';f.el('ai-provider').value='anthropic';f.el('ai-provider').onchange();
  assert.equal(f.el('ai-key').value,'','switching provider clears an unsaved credential');
  assert.equal(f.el('ai-base').value,'https://api.anthropic.com');assert.equal(f.calls.length,0);
  f.el('ai-provider').value='openai';f.el('ai-provider').onchange();
  assert.equal(f.el('ai-key').value,'','switching back does not recover or expose the retained credential');
  f.el('ai-base').value='https://different.example/v1';f.el('ai-base').fire('input');
  assert.doesNotMatch(f.el('ai-key').placeholder,/留空保留/);assert.equal(f.el('ai-clear-key').disabled,true);
  await f.el('ai-save').onclick();
  assert.equal(f.calls.length,1);assert.equal(f.calls[0].body.apiKey,'','a new service never receives the saved service credential');
  assert.equal(f.key,'');assert.equal(f.el('ai-key').value,'');

  f.el('ai-key').value='unfinished-secret';f.el('settings-close').onclick();
  assert.equal(f.dialog.open,false);assert.equal(f.el('ai-key').value,'');
  await f.window.CanvasSettings.open();f.el('ai-key').value='unfinished-secret';f.dialog.close();
  assert.equal(f.el('ai-key').value,'','native Escape/dialog close also clears the input');

  await f.window.CanvasSettings.open();f.calls.length=0;
  f.failNext=new Error('<img src=x onerror=malicious()>');
  await f.el('ai-save').onclick();
  assert.equal(f.el('settings-status').textContent,'<img src=x onerror=malicious()>');
  assert.equal(f.el('settings-status').classList.contains('error'),true);
  assert.equal(f.calls.length,1);assert.equal(f.calls.some(call=>call.path.endsWith('/test')),false,'failed saves must not test the old connection');
  for(const control of f.controls)assert.equal(control.disabled,false,'errors restore controls');
  assertNoSecretInDisplay(f);

  const invalid=fixture();await invalid.window.CanvasSettings.open();invalid.calls.length=0;invalid.el('ai-model').valid=false;
  await invalid.el('ai-test').onclick();assert.equal(invalid.calls.length,0,'invalid settings cannot submit a paid test');
  console.log('连接设置检查通过：无自动 AI 请求、保存与测试分离、忙碌锁、仅固定测试参数、服务切换不带旧密钥、关闭清空、错误文本安全。');
}
checks().catch(error=>{console.error(error);process.exitCode=1;});
