// No service, project or model calls. Exercise native/fallback dismissal and persistence.
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync(__dirname + '/appearance.js', 'utf8');

function fixture({native = false, saved = null, blocked = false} = {}) {
  const handlers = new Map(), writes = [], options = [], root = {dataset:{}};
  const listeners = (node, type, callback) => {const key = node.id + ':' + type; handlers.set(key, [...(handlers.get(key) || []), callback]);};
  const node = (id, dataset = {}) => ({id,dataset,attributes:{},hidden:false,children:[],focus(){document.activeElement=this;},setAttribute(key,value){this.attributes[key]=value;},addEventListener(type,callback){listeners(this,type,callback);},contains(target){return this===target||this.children.includes(target);},closest(selector){return selector==='[data-skin-option]'&&this.dataset.skinOption?this:null;}});
  const toggle = node('skin-toggle'), menu = node('skin-menu');
  for (const skin of ['glass','midnight','sage','sand']) options.push(node(skin,{skinOption:skin}));
  menu.children = options;
  menu.querySelectorAll = () => options;
  menu.querySelector = () => options.find(item => item.attributes['aria-pressed'] === 'true');
  function fire(target,type,event={}) {
    (handlers.get(target.id+':'+type)||[]).forEach(handler=>handler(event));
    if(native&&target===toggle&&type==='click'&&target.attributes.popovertarget) {
      fire(menu,'toggle',{newState:target.attributes['aria-expanded']==='true'?'closed':'open'});
    }
  }
  if (native) {
    menu.showPopover = () => fire(menu,'toggle',{newState:'open'});
    menu.hidePopover = () => fire(menu,'toggle',{newState:'closed'});
  }
  const document = {id:'document',documentElement:root,activeElement:null,getElementById:id=>id==='skin-toggle'?toggle:id==='skin-menu'?menu:null,addEventListener(type,callback){listeners(this,type,callback);}};
  const window = {id:'window',addEventListener(type,callback){listeners(this,type,callback);}};
  const localStorage = {getItem(){if(blocked)throw Error('unavailable');return saved;},setItem(key,value){if(blocked)throw Error('unavailable');writes.push([key,value]);}};
  const context = {window,document,localStorage};
  vm.createContext(context);vm.runInContext(source,context);
  return {api:window.CanvasSkins,root,toggle,menu,options,writes,document,window,fire};
}

for (const native of [false,true]) {
  const f = fixture({native,saved:'midnight'});
  assert.equal(f.root.dataset.skin,'midnight');assert.equal(f.writes.length,0);
  assert.equal(f.api.init(),true);f.api.init();
  f.fire(f.toggle,'click');assert.equal(f.toggle.attributes['aria-expanded'],'true','init must not bind twice');
  assert.equal(f.document.activeElement,f.options[1]);
  f.fire(f.menu,'click',{target:f.options[2]});
  assert.equal(f.root.dataset.skin,'sage');assert.equal(f.options[2].attributes['aria-pressed'],'true');
  assert.equal(f.toggle.attributes['aria-expanded'],'false');assert.equal(f.document.activeElement,f.toggle);
  assert.deepEqual(f.writes,[['creation-canvas-skin-v1','sage']]);
  f.fire(f.toggle,'click');f.fire(f.menu,'keydown',{key:'End',preventDefault(){}});assert.equal(f.document.activeElement,f.options[3]);
  f.fire(f.document,'keydown',{key:'Escape',preventDefault(){}});assert.equal(f.toggle.attributes['aria-expanded'],'false');
  f.fire(f.toggle,'click');
  if(native) f.fire(f.menu,'toggle',{newState:'closed'});
  else f.fire(f.document,'pointerdown',{target:{}});
  assert.equal(f.toggle.attributes['aria-expanded'],'false','outside dismissal must sync the trigger');
  f.fire(f.window,'storage',{key:'creation-canvas-skin-v1',newValue:'sand'});
  assert.equal(f.root.dataset.skin,'sand');assert.equal(f.writes.length,1,'storage synchronization never writes back');
}
const blocked = fixture({saved:'invalid',blocked:true});
assert.equal(blocked.root.dataset.skin,'glass');blocked.api.init();
assert.equal(blocked.api.apply('midnight'),'midnight');assert.equal(blocked.root.dataset.skin,'midnight');
assert.equal(blocked.api.apply('javascript:alert(1)'),'glass');
console.log('皮肤检查通过：四种皮肤、记住选择、禁用存储、键盘选择、原生及备用弹层关闭、跨窗口同步。');
