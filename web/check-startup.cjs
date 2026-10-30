// Execute the actual startup controller against controlled frames and animations.
// This is an offline VM test: no HWND, browser, service, engine or model is opened.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/startup.js','utf8');
const html=fs.readFileSync(__dirname+'/index.html','utf8');
const css=fs.readFileSync(__dirname+'/startup.css','utf8');
const native=fs.readFileSync(__dirname+'/../StartupWindow.cs','utf8');
const plain=value=>JSON.parse(JSON.stringify(value));

function fixture({native=true,frame=false,search='?canvas_startup=1',reduced=false,missingTarget=false,noAnimation=false,postThrows=false}={}) {
  const frames=[],messages=[],animations=[],events=new Map(),documentEvents=new Map();
  function element(name,bounds={left:0,top:0,width:1,height:1}) {
    const handlers=new Map();
    const node={name,hidden:true,textContent:'',style:{},getBoundingClientRect:()=>({...bounds}),
      addEventListener(type,fn){handlers.set(type,fn);},fire(type,event){handlers.get(type)?.(event);},
      querySelector(selector){return children.get(selector)||null;}};
    Object.defineProperty(node,'innerHTML',{get(){return '';},set(){throw Error('Startup errors must use textContent');}});
    if(!noAnimation)node.animate=(keyframes,options)=>{
      let resolve;
      const animation={node,keyframes:plain(keyframes),options:plain(options),cancelled:false,
        finished:new Promise(done=>resolve=done),cancel(){this.cancelled=true;},resolve(){resolve();}};
      animations.push(animation);return animation;
    };
    return node;
  }
  const children=new Map();
  const layer=element('layer'),symbol=element('symbol',{left:600,top:260,width:112,height:112});
  const wash=element('wash'),caption=element('caption'),status=element('status');
  const target=element('header-icon',{left:30,top:22,width:32,height:32});
  children.set('.startup-symbol',symbol);children.set('.startup-wash',wash);children.set('.startup-caption',caption);children.set('[role="status"]',status);
  const root={dataset:{},style:{}};
  const window={matchMedia:()=>({matches:reduced}),
    addEventListener(type,fn){events.set(type,fn);},removeEventListener(type,fn){if(events.get(type)===fn)events.delete(type);}};
  window.top=frame?{}:window;
  if(native)window.chrome={webview:{postMessage(message){if(postThrows)throw Error('Bridge unavailable');messages.push(plain(message));}}};
  const document={documentElement:root,hidden:false,
    getElementById:id=>id==='startup-layer'?layer:null,
    querySelector:selector=>selector==='.brand-mark img'&&!missingTarget?target:null,
    addEventListener(type,fn){documentEvents.set(type,fn);}};
  const context={window,document,location:{search},URLSearchParams,console,
    getComputedStyle:()=>({getPropertyValue:()=> '#e9e9ed'}),
    requestAnimationFrame:fn=>{frames.push(fn);return frames.length;}};
  vm.createContext(context);vm.runInContext(source,context);
  const f={window,document,root,layer,symbol,wash,caption,status,frames,messages,animations,
    async frame(){const callbacks=frames.splice(0);for(const fn of callbacks)fn(16);await Promise.resolve();await Promise.resolve();},
    async painted(){await this.frame();await this.frame();await Promise.resolve();},
    async complete(){for(const animation of animations)animation.resolve();for(let i=0;i<10;i++)await Promise.resolve();},
    resize(){events.get('resize')?.();},hide(){document.hidden=true;documentEvents.get('visibilitychange')?.();},events};
  return f;
}

