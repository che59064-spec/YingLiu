// No network or generation: inline edits stay focused and do not open the detailed sidebar.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const elements=new Map();
function element(key){if(!elements.has(key)){const classes=new Set();elements.set(key,{dataset:{},style:{},outerHTML:'untouched',classList:{add:v=>classes.add(v),remove:v=>classes.delete(v),contains:v=>classes.has(v),toggle(v,on){if(on??!classes.has(v))classes.add(v);else classes.delete(v);}},setAttribute(){}});}return elements.get(key);}
const context={window:{},console,URL,document:{activeElement:null,querySelector:element,querySelectorAll:()=>[]},setTimeout:()=>0,clearTimeout(){},requestAnimationFrame(){},localStorage:{setItem(){}}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(__dirname+'/app.js','utf8').split('boot().catch')[0]+`
window.check={state,selectCard,fieldHtml,renderQuickControls,updateField,renderSingleCard,readableJobError};
renderInspector=()=>{};drawConnections=()=>{};projectChanged=()=>{};renderMedia=()=>{};`,context);
const c=context.window.check;
const prompt={id:'prompt',key:'value',label:'画面描述',role:'prompt',type:'textarea',editable:true};
const seed={id:'seed',key:'noise_seed',label:'变化编号',type:'integer',editable:true};
const advanced={id:'advanced',key:'cfg',label:'专业参数',type:'number',advanced:true,editable:true};
const card={id:'a',type:'workflow',name:'工作流',fields:[prompt,seed,advanced],values:{prompt:'原文',seed:5,advanced:1},workflow:{nodes:[]},x:0,y:0};
c.state.project={cards:[card],edges:[],viewport:{x:0,y:0,zoom:1}};c.state.rightTab='media';
c.selectCard('a');assert.equal(c.state.rightTab,'media','selection must not force detailed settings');
const quick=c.renderQuickControls(card);assert.match(quick,/quick-a-prompt/);assert.match(quick,/quick-a-seed/);assert.doesNotMatch(quick,/专业参数/);
assert.match(c.fieldHtml(card,prompt),/inspector-a-prompt/,'sidebar IDs must differ from node IDs');
const node=element('.card[data-id="a"]');node.dataset.id='a';
const input={dataset:{field:'prompt'},type:'textarea',value:'持续输入第一段',closest:selector=>selector==='[data-quick-card]'?{dataset:{quickCard:'a'}}:selector==='.card'?node:null};
context.document.activeElement=input;node.outerHTML='editor still mounted';
for(const value of ['持续输入第一段','持续输入第一段第二段']){input.value=value;c.updateField(input);c.renderSingleCard('a');assert.equal(node.outerHTML,'editor still mounted','typing must preserve the active input DOM');}
assert.equal(card.values.prompt,'持续输入第一段第二段');assert.equal(card.values.seed,5);assert.deepEqual(card.workflow,{nodes:[]});
context.document.activeElement=null;c.renderSingleCard('a');assert.notEqual(node.outerHTML,'editor still mounted','normal rendering resumes after editing');
assert.equal(c.readableJobError('[["execution_interrupted",{}]]'),'生成已停止，可以调整后重新生成');
console.log('节点界面检查通过：点选不占右栏、快调权限、唯一字段ID、连续输入保留光标、失焦刷新与简洁错误。');