async function checks() {
  for(const options of [{native:false},{frame:true},{search:''},{search:'?canvas_startup=0'},
    {search:'?canvas_startup=-1'},{search:'?canvas_startup=1x'}]) {
    const f=fixture(options);
    assert.equal(f.window.CanvasStartup,undefined,'ordinary browsers/iframes/invalid attempts must not activate startup');
    assert.equal(f.layer.hidden,true);assert.equal(f.frames.length,0);assert.equal(f.messages.length,0);
    assert.equal(f.root.dataset.startup,undefined,'normal browsing must not hide the real workspace');
  }

  const f=fixture();
  assert.equal(f.layer.hidden,false);assert.equal(f.root.dataset.startup,'pending');
  assert.equal(f.window.CanvasStartup.notify('ready'),true);
  assert.equal(f.messages.length,0,'ready waits for an actual first-paint opportunity');
  await f.frame();assert.equal(f.messages.length,0,'one frame cannot claim the handoff has painted');
  await f.frame();
  assert.deepEqual(f.messages,[{type:'startup',state:'painted',background:'#e9e9ed'},{type:'startup',state:'ready'}]);
  assert.equal(f.root.dataset.startup,'entering');assert.equal(f.animations.length,3);
  assert.deepEqual(f.animations.map(a=>a.options.duration),[300,240,150]);
  assert.match(f.animations[0].keyframes[1].transform,/scale\(0\.285714/,'the moving logo ends at the existing header icon size');
  assert.match(f.animations[0].keyframes[1].transform,/-610px.*-278px/,'the shared logo ends at the real header icon center');
  f.window.CanvasStartup.notify('ready');
  assert.equal(f.messages.length,2,'duplicate readiness must not restart or duplicate the transition');
  await f.complete();
  assert.equal(f.layer.hidden,true);assert.equal(f.root.dataset.startup,undefined);
  assert.ok(f.animations.every(a=>a.cancelled),'completed animation handles must be released');
  assert.equal(f.events.has('resize'),false);
  f.window.CanvasStartup.notify('error','late error');assert.equal(f.messages.length,2,'late callbacks cannot put the splash back');

  const waiting=fixture();await waiting.painted();
  assert.deepEqual(waiting.messages,[{type:'startup',state:'painted',background:'#e9e9ed'}]);
  assert.equal(waiting.root.dataset.startup,'pending');assert.equal(waiting.animations.length,0,'first paint is not data readiness');
  assert.equal(waiting.window.CanvasStartup.notify('unexpected'),false);
  waiting.window.CanvasStartup.notify('ready');await waiting.complete();assert.equal(waiting.layer.hidden,true);

  for(const options of [{reduced:true},{missingTarget:true},{noAnimation:true}]) {
    const quiet=fixture(options);await quiet.painted();quiet.window.CanvasStartup.notify('ready');
    assert.equal(quiet.layer.hidden,true,'reduced motion or unavailable geometry/animation must leave a usable canvas');
    assert.equal(quiet.root.dataset.startup,undefined);assert.equal(quiet.animations.length,0);
    assert.equal(quiet.messages.filter(m=>m.state==='ready').length,1);
  }

  for(const interrupt of ['resize','hide']) {
    const interrupted=fixture();await interrupted.painted();interrupted.window.CanvasStartup.notify('ready');
    interrupted[interrupt]();assert.equal(interrupted.layer.hidden,true);
    assert.equal(interrupted.root.dataset.startup,undefined);assert.ok(interrupted.animations.every(a=>a.cancelled));
    await interrupted.complete();assert.equal(interrupted.messages.filter(m=>m.state==='ready').length,1);
  }

  for(const beforePaint of [true,false]) {
    const failed=fixture();if(!beforePaint)await failed.painted();
    failed.window.CanvasStartup.notify('error','<img onerror="private">\n'+ 'x'.repeat(500));
    await failed.painted();
    assert.equal(failed.root.dataset.startup,'error');assert.equal(failed.layer.hidden,false);
    assert.equal(failed.animations.length,0);assert.match(failed.status.textContent,/^<img/);
    assert.ok(failed.status.textContent.length<=400);
    failed.window.CanvasStartup.notify('ready');
    assert.equal(failed.messages.filter(m=>m.state==='ready').length,0,'late ready cannot erase an earlier loading failure');
    assert.equal(failed.messages.filter(m=>m.state==='error').length,1);
  }
  const retry=fixture({search:'?canvas_startup=2',reduced:true});await retry.painted();retry.window.CanvasStartup.notify('ready');
  assert.equal(retry.layer.hidden,true,'a new attempt owns fresh state after native retry reloads it');

  const drag=fixture();let prevented=0;
  drag.layer.fire('mousedown',{button:0,target:{closest:()=>null},preventDefault(){prevented++;}});
  assert.deepEqual(drag.messages,[{type:'window',action:'drag'}]);assert.equal(prevented,1);
  drag.layer.fire('mousedown',{button:0,target:{closest:()=>({})},preventDefault(){throw Error('Interactive controls must not drag');}});
  await drag.painted();drag.window.CanvasStartup.notify('ready');
  drag.layer.fire('mousedown',{button:0,target:{closest:()=>null},preventDefault(){throw Error('Exiting surface must not capture drag');}});
  await drag.complete();
  const brokenBridge=fixture({postThrows:true,reduced:true});await brokenBridge.painted();brokenBridge.window.CanvasStartup.notify('ready');
  assert.equal(brokenBridge.layer.hidden,true,'a notification failure must not trap the browser surface');

  assert.equal((html.match(/id="startup-layer"/g)||[]).length,1);
  assert.equal((html.match(/data-window-action="(?:minimize|maximize|close)"/g)||[]).length,3,'only header window controls may exist');
  assert.match(css,/\.startup-wash\s*\{[^}]*background:var\(--bg\)/,'the startup surface shares the real skin background');
  assert.match(css,/#startup-layer\[hidden\]\s*\{\s*display:none/);
  assert.doesNotMatch(css,/clip-path|mask-image/,'the transition must not split the page into curtains');
  assert.doesNotMatch(native,/RoundButton|Region\.Exclude|exitClock/,'native fallback must not add duplicate controls or a second animation');
  console.log('Startup continuity passed: single header controls, painted/ready ordering, shared-logo transition, safe errors/retry, reduced motion and interruption, ordinary-browser isolation.');
}
checks().catch(error=>{console.error(error);process.exitCode=1;});
