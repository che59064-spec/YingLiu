/* 映流 · 原生浏览器实现。每个工作流保留完整原图，外层仅保存创作编排。 */
'use strict';
const $ = (s, p = document) => p.querySelector(s);
const $$ = (s, p = document) => [...p.querySelectorAll(s)];
const icon = name => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const canvasJSON = window.CanvasJSON || JSON;
const clone = value => canvasJSON.parse(canvasJSON.stringify(value));
const uid = () => crypto.randomUUID();
function notifyStartup(status,message) {
  try {
    if(window.CanvasStartup?.notify)return window.CanvasStartup.notify(status,message);
    if(window.top!==window||!window.chrome?.webview?.postMessage||!['ready','error'].includes(status))return false;
    window.chrome.webview.postMessage({type:'startup',state:status,...(status==='error'?{message:String(message||'画布载入失败，请重新打开映流。').replace(/\s+/g,' ').slice(0,400)}:{})});
    return true;
  }catch{return false;}
}
const LOCAL_KEY = 'creation-canvas-project-v1';
const ACTIVE_CANVAS_KEY = 'creation-canvas-active-v1';
const RECOVERY_KEY = 'creation-canvas-recoveries-v1';
const state = {project:{version:1,name:'我的创作',revision:0,cards:[],edges:[],viewport:{x:110,y:95,zoom:1}},catalog:[],selected:null,category:'全部',search:'',mode:'simple',jobs:[],connected:false,bridgeConnected:false,engineUrl:'http://127.0.0.1:8188',bridgeRevision:0,bridgeActive:null,bridgeHash:null,saving:false,dirty:false,space:false,connecting:null,drag:null,starting:false,booted:false};
let saveTimer, toastTimer, pollTimer, bridgeTimer, pendingSave, lastPersist = '';
let changeSerial = 0;
let pendingBridgeWrite = null;
let engineStartPending = false, reloadEngineFrame = false, statusRequest = null;
let engineFrameDisconnected = false, pollRequest = null;
const MEDIA_DRAG = 'application/x-creation-canvas-media';
Object.assign(state,{media:[],mediaFilter:'all',mediaSearch:'',mediaError:'',rightTab:'media'});
let draggedMediaKey = null;
let promptSelectionSerial = 0;
const promptOptimizer = {busy:false,requestId:0,session:null};
let layoutUndo = null;
const pendingVideoFields = new WeakMap();
Object.assign(state,{canvases:[],canvasesAvailable:false,canvasBusy:false,recoveryDrafts:[],recoveryBusy:false});
function projectPath(project=state.project) {return project.id?`/api/canvases/${encodeURIComponent(project.id)}`:'/api/project';}
function projectStorageKey(project=state.project) {return project.id?`${LOCAL_KEY}:${project.id}`:LOCAL_KEY;}
function projectContent(project) {
  const document={version:1,...normalizeProject(project)};delete document.revision;delete document.savedAt;delete document.expectedRevision;
  return canvasJSON.stringify(document,(_,value)=>value&&typeof value==='object'&&!Array.isArray(value)?Object.fromEntries(Object.entries(value).sort(([a],[b])=>a.localeCompare(b))):value);
}
function setSaveStatus(message) {
  const element=$('#save-state'),count=state.recoveryDrafts.length;
  element.textContent=count?`恢复草稿（${count}）`:message;element.title=count?`${message}；点击将未保存内容另存为独立画布`:message;element.tabIndex=count?0:-1;element.style.display=count?'inline':'';element.style.cursor=count?'pointer':'';element.style.textDecoration=count?'underline':'';
  if(count){element.setAttribute('role','button');element.setAttribute('aria-label',`恢复 ${count} 份未保存的画布草稿`);element.onclick=restoreRecoveredDrafts;element.onkeydown=event=>{if(['Enter',' '].includes(event.key)){event.preventDefault();restoreRecoveredDrafts();}};}
  else{element.removeAttribute?.('role');element.removeAttribute?.('aria-label');element.onclick=null;element.onkeydown=null;}
}
function storeRecoveries() {localStorage.setItem(RECOVERY_KEY,canvasJSON.stringify(state.recoveryDrafts));state.recoveryDrafts.forEach(entry=>entry.persisted=true);}
function loadRecoveries() {try{const saved=canvasJSON.parse(localStorage.getItem(RECOVERY_KEY));state.recoveryDrafts=Array.isArray(saved)?saved.filter(entry=>entry?.project?.cards&&entry.project.edges).map(entry=>({...entry,persisted:true})):[];}catch(error){console.warn('恢复草稿暂时无法读取',error);}}
function preserveRecovery(project) {
  if(state.recoveryDrafts.some(entry=>entry.sourceId===(project.id||null)&&projectContent(entry.project)===projectContent(project)))return;
  state.recoveryDrafts.push({id:uid(),sourceId:project.id||null,project:clone(project),persisted:false});
  try{storeRecoveries();}catch(error){console.warn('恢复副本未能另存，保留原浏览器备份',error);}
}
function resolveProjectDraft(serverProject) {
  try {
    const key=projectStorageKey(serverProject),draft=canvasJSON.parse(localStorage.getItem(key));
    if(!draft?.cards||!draft.edges||(draft.id||null)!==(serverProject.id||null))return{project:serverProject,dirty:false};
    if(projectContent(draft)===projectContent(serverProject))return{project:serverProject,dirty:false};
    let marker=null;try{marker=canvasJSON.parse(localStorage.getItem(key+':state'));}catch{}
    if(marker?.dirty&&marker.baseRevision===serverProject.revision)return{project:{...draft,revision:serverProject.revision},dirty:true};
    // A known clean backup may be older. An unmarked legacy draft has no such guarantee.
    if(marker?.dirty===false)return{project:serverProject,dirty:false};
    preserveRecovery(draft);
  }catch(error){console.warn('画布草稿读取失败，原备份已保留',error);}
  return{project:serverProject,dirty:false};
}
async function restoreRecoveredDrafts() {
  if(state.recoveryBusy||state.canvasBusy)return;
  state.recoveryBusy=true;const restored=[];
  try {
    for(const entry of [...state.recoveryDrafts]){
      if(!state.canvasesAvailable){const url=URL.createObjectURL(new Blob([canvasJSON.stringify(entry.project,null,2)],{type:'application/json'})),link=document.createElement('a');link.href=url;link.download=`恢复草稿_${entry.project.name||'画布'}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);toast('草稿已导出；更新软件后可导入为独立画布。');break;}
      let target=entry.targetId?await api(`/api/canvases/${encodeURIComponent(entry.targetId)}`):null;target=target?.project||target;
      let document=target&&{...entry.project,id:target.id,revision:target.revision,name:target.name};
      if(!target||target.revision>0&&projectContent(target)!==projectContent(document)){
        target=await api('/api/canvases',{method:'POST',body:canvasJSON.stringify({name:`${entry.project.name||'画布'} · 恢复草稿`.slice(0,80)})});target=target.project||target;
        entry.targetId=target.id;try{storeRecoveries();}catch(error){console.warn('恢复位置暂未记入浏览器备份',error);}
        document={...entry.project,id:target.id,revision:target.revision,name:target.name};
      }
      const saved=projectContent(target)===projectContent(document)?target:await api(`/api/canvases/${encodeURIComponent(target.id)}`,{method:'PUT',body:canvasJSON.stringify(document)});
      updateCanvasItem(saved);const remaining=state.recoveryDrafts.filter(item=>item.id!==entry.id);localStorage.setItem(RECOVERY_KEY,canvasJSON.stringify(remaining));state.recoveryDrafts=remaining;restored.push(saved.name);renderCanvasTabs();
    }
    if(restored.length)toast('未保存的内容已另存为恢复画布，可在左上角切换查看。',6500);
  }catch(error){toast(`草稿仍已保留，恢复未完成：${error.message}。可点击“恢复草稿”重试。`,7500);}
  finally{state.recoveryBusy=false;setSaveStatus(state.dirty?'未保存':'已读取本机画布');}
}
function jobForProject(job,project=state.project) {return !job.canvas_id||job.canvas_id===project.id;}
function updateCanvasItem(project=state.project) {
  if(!project.id)return;
  const item={id:project.id,name:project.name||'未命名画布',revision:project.revision,savedAt:project.savedAt};
  const index=state.canvases.findIndex(canvas=>canvas.id===item.id);
  if(index<0)state.canvases.push(item);else state.canvases[index]=item;
}
function updateCanvasOverflow() {
  const tabs=$('#canvas-tabs'),left=$('#canvas-scroll-left'),right=$('#canvas-scroll-right');if(!tabs||!left||!right)return;
  const occupied=left.hidden?0:(Number(left.offsetWidth)||28)+(Number(right.offsetWidth)||28)+12;
  const overflow=Number(tabs.scrollWidth)>Number(tabs.clientWidth)+occupied+2;
  left.hidden=right.hidden=!overflow;
  const end=Math.max(0,Number(tabs.scrollWidth)-Number(tabs.clientWidth));
  left.disabled=state.canvasBusy||!overflow||Number(tabs.scrollLeft)<=1;
  right.disabled=state.canvasBusy||!overflow||Number(tabs.scrollLeft)>=end-1;
}
function renderCanvasTabs() {
  const tabs=$('#canvas-tabs');if(!tabs)return;
  const items=state.canvases.length?state.canvases:[{id:state.project.id||'legacy',name:state.project.name}];
  tabs.innerHTML=items.map(item=>`<button type="button" class="canvas-tab" role="tab" data-canvas-id="${esc(item.id)}" draggable="${state.canvasesAvailable&&!state.canvasBusy}" aria-selected="${item.id===state.project.id||!state.project.id&&item.id==='legacy'}" tabindex="${item.id===state.project.id||!state.project.id&&item.id==='legacy'?0:-1}" title="${esc(item.name||'未命名画布')} · 拖动排序，右键管理"${state.canvasBusy?' disabled':''}><span>${esc(item.name||'未命名画布')}</span></button>`).join('');
  $('#new-canvas').disabled=state.canvasBusy||!state.canvasesAvailable;
  $('#new-canvas').title=state.canvasesAvailable?'新建画布':'重新打开新版映流后可新建画布';
  const actions=$('#canvas-actions');if(actions)actions.disabled=state.canvasBusy||!state.canvasesAvailable;
  $('#canvas-switcher').setAttribute('aria-busy',String(state.canvasBusy));
  updateCanvasOverflow();if(typeof requestAnimationFrame==='function')requestAnimationFrame(updateCanvasOverflow);
}
function canvasDeleteBlocked(id) {
  if(state.canvases.length<2)return '至少保留一张画布，请先新建另一张。';
  if(state.jobs.some(job=>jobBusy(job)&&(!job.canvas_id?id===state.project.id:job.canvas_id===id)))return '这张画布有任务正在生成或排队，完成后再删除。';
  return '';
}
async function finishCanvasWrites() {
  clearTimeout(saveTimer);clearTimeout(bridgeTimer);
  if(pendingBridgeWrite)await pendingBridgeWrite;
  if(pendingSave)await pendingSave;
  if(state.dirty&&!await saveProject(false))throw new Error('当前画布尚未保存，请保存成功后再操作。');
}
function finishCanvasManagement() {
  setCanvasBusy(false);importResults(state.jobs);
  if(state.dirty){clearTimeout(saveTimer);saveTimer=setTimeout(()=>saveProject(false),700);}
}
async function renameCanvas(id,name) {
  if(state.canvasBusy||state.importing||!state.canvasesAvailable)return false;
  name=String(name||'').trim();state.canvasManagementError='';
  if(!name||name.length>80){state.canvasManagementError='画布名称请输入 1 到 80 个字符。';return false;}
  setCanvasBusy(true);
  try {
    await finishCanvasWrites();
    const project=state.project,target=id===project.id?project:await api(`/api/canvases/${encodeURIComponent(id)}`);
    const saved=await api(`/api/canvases/${encodeURIComponent(id)}/name`,{method:'PUT',body:canvasJSON.stringify({name,expectedRevision:target.revision})});
    updateCanvasItem(saved);
    if(state.project===project&&project.id===id){project.name=saved.name;project.revision=saved.revision;project.savedAt=saved.savedAt;$('#project-name').value=project.name;lastPersist=canvasJSON.stringify(project);safeStorageSave();setSaveStatus('已保存到本机');}
    renderCanvasTabs();toast('画布已重命名');return true;
  }catch(error){state.canvasManagementError=error.message;toast(`重命名未完成：${error.message}`,6000);return false;}
  finally{finishCanvasManagement();}
}
async function reorderCanvasTabs(ids) {
  if(state.canvasBusy||state.importing||!state.canvasesAvailable)return false;
  const original=state.canvases.map(item=>item.id);
  if(ids.length!==original.length||new Set(ids).size!==ids.length||ids.some(id=>!original.includes(id)))return false;
  if(ids.every((id,index)=>id===original[index]))return true;
  const focusId=document.activeElement?.dataset?.canvasId;
  setCanvasBusy(true);
  try {
    const result=await api('/api/canvases/order',{method:'PUT',body:canvasJSON.stringify({ids})});
    state.canvases=result.items;renderCanvasTabs();return true;
  }catch(error){toast(`画布顺序未保存：${error.message}`,6000);return false;}
  finally{finishCanvasManagement();if(focusId){const tab=$$('[data-canvas-id]').find(tab=>tab.dataset.canvasId===focusId);tab?.scrollIntoView?.({block:'nearest',inline:'nearest'});tab?.focus();}}
}
async function deleteCanvas(id) {
  if(state.canvasBusy||state.importing||!state.canvasesAvailable)return false;
  state.canvasManagementError=canvasDeleteBlocked(id);if(state.canvasManagementError){toast(state.canvasManagementError,6000);return false;}
  setCanvasBusy(true);
  try {
    await finishCanvasWrites();
    const target=id===state.project.id?state.project:await api(`/api/canvases/${encodeURIComponent(id)}`);
    const result=await api(`/api/canvases/${encodeURIComponent(id)}`,{method:'DELETE',body:canvasJSON.stringify({expectedRevision:target.revision})});
    state.canvases=result.items;
    if(state.project.id===id){const next=result.active_canvas||await api(`/api/canvases/${encodeURIComponent(result.active_id)}`);activateCanvas(next);}
    renderCanvasTabs();
    try{localStorage.removeItem(`${LOCAL_KEY}:${id}`);localStorage.removeItem(`${LOCAL_KEY}:${id}:state`);}catch{}
    toast('画布已删除，素材与作品继续保留');return true;
  }catch(error){state.canvasManagementError=error.message;toast(`画布未删除：${error.message}`,6000);return false;}
  finally{finishCanvasManagement();}
}
function setCanvasBusy(busy) {
  state.canvasBusy=busy;
  for(const selector of ['#viewport','#library','#inspector','#media-panel','#professional-view','.toolrail']){const element=$(selector);if(element)element.inert=busy;}
  for(const selector of ['#project-name','#import-project','#export-project','#new-workflow','#arrange-canvas','#undo-arrange']){const element=$(selector);if(element)element.disabled=busy;}
  $$('[data-mode]').forEach(button=>button.disabled=busy);
  renderCanvasTabs();
}
function activateCanvas(project,{dirty=false}={}) {
  clearTimeout(saveTimer);clearTimeout(bridgeTimer);cancelPromptOptimization();
  document.activeElement?.blur?.();state.project={version:1,...normalizeProject(project)};
  state.selected=null;state.bridgeActive=null;state.bridgeHash=null;state.bridgeTemporary=null;state.connecting=null;state.drag=null;state.space=false;state.uploadReference=null;state.uploadFieldId=null;state.uploadPosition=null;
  promptSelectionSerial++;layoutUndo=null;$('#undo-arrange').hidden=true;$('#node-menu')?.setAttribute('hidden','');$('#viewport').classList.remove('panning','space-pan');clearMediaDrop();
  state.dirty=dirty;changeSerial++;lastPersist=dirty?'':canvasJSON.stringify(state.project);updateCanvasItem();
  $('#project-name').value=state.project.name||'未命名画布';setSaveStatus(dirty?'已恢复未保存的草稿，请保存':'已读取本机画布');
  setMode('simple');renderCanvasTabs();renderCards();renderInspector();applyViewport();renderJobs();
  safeStorageSave();try{if(state.project.id)localStorage.setItem(ACTIVE_CANVAS_KEY,state.project.id);}catch{}
}
async function changeCanvas(id,{create=false}={}) {
  if(state.canvasBusy||state.importing||!state.canvasesAvailable||!create&&id===state.project.id)return false;
  const focusTabs=!!document.activeElement?.closest?.('#canvas-tabs');
  setCanvasBusy(true);clearTimeout(saveTimer);clearTimeout(bridgeTimer);
  try {
    if(pendingBridgeWrite)await pendingBridgeWrite;
    if(pendingSave)await pendingSave;
    if(state.dirty&&!await saveProject(false))throw new Error('当前画布尚未保存到本机，请点击保存重试后再切换。');
    const project=create?await api('/api/canvases',{method:'POST',body:'{}'}):await api(`/api/canvases/${encodeURIComponent(id)}`);
    const document=project.project||project;
    normalizeProject(document);
    if(create){updateCanvasItem(document);renderCanvasTabs();}
    // Selecting a canvas never sends its professional graph to the engine.
    await api('/api/canvases/active',{method:'PUT',body:canvasJSON.stringify({id:document.id})});
    const resolved=resolveProjectDraft(document);activateCanvas(resolved.project,{dirty:resolved.dirty});
    if(create)toast('已新建画布');
    return true;
  } catch(error){toast(`画布未切换：${error.message}`,6500);return false;}
  finally{setCanvasBusy(false);importResults(state.jobs);const selected=$('#canvas-tabs [aria-selected="true"]');selected?.scrollIntoView?.({block:'nearest',inline:'nearest'});if(focusTabs)selected?.focus?.();if(state.dirty){clearTimeout(saveTimer);saveTimer=setTimeout(()=>saveProject(false),700);}}
}
function bindCanvasTabs() {
  const tabs=$('#canvas-tabs');let draggedId=null,dropAfter=false,menuId=null;
  if(!$('#canvas-tab-menu'))document.body.insertAdjacentHTML('beforeend',`<div id="canvas-tab-menu" class="canvas-tab-menu" role="menu" aria-label="画布操作" hidden><button type="button" role="menuitem" data-canvas-rename>重命名</button><button type="button" role="menuitem" data-canvas-delete>删除画布</button></div><dialog id="canvas-rename-dialog" class="canvas-manage-dialog" aria-labelledby="canvas-rename-title"><form id="canvas-rename-form"><h2 id="canvas-rename-title">重命名画布</h2><label class="field"><span>画布名称</span><input id="canvas-rename-input" maxlength="80" required autocomplete="off"></label><p id="canvas-rename-error" class="field-help error" role="status"></p><div class="dialog-actions"><button type="button" data-canvas-dialog-close class="secondary-button">取消</button><button id="canvas-rename-save" class="primary-button">保存名称</button></div></form></dialog><dialog id="canvas-delete-dialog" class="canvas-manage-dialog" aria-labelledby="canvas-delete-title"><form id="canvas-delete-form"><h2 id="canvas-delete-title">删除画布？</h2><p id="canvas-delete-name"></p><p>素材、作品和其他画布不会删除。本机保留这张画布的备份。</p><p id="canvas-delete-error" class="field-help error" role="status"></p><div class="dialog-actions"><button type="button" data-canvas-dialog-close class="secondary-button">取消</button><button id="canvas-delete-confirm" class="danger-button">删除画布</button></div></form></dialog>`);
  const menu=$('#canvas-tab-menu'),renameDialog=$('#canvas-rename-dialog'),deleteDialog=$('#canvas-delete-dialog');
  const closeMenu=()=>{menu.hidden=true;$('#canvas-actions')?.setAttribute('aria-expanded','false');};
  const openMenu=(id,x,y)=>{
    if(state.canvasBusy||!state.canvasesAvailable)return;
    menuId=id;const reason=canvasDeleteBlocked(id),button=$('[data-canvas-delete]',menu);button.disabled=!!reason;button.title=reason;
    menu.hidden=false;
    menu.style.left=`${Math.max(8,Math.min(x,window.innerWidth-menu.offsetWidth-8))}px`;menu.style.top=`${Math.max(8,Math.min(y,window.innerHeight-menu.offsetHeight-8))}px`;
    $('#canvas-actions')?.setAttribute('aria-expanded','true');$('[data-canvas-rename]',menu)?.focus();
  };
  $('#new-canvas').onclick=()=>changeCanvas(null,{create:true});
  $('#canvas-actions').onclick=()=>{const box=$('#canvas-actions').getBoundingClientRect();if(!menu.hidden)closeMenu();else openMenu(state.project.id,box.left,box.bottom+7);};
  for(const direction of [-1,1])$(direction<0?'#canvas-scroll-left':'#canvas-scroll-right').onclick=()=>{
    const reduced=window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    tabs.scrollBy({left:direction*Math.max(120,tabs.clientWidth*.7),behavior:reduced?'auto':'smooth'});
  };
  for(const direction of [-1,1])$(direction<0?'#canvas-scroll-left':'#canvas-scroll-right').addEventListener('dragover',event=>{if(!draggedId)return;event.preventDefault();tabs.scrollBy({left:direction*70,behavior:'auto'});});
  tabs.addEventListener('scroll',updateCanvasOverflow,{passive:true});
  if(typeof ResizeObserver==='function')new ResizeObserver(updateCanvasOverflow).observe(tabs);
  window.addEventListener('resize',updateCanvasOverflow);
  tabs.addEventListener('click',event=>{const tab=event.target.closest('[data-canvas-id]');if(tab)changeCanvas(tab.dataset.canvasId);});
  tabs.addEventListener('contextmenu',event=>{const tab=event.target.closest('[data-canvas-id]');if(tab){event.preventDefault();openMenu(tab.dataset.canvasId,event.clientX,event.clientY);}});
  tabs.addEventListener('keydown',event=>{
    const tab=event.target.closest('[data-canvas-id]'),all=$$('[data-canvas-id]');if(!tab||state.canvasBusy)return;
    if(event.key==='F2'){event.preventDefault();menuId=tab.dataset.canvasId;$('[data-canvas-rename]',menu).click();return;}
    if(event.key==='ContextMenu'||event.shiftKey&&event.key==='F10'){event.preventDefault();const box=tab.getBoundingClientRect();openMenu(tab.dataset.canvasId,box.left,box.bottom+7);return;}
    const index=all.indexOf(tab);let next;
    if(event.key==='ArrowRight')next=(index+1)%all.length;else if(event.key==='ArrowLeft')next=(index+all.length-1)%all.length;else if(event.key==='Home')next=0;else if(event.key==='End')next=all.length-1;else return;
    event.preventDefault();
    if(event.ctrlKey&&event.shiftKey&&['ArrowLeft','ArrowRight'].includes(event.key)){
      const target=index+(event.key==='ArrowLeft'?-1:1);if(target<0||target>=all.length)return;
      const ids=state.canvases.map(item=>item.id),[id]=ids.splice(index,1);ids.splice(target,0,id);reorderCanvasTabs(ids);return;
    }
    all[next].scrollIntoView?.({block:'nearest',inline:'nearest'});all[next].focus();changeCanvas(all[next].dataset.canvasId);
  });
  const clearDrop=()=>{$$('.canvas-tab').forEach(tab=>tab.classList.remove('canvas-drop-before','canvas-drop-after','canvas-dragging'));};
  tabs.addEventListener('dragstart',event=>{const tab=event.target.closest('[data-canvas-id]');if(!tab||state.canvasBusy||!state.canvasesAvailable){event.preventDefault();return;}draggedId=tab.dataset.canvasId;event.dataTransfer.effectAllowed='move';event.dataTransfer.setData('application/x-yingliu-canvas',draggedId);tab.classList.add('canvas-dragging');});
  tabs.addEventListener('dragover',event=>{const tab=event.target.closest('[data-canvas-id]');if(!draggedId||!tab)return;event.preventDefault();event.dataTransfer.dropEffect='move';clearDrop();const box=tab.getBoundingClientRect();dropAfter=event.clientX>box.left+box.width/2;tab.classList.add(dropAfter?'canvas-drop-after':'canvas-drop-before');});
  tabs.addEventListener('drop',event=>{const tab=event.target.closest('[data-canvas-id]');event.preventDefault();if(draggedId&&tab&&tab.dataset.canvasId!==draggedId){const ids=state.canvases.map(item=>item.id).filter(id=>id!==draggedId);const at=ids.indexOf(tab.dataset.canvasId);ids.splice(at+(dropAfter?1:0),0,draggedId);reorderCanvasTabs(ids);}draggedId=null;clearDrop();});
  tabs.addEventListener('dragend',()=>{draggedId=null;clearDrop();});
  document.addEventListener('pointerdown',event=>{if(!event.target.closest('#canvas-tab-menu,#canvas-actions'))closeMenu();});
  menu.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();closeMenu();$('#canvas-actions').focus();}if(['ArrowUp','ArrowDown'].includes(event.key)){event.preventDefault();const choices=$$('button:not(:disabled)',menu),index=choices.indexOf(document.activeElement);choices[(index+(event.key==='ArrowDown'?1:-1)+choices.length)%choices.length]?.focus();}});
  $('[data-canvas-rename]',menu).onclick=()=>{closeMenu();const item=state.canvases.find(item=>item.id===menuId);if(!item)return;renameDialog.dataset.canvasId=menuId;$('#canvas-rename-input').value=item.name;$('#canvas-rename-error').textContent='';renameDialog.showModal();$('#canvas-rename-input').select();};
  $('[data-canvas-delete]',menu).onclick=()=>{closeMenu();const item=state.canvases.find(item=>item.id===menuId),reason=canvasDeleteBlocked(menuId);if(!item||reason){if(reason)toast(reason,6000);return;}deleteDialog.dataset.canvasId=menuId;$('#canvas-delete-name').textContent=`“${item.name||'未命名画布'}”将从画布列表移除。`;$('#canvas-delete-error').textContent='';deleteDialog.showModal();};
  $$('[data-canvas-dialog-close]').forEach(button=>button.onclick=()=>button.closest('dialog').close());
  $('#canvas-rename-form').onsubmit=async event=>{event.preventDefault();const button=$('#canvas-rename-save');button.disabled=true;try{if(await renameCanvas(renameDialog.dataset.canvasId,$('#canvas-rename-input').value))renameDialog.close();else $('#canvas-rename-error').textContent=state.canvasManagementError||'暂时无法重命名，请稍后重试。';}finally{button.disabled=false;}};
  $('#canvas-delete-form').onsubmit=async event=>{event.preventDefault();const button=$('#canvas-delete-confirm');button.disabled=true;try{if(await deleteCanvas(deleteDialog.dataset.canvasId))deleteDialog.close();else $('#canvas-delete-error').textContent=state.canvasManagementError||'暂时无法删除，请稍后重试。';}finally{button.disabled=false;}};
  updateCanvasOverflow();
}
const UI_KEY = 'creation-canvas-ui-v2';
function editingField() {return !!(document.activeElement?.dataset?.field||document.activeElement?.dataset?.nodeRange);}
function storeUi() {try{localStorage.setItem(UI_KEY,canvasJSON.stringify({library:!$('#library').classList.contains('hidden'),right:!$('#right-panel').classList.contains('is-collapsed')}));}catch{}}
function setRightOpen(open) {
  const panel=$('#right-panel');panel.classList.toggle('is-collapsed',!open);
  if(!open){state.rightTab='media';updateRightPanel();}
  for(const selector of ['#right-toggle','#right-compact-toggle']){const button=$(selector);if(!button)continue;button.setAttribute('aria-expanded',String(open));button.setAttribute('aria-label',open?'收起右侧面板':'展开素材与作品');button.title=open?'收起右侧面板':'展开素材与作品';}
  storeUi();requestAnimationFrame(drawConnections);
}
function restoreUi() {let saved={};try{saved=canvasJSON.parse(localStorage.getItem(UI_KEY))||{};}catch{}setLibrary(saved.library===true);setRightOpen(saved.right===true);}
function arrangeCanvas() {
  if(!state.project.cards.length||!window.CanvasLayout)return;
  layoutUndo={project:state.project,positions:Object.fromEntries(state.project.cards.map(card=>[card.id,{x:card.x,y:card.y}])),viewport:clone(state.project.viewport)};
  const sizes=Object.fromEntries($$('.card').map(el=>[el.dataset.id,{width:el.offsetWidth,height:el.offsetHeight}]));
  const positions=window.CanvasLayout.arrange(state.project.cards,state.project.edges,sizes);
  state.project.cards.forEach(card=>Object.assign(card,positions[card.id]||{}));
  $('#undo-arrange').hidden=false;renderCards();fitView();projectChanged();
}
function undoArrange() {
  if(!layoutUndo||layoutUndo.project!==state.project)return;
  state.project.cards.forEach(card=>{if(layoutUndo.positions[card.id])Object.assign(card,layoutUndo.positions[card.id]);});
  state.project.viewport=clone(layoutUndo.viewport);layoutUndo=null;$('#undo-arrange').hidden=true;renderCards();applyViewport();projectChanged();
}
function focusNode(id) {
  const card=cardById(id);if(!isWorkflow(card))return;
  requestAnimationFrame(()=>{const el=$(`.card[data-id="${id}"]`);if(!el||state.selected!==id)return;
    const view=$('#viewport').getBoundingClientRect(),zoom=Math.max(.85,state.project.viewport.zoom);
    state.project.viewport.zoom=zoom;state.project.viewport.x=view.width/2-(card.x+el.offsetWidth/2)*zoom;
    state.project.viewport.y=Math.max(64,(view.height-el.offsetHeight*zoom)/2)-card.y*zoom;applyViewport();projectChanged();
  });
}
function readableJobError(value) {
  const text=String(value||'');
  if(/execution_interrupted|interrupted|cancelled|canceled/i.test(text))return '生成已停止，可以调整后重新生成';
  if(/out of memory|cuda.*memory|显存不足/i.test(text))return '显存不足，请在详细设置中调整画面尺寸';
  if(/^\s*[\[{]/.test(text))return '本次生成未完成，请查看引擎中的详细原因';
  return text.length>150?text.slice(0,147)+'…':text||'本次生成未完成';
}

function optimizerContextValid(session) {
  return !!session&&state.project===session.project&&cardById(session.card.id)===session.card&&state.selected===session.card.id&&promptSelectionSerial===session.selection&&fieldsOf(session.card).some(field=>field.id===session.field.id&&field.editable!==false)&&String(fieldValue(session.card,session.field)??'')===session.original;
}
function promptSourcesHtml(sources) {
  return (Array.isArray(sources)?sources:[]).flatMap(source=>{try{const url=new URL(source.url);return ['https:','http:'].includes(url.protocol)?[`<a href="${esc(url.href)}" target="_blank" rel="noopener">${esc(source.title||'官方规范')}</a>`]:[];}catch{return[];}}).join(' · ');
}
function syncOptimizerButtons() {$$('[data-optimize-prompt]').forEach(button=>{button.disabled=promptOptimizer.busy;});}
function renderPromptPreview(session) {
  if(promptOptimizer.session!==session)return;
  $('#prompt-original').value=session.original;
  $('#prompt-result').value=session.result||'';
  $('#prompt-translation').value=session.translation||'';
  $('#prompt-translation').placeholder=promptOptimizer.busy?'正在结合参考素材整理中文对照…':session.error?'优化未完成，原文已保留。':'此结果未包含中文对照，请重新优化。';
  $('#prompt-translation-label').textContent='中文对照';
  $('#prompt-result').disabled=promptOptimizer.busy||!!session.error;
  $('#prompt-apply').disabled=promptOptimizer.busy||!!session.error||!session.result||!optimizerContextValid(session);
  $('#prompt-retry').disabled=promptOptimizer.busy||!optimizerContextValid(session);
  $('#prompt-status').classList.toggle('error',!!session.error);
  $('#prompt-status').textContent=session.error||(promptOptimizer.busy?'正在优化提示词并生成中文对照…':(session.warnings||[]).map(item=>String(item?.message||item)).join('；'));
  const imageCount=(session.media||[]).filter(item=>item.type==='image').length;
  $('#prompt-media').textContent=[imageCount?`已参考 ${imageCount} 张图片`:'',...(session.media||[]).filter(item=>item.type==='video').map(item=>`视频 ${item.start}–${item.end} 秒 · ${item.frames} 帧 · 不含音频`)].filter(Boolean).join('；');
  const profileLabels={h3_ref:'MiniMax H3 · 参考生成',h3_base:'MiniMax H3 · 视频生成',qwen_image:'千问 · 图片生成',qwen_edit:'千问 · 图片编辑',generic:'通用提示词'};
  $('#prompt-provider').textContent=[session.provider||'所选 AI 接口',session.model,profileLabels[session.profile],session.referencePolicy==='text_only'?'仅发送文字':'发送描述、参考图片与视频抽帧'].filter(Boolean).join(' · ');
  $('#prompt-result-label').textContent=/^h3_/.test(session.profile||'')?'用于引擎的提示词':'优化结果';
  $('#prompt-sources').innerHTML=promptSourcesHtml(session.sources);
}
async function optimizePrompt(cardId) {
  if(promptOptimizer.busy)return;
  const card=cardById(cardId),field=isWorkflow(card)&&descField(card);
  if(!field||field.editable===false||state.selected!==cardId)return;
  const original=String(fieldValue(card,field)??'');
  if(!original.trim()){toast('先写下你想生成或修改的内容');return;}
  const session={project:state.project,card,field,original,workflow:clone(card.workflow),values:clone(card.values||{}),references:referencePayload(card).map(reference=>({type:reference.type,name:reference.name,fieldId:reference.fieldId,assetId:reference.assetId,url:reference.url})),selection:promptSelectionSerial,requestId:++promptOptimizer.requestId};
  promptOptimizer.session=session;promptOptimizer.busy=true;syncOptimizerButtons();renderPromptPreview(session);
  const dialog=$('#prompt-dialog');if(!dialog.open)dialog.showModal();
  try {
    const config=await api('/api/prompt-optimizer');
    if(promptOptimizer.session!==session||session.requestId!==promptOptimizer.requestId)return;
    session.model=config.model;session.provider=config.label||config.provider||'AI';session.referencePolicy=config.referencePolicy;
    if(!config.configured)throw new Error(config.message||'请先在设置中配置 AI 接口，原提示词已保留。');
    renderPromptPreview(session);
    if(!optimizerContextValid(session))throw new Error('所选卡片或原文已改变，请关闭后重新优化。');
    const result=await api('/api/prompt-optimizer',{method:'POST',timeoutMs:240000,body:canvasJSON.stringify({workflow:session.workflow,values:session.values,prompt:original,references:session.references})});
    if(promptOptimizer.session!==session||session.requestId!==promptOptimizer.requestId)return;
    if(!optimizerContextValid(session))throw new Error('所选卡片或原文已改变，请关闭后重新优化。');
    if(typeof result.prompt!=='string'||!result.prompt.trim())throw new Error('AI 未返回可用提示词，请重试。原文已保留。');
    session.result=result.prompt;session.translation=typeof result.translation==='string'?result.translation:'';session.media=result.media||[];session.model=result.model||session.model;session.sources=result.sources;session.warnings=result.warnings||[];session.profile=result.profile;
  }catch(error){if(promptOptimizer.session===session&&session.requestId===promptOptimizer.requestId)session.error=error.timedOut?'提示词优化仍未确认完成，原文已保留；请稍后重试，避免连续点击重复请求。':error.message||'优化失败，原提示词已保留。';}
  finally{promptOptimizer.busy=false;syncOptimizerButtons();renderPromptPreview(session);}
}
function cancelPromptOptimization() {promptOptimizer.requestId++;promptOptimizer.session=null;const dialog=$('#prompt-dialog');if(dialog.open)dialog.close();}
function applyPromptOptimization() {
  const session=promptOptimizer.session,draft=$('#prompt-result').value;
  if(promptOptimizer.busy||session?.error||!draft.trim()||!optimizerContextValid(session)){if(session&&!optimizerContextValid(session)){session.error='所选卡片或原文已改变，请关闭后重新优化。';renderPromptPreview(session);}return false;}
  session.card.promptOptimization={fieldId:session.field.id,before:session.original,after:draft,translation:session.translation,translationFor:session.result,provider:session.provider||'AI',model:session.model,profile:session.profile,at:new Date().toISOString()};
  updateField({dataset:{field:session.field.id},type:'textarea',value:draft});renderInspector();cancelPromptOptimization();return true;
}
function undoPromptOptimization(cardId) {
  const card=cardById(cardId),previous=card?.promptOptimization,field=fieldsOf(card).find(item=>item.id===previous?.fieldId);
  if(state.selected!==cardId||!field||field.editable===false||String(fieldValue(card,field)??'')!==previous.after)return false;
  delete card.promptOptimization;updateField({dataset:{field:field.id},type:'textarea',value:previous.before});renderInspector();return true;
}
function bindPromptOptimizer() {
  $('#prompt-cancel').onclick=cancelPromptOptimization;$('#prompt-close').onclick=cancelPromptOptimization;$('#prompt-apply').onclick=applyPromptOptimization;
  $('#prompt-retry').onclick=()=>{const session=promptOptimizer.session;if(session&&optimizerContextValid(session))optimizePrompt(session.card.id);};
  $('#prompt-result').oninput=()=>{const session=promptOptimizer.session;$('#prompt-translation-label').textContent=$('#prompt-result').value===session?.result?'中文对照':'中文对照（对应优化初稿）';$('#prompt-apply').disabled=promptOptimizer.busy||!!session?.error||!$('#prompt-result').value.trim()||!optimizerContextValid(session);};
  $('#prompt-dialog').addEventListener('cancel',event=>{event.preventDefault();cancelPromptOptimization();});
}

function mediaKey(item) {return String(item.key||item.assetId||item.id||item.outputKey||item.url||'');}
function mediaIdentity(item) {try{return item.assetId||new URL(item.url,location.origin).pathname+new URL(item.url,location.origin).search;}catch{return '';}}
function mediaItems() {
  const items=[...state.media,...(state.project.mediaArchive||[])];
  state.jobs.forEach(job=>(job.outputs||[]).forEach((out,index)=>items.push({...out,key:`${job.id}:${index}`,outputKey:`${job.id}:${index}`,fromJob:job.id,jobId:job.id,source:job.kind==='video-join'?'joined':'generated',type:out.type==='video'?'video':'image',name:out.name||job.name||'生成结果'})));
  state.project.cards.filter(isMedia).forEach(c=>items.push({...c,key:c.mediaKey||c.outputKey||c.assetId||c.url,source:c.fromJob?'generated':'upload'}));
  const unique=new Map(),keys=new Set();
  items.forEach(item=>{if(!item||!['image','video'].includes(item.type)||!safeMediaUrl(item.url))return;const identity=mediaIdentity(item),key=mediaKey(item);if(identity&&!unique.has(identity)&&!keys.has(key)){unique.set(identity,{...item,key});keys.add(key);}});
  return [...unique.values()];
}
function mediaByKey(key) {return mediaItems().find(item=>mediaKey(item)===key);}
function supportsContinuation(card) {
  const graph=card?.workflow;
  if(!graph||typeof graph!=='object')return false;
  return Array.isArray(graph.nodes)&&graph.nodes.some(node=>node?.type==='MiniMaxH3ReferenceToVideo'&&![2,4].includes(node.mode));
}
function continuationReference(item) {
  if(!item||item.type!=='video')return null;
  const id=item.assetId||item.id;
  const assetId=typeof id==='string'&&/^[a-f0-9]{32}\.[a-z0-9]+$/.test(id)?id:undefined;
  let url;
  try{
    const parsed=new URL(String(item.url||''),location.origin);
    if(parsed.origin===location.origin&&((parsed.pathname==='/api/comfy/view'&&parsed.searchParams.has('filename'))||/^\/assets\/[a-f0-9]{32}\.[a-z0-9]+$/.test(parsed.pathname)))url=parsed.pathname+parsed.search;
  }catch{}
  if(!assetId&&!url)return null;
  return {type:'video',...(assetId?{assetId}:{}),...(url?{url}:{}),name:String(item.name||'视频').slice(0,180)};
}
function continuityOptions() {
  const seen=new Set(),items=[],project=state.project;
  const candidates=[...project.cards.filter(isMedia),...(project.mediaArchive||[])];
  project.cards.filter(isWorkflow).forEach(card=>(card.outputs||[]).forEach(out=>candidates.push({...out,fromJob:true})));
  state.jobs.filter(job=>job.canvas_id===project.id||(!job.canvas_id&&project.cards.some(card=>card.id===job.instance_id)))
    .forEach(job=>(job.outputs||[]).forEach(out=>candidates.push({...out,fromJob:job.id})));
  for(const item of candidates){
    const reference=continuationReference(item);if(!reference)continue;
    const source=continuationReference({...item,url:item.sourceUrl,assetId:undefined,id:undefined});
    const identity=source?.url||reference.url||reference.assetId;
    if(seen.has(identity))continue;seen.add(identity);
    items.push({identity,reference,label:`${item.fromJob||item.jobId||['generated','joined'].includes(item.source)?'作品':'素材'} · ${reference.name}`});
  }
  return items;
}
function continuationMatches(selected,item) {
  return !!selected&&!!item&&((selected.assetId&&selected.assetId===item.reference.assetId)||
    (selected.url&&(selected.url===item.reference.url||selected.url===item.identity)));
}
function continuationHtml(card) {
  if(!supportsContinuation(card)&&!card.continuation)return '';
  const supported=supportsContinuation(card),options=supported?continuityOptions():[],selected=continuationReference(card.continuation);
  const match=selected&&options.find(item=>continuationMatches(selected,item));
  const unavailable=card.continuation&&!match;
  return `<label class="field"><span>接续上一段</span><select data-continuation-select="${esc(card.id)}" aria-label="接续上一段视频"><option value="">不接续，独立生成</option>${unavailable?`<option value="__saved__" selected>${esc(!supported?'该工作流不支持接续，请清除选择':selected?`已选：${selected.name}（当前画布列表暂未显示）`:'已选视频无效，请重新选择')}</option>`:''}${options.map(item=>`<option value="${esc(item.identity)}"${match===item?' selected':''}>${esc(item.label)}</option>`).join('')}</select><small>选择当前画布的上一段成片，用最后一帧引导下一段开头。</small></label>`;
}
function setContinuation(cardId,identity) {
  const card=cardById(cardId);if(!isWorkflow(card)||state.selected!==cardId||state.canvasBusy)return false;
  if(!identity){delete card.continuation;projectChanged();return true;}
  if(identity==='__saved__'||!supportsContinuation(card))return false;
  const item=continuityOptions().find(option=>option.identity===identity);if(!item)return false;
  card.continuation={...item.reference};projectChanged();return true;
}
function continuationPayload(card) {
  if(!card.continuation)return null;
  if(!supportsContinuation(card))throw new Error('该工作流不支持接续，请先清除上一段选择。');
  const reference=continuationReference(card.continuation);
  if(!reference)throw new Error('上一段视频地址无效，请重新选择或清除。');
  return reference;
}
function setRightTab(tab) {
  state.rightTab=tab==='settings'&&!selectedCard()?'media':tab;
  setRightOpen(true);
  updateRightPanel();
}
function updateRightPanel() {
  $('#right-panel')?.classList.toggle('show-settings',state.rightTab==='settings');
  $$('[data-right-tab]').forEach(button=>{const active=button.dataset.rightTab===state.rightTab;button.classList.toggle('active',active);button.setAttribute('aria-selected',String(active));if(button.dataset.rightTab==='settings')button.hidden=!selectedCard();});
  if($('#media-panel'))$('#media-panel').hidden=state.rightTab!=='media';
  if($('#inspector'))$('#inspector').hidden=state.rightTab!=='settings';
  renderMedia();
}
function renderMedia() {
  const panel=$('#media-list');if(!panel)return;
  const items=mediaItems(),search=state.mediaSearch.toLowerCase(),shown=items.filter(item=>(state.mediaFilter==='all'||state.mediaFilter==='generated'&&['generated','joined'].includes(item.source)||item.type===state.mediaFilter)&&String(item.name||'').toLowerCase().includes(search));
  const busy=state.jobs.filter(jobBusy);if($('#media-summary'))$('#media-summary').textContent=busy.length?`${busy.length} 个任务处理中 · ${items.length} 份素材`:`${items.length} 份素材 · 拖到画布即可使用`;
  $$('[data-media-filter]').forEach(button=>{const active=button.dataset.mediaFilter===state.mediaFilter;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));});
  const fingerprint=canvasJSON.stringify([shown,state.mediaError]);if(panel.dataset.fingerprint===fingerprint)return;panel.dataset.fingerprint=fingerprint;
  panel.innerHTML=shown.length?shown.map(item=>`<article class="media-item" draggable="true" data-media-key="${esc(mediaKey(item))}" tabindex="0" aria-label="${esc(item.name||'素材')}，可拖到画布"><div class="media-thumb">${item.type==='video'?`<video src="${esc(item.url)}" preload="metadata" muted playsinline></video>${icon('video')}`:`<img src="${esc(item.url)}" alt="${esc(item.name||'图片')}" loading="lazy" draggable="false">`}</div><div class="media-info"><strong title="${esc(item.name||'未命名素材')}">${esc(item.name||'未命名素材')}</strong></div></article>`).join(''):`<div class="media-empty">${state.mediaError?esc(state.mediaError):items.length?'没有匹配的素材':'上传图片或视频，生成的作品也会保存在这里。'}</div>`;
}
async function refreshMedia() {try{const data=await api('/api/media');state.media=Array.isArray(data.items)?data.items:[];state.mediaError='';renderMedia();}catch(error){state.mediaError='素材库暂时无法读取，已保留画布中的素材。';renderMedia();}}
function vacantPosition(position,width=308,height=300) {
  const start={x:Math.round(position.x),y:Math.round(position.y)};
  for(let index=0;index<200;index++){
    const point={x:start.x+Math.floor(index/8)*(width+44),y:start.y+(index%8)*(height+40)};
    if(!state.project.cards.some(card=>point.x<card.x+308+24&&point.x+width+24>card.x&&point.y<card.y+height+24&&point.y+height+24>card.y))return point;
  }
  return{x:start.x+Math.ceil(state.project.cards.length/8)*(width+44),y:start.y};
}
function addMediaToCanvas(key,position) {
  const item=mediaByKey(key);if(!item){toast('这份素材尚未就绪，请刷新素材库后重试');return;}
  const p=position||centerPoint(),point=vacantPosition({x:p.x-154,y:p.y-120});
  const card={id:uid(),type:item.type,name:item.name||'素材',url:item.url,assetId:item.assetId||(/^[a-f0-9]{32}\.[a-z0-9]+$/.test(item.id||'')?item.id:undefined),mediaKey:mediaKey(item),fromJob:item.fromJob||item.jobId,outputKey:item.outputKey,...point};
  state.project.cards.push(card);renderCards();selectCard(card.id);projectChanged();return card;
}
function disconnectEdge(id) {const edge=state.project.edges.find(e=>e.id===id);if(!edge)return;state.project.edges=state.project.edges.filter(e=>e.id!==id);renderCards();renderInspector();projectChanged();toast('连线已断开，工作流和素材都已保留');}
function disconnectSource(id) {state.project.edges=state.project.edges.filter(e=>!(e.to===id&&e.result));renderCards();renderInspector();projectChanged();toast('来源连线已断开，可以继续使用原工作流生成');}
function archiveCard(id) {
  const card=cardById(id);if(!isMedia(card))return;
  state.project.mediaArchive||=[];if(!state.project.mediaArchive.some(item=>mediaIdentity(item)===mediaIdentity(card)))state.project.mediaArchive.push({...card,key:card.mediaKey||card.outputKey||card.assetId||card.url,source:card.fromJob?'generated':'upload'});
  removeCard(id);setRightTab('media');toast('已收纳到素材栏，原文件保留');
}
function rightDropTarget(target) {return target?.closest?.('#right-panel')?'media':null;}
function dropCanvasCardOnRight(drag,target) {const card=cardById(drag.id);if(!isMedia(card)||!rightDropTarget(target))return false;archiveCard(card.id);return true;}
function highlightMediaDrop(target,item) {$('#right-panel').classList.toggle('media-drop-active',isMedia(item)&&!!rightDropTarget(target));}
function clearMediaDrop() {$('#right-panel').classList.remove('media-drop-active');}
function videoReference(card) {return card?.type==='video'?card:(card?.outputs||[]).find(output=>output.type==='video'&&safeMediaUrl(output.url));}
function joinInputs(card) {return state.project.edges.filter(edge=>edge.to===card.id&&!edge.result).map(edge=>({edge,source:cardById(edge.from)})).map(input=>({...input,media:videoReference(input.source)}));}
function addJoinNode(position) {
  const point=position||centerPoint(),card={id:uid(),type:'join',name:'视频拼接',x:Math.round(point.x),y:Math.round(point.y),ranges:{},outputs:[]};
  state.project.cards.push(card);renderCards();selectCard(card.id);projectChanged();return card;
}
function moveJoinInput(cardId,edgeId,offset) {
  const ordered=state.project.edges.filter(edge=>edge.to===cardId&&!edge.result),index=ordered.findIndex(edge=>edge.id===edgeId),other=ordered[index+offset];
  if(index<0||!other)return;const a=state.project.edges.indexOf(ordered[index]),b=state.project.edges.indexOf(other);
  [state.project.edges[a],state.project.edges[b]]=[state.project.edges[b],state.project.edges[a]];renderSingleCard(cardId);projectChanged();
}
function joinPayload(card) {
  const inputs=joinInputs(card);if(inputs.length<2)throw new Error('请连接至少两段视频');if(inputs.length>20)throw new Error('一次最多拼接20段视频');
  return{canvas_id:state.project.id,instance_id:card.id,name:card.name||'视频拼接',clips:inputs.map(({edge,media},index)=>{if(!media)throw new Error(`第${index+1}段还没有视频结果`);const range=card.ranges?.[edge.id]||{},start=Number(range.start||0),end=range.end===''||range.end==null?null:Number(range.end);if(!Number.isFinite(start)||start<0||end!=null&&(!Number.isFinite(end)||end<=start))throw new Error(`第${index+1}段的结束时间需要大于开始时间`);return{assetId:media.assetId||(/^[a-f0-9]{32}\.[a-z0-9]+$/.test(media.id||'')?media.id:undefined),url:media.url,name:media.name,start,...(end==null?{}:{end})};})};
}
function renderJoinCard(card) {
  const inputs=joinInputs(card),job=state.jobs.filter(item=>jobForProject(item)&&(item.instance_id===card.id||item.id===card.legacyJobId)).at(-1),busy=card.submitting||jobBusy(job),error=card.error||(['failed','error'].includes(job?.status)?job.error:'');
  return `<article class="card join-card${state.selected===card.id?' selected':''}" data-id="${card.id}" style="left:${card.x}px;top:${card.y}px" tabindex="0" aria-label="${esc(card.name)}"><div class="card-header"><span class="card-type-icon">${icon('video')}</span><span class="card-title">${esc(card.name)}</span><button class="icon-button card-more" title="卡片设置" aria-label="卡片设置">${icon('more')}</button></div><div class="join-inputs">${inputs.map(({edge,source,media},index)=>{const range=card.ranges?.[edge.id]||{};return `<div class="join-input-row"><div class="join-input-head"><span class="join-number">${index+1}</span><strong title="${esc(media?.name||source?.name||'视频')}">${esc(media?.name||source?.name||'视频')}</strong><button class="icon-button small" data-node-sort="${card.id}" data-edge-id="${edge.id}" data-offset="-1" title="上移" aria-label="上移"${index===0?' disabled':''}>${icon('arrow')}</button><button class="icon-button small" data-node-sort="${card.id}" data-edge-id="${edge.id}" data-offset="1" title="下移" aria-label="下移"${index===inputs.length-1?' disabled':''}>${icon('arrow')}</button><button class="icon-button small" data-edge-unlink="${edge.id}" title="断开连线" aria-label="断开连线">${icon('close')}</button></div><details class="join-node-range"><summary>裁切</summary><label>开始 / 秒<input type="number" min="0" step="0.1" data-node-range="${card.id}" data-input-edge="${edge.id}" data-range-key="start" value="${esc(range.start||0)}"></label><label>结束 / 秒<input type="number" min="0" step="0.1" data-node-range="${card.id}" data-input-edge="${edge.id}" data-range-key="end" value="${esc(range.end??'')}" placeholder="到片尾"></label></details></div>`;}).join('')||`<div class="join-node-empty" aria-label="连接视频输入">${icon('link')}</div>`}</div>${error?`<p class="join-node-status error">${esc(error)}</p>`:busy?`<p class="join-node-status">正在拼接${job?.progress?` · ${jobPercent(job)}%`:''}</p>`:''}<div class="join-node-actions"><button class="generate-button" data-node-join="${card.id}"${inputs.length<2||busy?' disabled':''}>${icon('play')}${busy?'正在拼接':'拼接'}</button></div><button class="port port-input" title="连接视频" aria-label="拼接视频输入" data-port="input"></button><button class="port port-output" title="最新拼接结果" aria-label="拼接结果输出" data-port="output"></button></article>`;
}
async function submitJoin(id) {
  const project=state.project,card=cardById(id);if(state.canvasBusy||!isJoin(card)||card.submitting||state.jobs.some(job=>jobForProject(job)&&job.instance_id===id&&jobBusy(job)))return;
  let body;try{body=joinPayload(card);}catch(error){card.error=error.message;renderSingleCard(id);return;}
  card.submitting=true;card.error='';renderSingleCard(id);
  try{const result=await api('/api/videos/join',{method:'POST',body:canvasJSON.stringify(body)});card.joinJobId=result.job_id||result.id;state.jobs.push({id:card.joinJobId,canvas_id:project.id,instance_id:id,name:body.name,kind:'video-join',status:result.status||'queued',outputs:[]});if(state.project===project&&!state.canvasBusy)projectChanged();await refreshJobs();}
  catch(error){card.error=`拼接未能开始：${error.message}`;}
  finally{card.submitting=false;if(state.project===project)renderSingleCard(id);}
}
function dropMediaOnJoin(key,target,position) {
  const join=cardById(target?.closest?.('.join-card')?.dataset.id),item=mediaByKey(key);if(!isJoin(join)||item?.type!=='video')return false;
  const media=addMediaToCanvas(key,position||{x:join.x-200,y:join.y});if(media)connectCards(media.id,join.id);return !!media;
}
function joinNodeAction(event) {
  const run=event.target.closest('[data-node-join]');if(run){submitJoin(run.dataset.nodeJoin);return true;}
  const unlink=event.target.closest('[data-edge-unlink]');if(unlink){disconnectEdge(unlink.dataset.edgeUnlink);return true;}
  const sort=event.target.closest('[data-node-sort]');if(sort){moveJoinInput(sort.dataset.nodeSort,sort.dataset.edgeId,Number(sort.dataset.offset));return true;}
  return false;
}
function updateJoinRange(event) {const {nodeRange,inputEdge,rangeKey}=event.target.dataset;if(!nodeRange||!['start','end'].includes(rangeKey))return;const card=cardById(nodeRange);if(!isJoin(card))return;card.ranges||={};card.ranges[inputEdge]||={};card.ranges[inputEdge][rangeKey]=event.target.value;card.error='';projectChanged();}
function migrateLegacyJoin(project) {
  if(project.legacyJoinMigrated||!Array.isArray(project.joinClips)||!project.joinClips.length)return false;
  const clips=project.joinClips.filter(clip=>clip&&safeMediaUrl(clip.url));if(!clips.length)return false;
  const place=(x,y)=>{for(let step=0;step<200;step++){const point={x:x+Math.floor(step/8)*352,y:y+(step%8)*340};if(!project.cards.some(card=>Math.abs(card.x-point.x)<332&&Math.abs(card.y-point.y)<324))return point;}return{x:x+project.cards.length*352,y};};
  const anchor=project.cards.find(isWorkflow)||{x:0,y:0},node={id:uid(),type:'join',name:project.joinName||'视频拼接',ranges:{},outputs:[],legacyJobId:project.joinJobId,joinJobId:project.joinJobId,...place(anchor.x+370,anchor.y)};
  project.cards.push(node);const reused=new Set();
  clips.forEach((clip,index)=>{
    let media=project.cards.find(card=>card.type==='video'&&!reused.has(card.id)&&(clip.assetId&&card.assetId===clip.assetId||card.url===clip.url));
    if(!media){media={id:uid(),type:'video',url:clip.url,name:clip.name||`视频片段 ${index+1}`,assetId:clip.assetId,mediaKey:clip.key,...place(node.x-352,node.y+index*340)};project.cards.push(media);}
    reused.add(media.id);const edge={id:uid(),from:media.id,to:node.id};project.edges.push(edge);node.ranges[edge.id]={start:clip.start??0,end:clip.end??''};
  });
  project.legacyJoinMigrated=true;return true;
}

function normalizeProject(project) {
  if(!project||!Array.isArray(project.cards)||!Array.isArray(project.edges))throw new Error('项目缺少卡片或连线数据');
  const copied=clone(project),ids=new Map();copied.dismissedOutputKeys=Array.isArray(copied.dismissedOutputKeys)?copied.dismissedOutputKeys.map(String):[];
  copied.cards=copied.cards.filter(c=>c&&['workflow','image','video','join'].includes(c.type)).map(c=>{const original=String(c.id??'');const id=/^[a-zA-Z0-9_-]{1,100}$/.test(original)&&!ids.has(original)?original:uid();ids.set(original,id);c.id=id;c.x=Number.isFinite(Number(c.x))?Number(c.x):0;c.y=Number.isFinite(Number(c.y))?Number(c.y):0;c.name=String(c.name||'未命名卡片');c.outputs=(Array.isArray(c.outputs)?c.outputs:[]).filter(item=>item&&safeMediaUrl(item.url));if(c.type==='workflow'){c.values=c.values&&typeof c.values==='object'&&!Array.isArray(c.values)?c.values:{};c.fields=(Array.isArray(c.fields)?c.fields:[]).filter(field=>field&&typeof field==='object'&&typeof field.id==='string'&&typeof field.key==='string').map(field=>{for(const name of ['min','max','step'])if(field[name]!=null&&(typeof field[name]!=='number'||!Number.isFinite(field[name])))delete field[name];if(!Array.isArray(field.options))delete field.options;else field.options=field.options.filter(option=>option!=null&&(['string','number','boolean'].includes(typeof option)||typeof option==='object'&&['string','number','boolean'].includes(typeof option.value)));return field;});c.models=(Array.isArray(c.models)?c.models:[]).map(String);c.warnings=(Array.isArray(c.warnings)?c.warnings:[]).filter(item=>typeof item==='string');}else if(isJoin(c)){c.ranges=c.ranges&&typeof c.ranges==='object'?c.ranges:{};c.submitting=false;}else if(!safeMediaUrl(c.url))c.url='';return c;});
  const edgeIds=new Set();copied.edges=copied.edges.filter(e=>e&&ids.has(String(e.from))&&ids.has(String(e.to))).map(e=>{const original=String(e.id??''),id=/^[a-zA-Z0-9_-]{1,100}$/.test(original)&&!edgeIds.has(original)?original:uid();edgeIds.add(id);return{...e,id,from:ids.get(String(e.from)),to:ids.get(String(e.to))};});
  copied.mediaArchive=(Array.isArray(copied.mediaArchive)?copied.mediaArchive:[]).filter(item=>item&&safeMediaUrl(item.url));
  const v=copied.viewport||{};copied.viewport={x:Number.isFinite(Number(v.x))?Number(v.x):110,y:Number.isFinite(Number(v.y))?Number(v.y):95,zoom:Math.max(.25,Math.min(2,Number(v.zoom)||1))};migrateLegacyJoin(copied);return copied;
}
function safeMediaUrl(url) { try {const parsed=new URL(String(url),location.origin);return parsed.origin===location.origin&&['http:','https:'].includes(parsed.protocol);}catch{return false;} }

async function api(path, options = {}) {
  const {timeoutMs=20000,...requestOptions}=options,controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),timeoutMs);
  try {
    const response = await fetch(path, {cache:'no-store',...requestOptions,signal:controller.signal,headers:{...(options.body && typeof options.body === 'string' ? {'Content-Type':'application/json'} : {}),...options.headers}});
    const contentType = response.headers.get('content-type') || '';
    const data = contentType.includes('json') ? canvasJSON.parse(await response.text()) : {error:await response.text()};
    if (!response.ok) throw Object.assign(new Error(data.error || data.message || `请求失败 (${response.status})`),{status:response.status,data});
    return data;
  } catch(error) {
    if(controller.signal.aborted)throw Object.assign(new Error('本地服务响应超时，请重试连接'),{timedOut:true});
    throw error;
  } finally {clearTimeout(timer);}
}
function toast(message, duration = 3600) { $('#toast').textContent=message; $('#toast').classList.add('visible'); clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('#toast').classList.remove('visible'),duration); }
function selectedCard() { return state.project.cards.find(c=>c.id===state.selected); }
function cardById(id) { return state.project.cards.find(c=>c.id===id); }
function isWorkflow(card) { return card?.type === 'workflow'; }
function isJoin(card) {return card?.type==='join';}
function isMedia(card) {return card?.type==='video'||card?.type==='image';}
function fieldsOf(card) { return Array.isArray(card?.fields) ? card.fields : []; }
async function refreshLegacyVideoFields(card) {
  const nodes=card?.workflow?.nodes;
  if(!isWorkflow(card)||!Array.isArray(nodes)||fieldsOf(card).some(field=>field.type==='video')||
     !nodes.some(node=>['H3ReferenceVideoLoader','VHS_LoadVideoFFmpeg'].includes(node?.type)&&Array.isArray(node.widgets_values)&&![2,4].includes(node.mode)))return;
  if(pendingVideoFields.has(card))return pendingVideoFields.get(card);
  const project=state.project,graph=canvasJSON.stringify(card.workflow);
  const operation=(async()=>{
    try {
      const response=await api('/api/workflow-fields',{method:'POST',body:canvasJSON.stringify({workflow:card.workflow,values:{}})});
      if(state.project!==project||cardById(card.id)!==card||canvasJSON.stringify(card.workflow)!==graph)return;
      const known=new Set(fieldsOf(card).map(field=>field.id));
      const added=(Array.isArray(response.fields)?response.fields:[]).filter(field=>field?.type==='video'&&
        ['H3ReferenceVideoLoader','VHS_LoadVideoFFmpeg'].includes(field.nodeType)&&typeof field.id==='string'&&!known.has(field.id));
      if(!added.length)return;
      card.fields=[...fieldsOf(card),...clone(added)];projectChanged();
      if(state.selected===card.id&&!editingField()){renderSingleCard(card.id);renderInspector();}
    }catch(error){console.warn('动作视频输入暂时无法更新',error.message);}
  })();
  pendingVideoFields.set(card,operation);
  try{return await operation;}finally{if(pendingVideoFields.get(card)===operation)pendingVideoFields.delete(card);}
}
function fieldValue(card,field) { return Object.hasOwn(card.values||{},field.id) ? card.values[field.id] : field.value; }
function kindOf(item) { const text=`${item.kind||''} ${item.name||''}`; return /motion|动作|动画/.test(text)?'motion':/edit|编辑/.test(text)?'edit':/video|视频/.test(text)?'video':'image'; }
function typeIcon(card) { return card.type==='video'?'video':card.type==='image'?'image':kindOf(card)==='motion'?'motion':kindOf(card)==='video'?'video':kindOf(card)==='edit'?'edit':'image'; }
function modelOf(card) { const text=`${card.name||''} ${(card.models||[]).join(' ')}`;return /万相|wan.?2/i.test(text)?'万相动画 · 本机运行':/H3|MiniMax/i.test(text)?'MiniMax H3 · 本机运行':/千问|qwen/i.test(text)?'千问图片 · 本机运行':'本机 ComfyUI 工作流'; }
function jobPercent(job) { const p=Number(job?.progress)||0;return Math.round(Math.max(0,Math.min(100,p<=1?p*100:p))); }
function jobBusy(job) { return job&&['queued','running','pending','submitted','submitting'].includes(job.status); }
function prettyName(item) { return (item.name||'未命名工作流').replace(/_/g,' · ').replace(/千问21/g,'千问 2.1').replace(/768方图/,'768 方图'); }
function briefName(item) { const kind=kindOf(item); return kind==='motion'?'跟随动作':kind==='edit'?'编辑图片':kind==='video'?'生成视频':'生成图片'; }
function descField(card) { const editable=fieldsOf(card).filter(f=>f.editable!==false&&!f.advanced);return editable.find(f=>f.role==='prompt'||(['text','textarea'].includes(f.type)&&/prompt|提示词|描述|要求/.test(`${f.key} ${f.label}`))) || editable.find(f=>f.type==='textarea') || editable.find(f=>f.type==='text'); }
function cardDescription(card) { const field=descField(card); return field ? fieldValue(card,field) : ''; }
function connectedRefs(card) { return state.project.edges.filter(e=>e.to===card.id&&!e.result).map(e=>cardById(e.from)).filter(Boolean); }
function referenceFields(card) { return fieldsOf(card).filter(f=>['image','video'].includes(f.type)&&f.editable!==false); }
function referenceSlotsHtml(card) { const fields=referenceFields(card);return fields.length?`<div class="reference-slots">${fields.map((f,i)=>{const edge=state.project.edges.find(e=>e.to===card.id&&e.fieldId===f.id),source=edge&&cardById(edge.from);return `<button type="button" class="reference-slot" data-reference-upload="${esc(f.id)}"><span>${esc(f.nodeTitle&&/[^\x00-\x7F]/.test(f.nodeTitle)?f.nodeTitle:`${f.label||'参考素材'} ${fields.length>1?i+1:''}`)}</span><small>${source?esc(source.name):fieldValue(card,f)?`已选：${esc(fieldValue(card,f))}`:`添加${f.type==='video'?'动作视频':'图片'} ${icon('plus')}`}</small></button>`;}).join('')}</div>`:''; }
function projectChanged() { changeSerial++;state.dirty=true;updateCanvasItem();renderCanvasTabs();safeStorageSave();setSaveStatus('未保存');clearTimeout(saveTimer);if(!state.importing&&!state.canvasBusy)saveTimer=setTimeout(()=>saveProject(false),700); }
function safeStorageSave() {
  try {
    if(state.recoveryDrafts.some(entry=>!entry.persisted&&entry.sourceId===(state.project.id||null)))return false;
    const key=projectStorageKey();localStorage.setItem(key+':state',canvasJSON.stringify({dirty:state.dirty,baseRevision:state.project.revision}));localStorage.setItem(key,canvasJSON.stringify(state.project));
    return true;
  } catch(error) { console.warn('本地备份失败',error);return false; }
}
async function saveProject(announce=true,allowImport=false) {
  if(state.importing&&!allowImport)return false;
  if(pendingBridgeWrite)await pendingBridgeWrite;
  const project=state.project;
  if(pendingSave){await pendingSave;if(state.project!==project)return false;return saveProject(announce,allowImport);}
  project.name=$('#project-name').value.trim()||'未命名画布';$('#project-name').value=project.name;safeStorageSave();
  const body=canvasJSON.stringify(project);if(!announce&&body===lastPersist)return true;
  const serial=changeSerial;state.saving=true;setSaveStatus('保存中…');
  const operation=(async()=>{
    try {const saved=await api(projectPath(project),{method:'PUT',body});if(saved.revision!=null)project.revision=saved.revision;if(saved.savedAt)project.savedAt=saved.savedAt;if(state.project!==project)return true;state.dirty=serial!==changeSerial;if(!state.dirty)lastPersist=canvasJSON.stringify(project);updateCanvasItem(project);renderCanvasTabs();safeStorageSave();setSaveStatus(state.dirty?'未保存':'已保存到本机');if(announce)toast('画布已保存到本机');return true;}
    catch(error){if(state.project===project){const backedUp=safeStorageSave();setSaveStatus(backedUp?'已存浏览器备份':'未保存，请先导出备份');if(announce)toast(`本机保存失败：${error.message}。${backedUp?'浏览器草稿已保留':'内容仍在当前窗口，请先导出备份'}`,6500);}return false;}
    finally{state.saving=false;if(state.project===project&&state.dirty&&serial!==changeSerial&&!state.importing&&!state.canvasBusy){clearTimeout(saveTimer);saveTimer=setTimeout(()=>saveProject(false),300);}}
  })();
  pendingSave=operation;try{return await operation;}finally{if(pendingSave===operation)pendingSave=null;}
}
function applyViewport() {
  const v=state.project.viewport,world=$('#canvas-world'),pixelRatio=window.devicePixelRatio||1;
  v.x=Math.round(v.x*pixelRatio)/pixelRatio;v.y=Math.round(v.y*pixelRatio)/pixelRatio;
  // Layout zoom rerasterizes glyphs at the displayed size instead of scaling a cached text layer.
  world.style.transform='none';world.style.zoom=String(v.zoom);
  world.style.left=`${v.x/v.zoom}px`;world.style.top=`${v.y/v.zoom}px`;
  $('#zoom-value').textContent=`${Math.round(v.zoom*100)}%`;
  $('#viewport').style.backgroundSize=`${20*v.zoom}px ${20*v.zoom}px`;$('#viewport').style.backgroundPosition=`${v.x}px ${v.y}px`;
}
function worldPoint(clientX,clientY) { const r=$('#viewport').getBoundingClientRect(),v=state.project.viewport;return {x:(clientX-r.left-v.x)/v.zoom,y:(clientY-r.top-v.y)/v.zoom}; }
function centerPoint() { const r=$('#viewport').getBoundingClientRect();return worldPoint(r.left+r.width/2,r.top+r.height/2); }
function setZoom(zoom,point) { const v=state.project.viewport,r=$('#viewport').getBoundingClientRect();const cx=point?.x??r.left+r.width/2,cy=point?.y??r.top+r.height/2;const p=worldPoint(cx,cy);v.zoom=Math.max(.25,Math.min(2,zoom));v.x=cx-r.left-p.x*v.zoom;v.y=cy-r.top-p.y*v.zoom;applyViewport();projectChanged(); }
function fitView() {
  const cards=state.project.cards;if(!cards.length){state.project.viewport={x:110,y:95,zoom:1};applyViewport();return;}
  const r=$('#viewport').getBoundingClientRect();let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
  cards.forEach(c=>{const el=$(`.card[data-id="${c.id}"]`);minX=Math.min(minX,c.x);minY=Math.min(minY,c.y);maxX=Math.max(maxX,c.x+(el?.offsetWidth||294));maxY=Math.max(maxY,c.y+(el?.offsetHeight||240));});
  const z=Math.max(.25,Math.min(1,(r.width-100)/(maxX-minX),(r.height-130)/(maxY-minY)));state.project.viewport={x:(r.width-(maxX-minX)*z)/2-minX*z,y:(r.height-(maxY-minY)*z)/2-minY*z,zoom:z};applyViewport();projectChanged();
}
function renderCategories() { const categories=['全部',...new Set(state.catalog.map(c=>c.category).filter(Boolean))];$('#category-tabs').innerHTML=categories.map(c=>`<button class="${c===state.category?'active':''}" data-category="${esc(c)}">${esc(c)}</button>`).join(''); }
function renderCatalog() {
  $('#catalog-count').textContent=state.catalog.length;renderCategories();
  const items=state.catalog.filter(w=>(state.category==='全部'||w.category===state.category)&&`${w.name} ${w.description||''} ${w.category}`.toLowerCase().includes(state.search.toLowerCase()));
  $('#workflow-list').innerHTML=items.length?items.map(w=>`<button class="workflow-item" data-workflow="${esc(w.id)}"><div class="workflow-item-head">${icon(typeIcon({type:'workflow',...w}))}<span class="workflow-name">${esc(prettyName(w))}</span><span class="add-indicator">${icon('plus')}</span></div><p>${esc(w.description||`${briefName(w)}，保留完整专业工作流`)}</p><div class="workflow-tags"><span>${esc(w.category||'工作流')}</span>${w.node_count?`<span>· ${w.node_count} 个处理步骤</span>`:''}</div></button>`).join(''):'<div class="list-empty">没有找到匹配的工作流。<br>换一个名称，或导入你的工作流 JSON。</div>';
}
function renderCard(card) {
  const selected=state.selected===card.id?' selected':'';
  if(isJoin(card))return renderJoinCard(card);
  if(!isWorkflow(card)) {
    return `<article class="card media-card${selected}" data-id="${card.id}" style="left:${card.x}px;top:${card.y}px" tabindex="0" aria-label="${esc(card.name)}"><div class="card-header"><span class="card-type-icon">${icon(typeIcon(card))}</span><span class="card-title">${esc(card.name||'素材')}</span><button class="icon-button card-more" title="卡片设置" aria-label="卡片设置">${icon('more')}</button></div>${card.type==='video'?`<video class="media-preview" src="${esc(card.url)}" controls preload="metadata"></video>`:`<img class="media-preview" src="${esc(card.url)}" alt="${esc(card.name||'图片素材')}" draggable="false">`}<button class="port port-output" title="拖动连到工作流作为参考素材" aria-label="连接参考素材输出" data-port="output"></button></article>`;
  }
  const description=cardDescription(card);const summaries=fieldsOf(card).filter(f=>!f.advanced && ['width','height','duration','length','batch_size'].some(s=>`${f.key}`.includes(s))).slice(0,3).map(f=>`${f.label} ${fieldValue(card,f)}`);
  const job=state.jobs.filter(j=>jobForProject(j)&&j.instance_id===card.id).at(-1);const busy=jobBusy(job);const error=job?.status==='error'||job?.status==='failed';
  return `<article class="card${selected}" data-id="${card.id}" style="left:${card.x}px;top:${card.y}px" tabindex="0" aria-label="${esc(card.name)}"><div class="card-header"><span class="card-type-icon">${icon(typeIcon(card))}</span><span class="card-title">${esc(card.name)}</span><button class="icon-button card-more" title="工作流设置" aria-label="工作流设置">${icon('more')}</button></div><div class="card-content"><p class="card-model"><span class="model-dot"></span>${modelOf(card)}</p><div class="card-description-label">${kindOf(card)==='edit'?'修改要求':'画面描述'}</div><p class="card-description${description?'':' empty'}">${esc(description||'选中卡片，写下你想创作的画面…')}</p><div class="card-settings">${summaries.length?summaries.map(s=>`<span>${esc(s)}</span>`).join(''):`<span>${esc(card.category||'完整工作流')}</span><span>原图保留</span>`}</div><div class="card-footer"><span class="card-reference">${icon('link')}${connectedRefs(card).length?`${connectedRefs(card).length} 份参考素材`:'连接参考素材'}</span><button class="generate-button" data-run="${card.id}"${busy?' disabled':''}>${icon('play')}${busy?'正在处理':job?.outputs?.length?'再次生成':'开始生成'}</button></div>${error?`<p class="card-status error">${esc(readableJobError(job.error))}</p>`:busy?`<p class="card-status">${job.status==='running'?'正在生成':'等待本机引擎'}${job.progress?` · ${jobPercent(job)}%`:''}</p>`:''}</div>${state.selected===card.id?renderQuickControls(card):''}<button class="port port-input" title="将图片或视频连接到这里" aria-label="参考素材输入" data-port="input"></button><button class="port port-output" title="将最新结果连接到下一张卡片" aria-label="生成结果输出" data-port="output"></button>${busy?`<div class="card-progress"><span style="width:${Math.max(4,jobPercent(job))}%"></span></div>`:''}</article>`;
}
function renderCards() {
  if(editingField())return;
  $('#cards').innerHTML=state.project.cards.map(renderCard).join('');$('#card-count').textContent=`${state.project.cards.length} 张卡片`;$('#empty-hint').style.display=state.project.cards.length?'none':'flex';renderMedia();
  $$('.media-preview').forEach(el=>el.addEventListener('load',drawConnections,{once:true}));requestAnimationFrame(drawConnections);
}
function renderSingleCard(id) {const card=cardById(id),old=$(`.card[data-id="${id}"]`);if(editingField()&&document.activeElement?.closest?.('.card')?.dataset.id===id)return;if(card&&old){old.outerHTML=renderCard(card);requestAnimationFrame(drawConnections);} }
function drawConnections() {
  const portPoint=(id,side)=>{const c=cardById(id),el=$(`.card[data-id="${id}"]`);if(!c||!el)return null;return{x:c.x+(side==='output'?el.offsetWidth:0),y:c.y+el.offsetHeight/2};};
  const curve=(a,b)=>{const d=Math.max(65,Math.abs(b.x-a.x)*.5);return`M ${a.x} ${a.y} C ${a.x+d} ${a.y}, ${b.x-d} ${b.y}, ${b.x} ${b.y}`;};
  let paths=state.project.edges.map(e=>{const a=portPoint(e.from,'output'),b=portPoint(e.to,'input');return a&&b?`<path class="connection-hit" d="${curve(a,b)}" data-edge="${esc(e.id)}"><title>点击断开连线，保留两张卡片</title></path><path class="connection-path" d="${curve(a,b)}" data-edge="${esc(e.id)}" aria-label="断开连线，保留卡片"/>`:'';}).join('');
  if(state.connecting){const a=portPoint(state.connecting.from,'output');if(a&&state.connecting.point)paths+=`<path class="connection-path preview" d="${curve(a,state.connecting.point)}"/>`;}
  $('#connections').innerHTML=paths;
}
function selectCard(id, render=true) {
  const previous=state.selected;if(previous!==id){promptSelectionSerial++;if(document.activeElement?.closest?.('[data-quick-card]')?.dataset.quickCard===previous)document.activeElement.blur?.();}state.selected=id;
  $$('.card.selected').forEach(el=>el.classList.remove('selected'));$(`.card[data-id="${id}"]`)?.classList.add('selected');
  if(previous!==id)for(const identity of [previous,id])if(isWorkflow(cardById(identity)))renderSingleCard(identity);
  if(render)renderInspector();
  const card=cardById(id);if(isWorkflow(card))refreshLegacyVideoFields(card);
}
function quickFields(card) {
  const fields=fieldsOf(card).filter(field=>!field.advanced&&field.editable!==false&&!['image','video','audio'].includes(field.type));
  const order=['start_seconds','end_seconds','aspect_ratio','megapixels','width','height','noise_seed'];
  const rank=field=>order.includes(field.key)?order.indexOf(field.key):order.length;
  const prompt=descField(card),other=fields.filter(field=>field.id!==prompt?.id).sort((a,b)=>rank(a)-rank(b)).slice(0,6);
  return [...(prompt&&prompt.editable!==false?[prompt]:[]),...other];
}
function renderQuickControls(card) {
  const fields=quickFields(card),prompt=descField(card),other=fields.filter(field=>field.id!==prompt?.id);
  return `<section class="card-quick" data-quick-card="${card.id}" aria-label="节点常用设置"><div class="quick-continuation">${continuationHtml(card)}</div>${prompt?.editable!==false&&prompt?`<div class="quick-prompt">${fieldHtml(card,prompt,'quick')}</div>`:''}<div class="quick-grid">${other.map(field=>fieldHtml(card,field,'quick')).join('')}</div></section><div class="quick-actions"><button type="button" class="small-button quick-details" data-details="${card.id}">${icon('settings')}详细设置</button></div>`;
}
function fieldHtml(card,field,scope='inspector') {
  const value=fieldValue(card,field), id=`${scope}-${card.id}-${field.id}`,label=esc(scope==='quick'&&/四步加速/.test(field.label||'')?'快速生成（4步）':field.label||field.key),help=field.help||field.description||'';
  if(field.type==='image'||field.type==='video'||field.role==='image'||field.role==='video'||/image|video|asset|file/.test(field.type||'')) return '';
  let input;
  if(field.sensitive)input=`<input id="${esc(id)}" type="password" value="保存在原始工作流中" disabled autocomplete="off">`;
  else if(typeof value==='bigint')input=`<input id="${esc(id)}" data-field="${esc(field.id)}" type="text" value="${esc(value)}" readonly>`;
  else if(Array.isArray(field.options)&&field.options.length)input=`<select id="${esc(id)}" data-field="${esc(field.id)}">${field.options.map(o=>{const val=typeof o==='object'?(o.value??o.label):o;const text=typeof o==='object'?(o.label??o.value):o;return`<option value="${esc(val)}"${String(val)===String(value)?' selected':''}>${esc(text)}</option>`;}).join('')}</select>`;
  else if(field.type==='boolean'||typeof value==='boolean')input=`<input id="${esc(id)}" data-field="${esc(field.id)}" type="checkbox"${value?' checked':''}>`;
  else if(['number','integer','float','int'].includes(field.type)||typeof value==='number')input=`<input id="${esc(id)}" data-field="${esc(field.id)}" type="number" value="${esc(value)}"${field.min!=null?` min="${esc(field.min)}"`:''}${field.max!=null?` max="${esc(field.max)}"`:''} step="${esc(field.step??(field.type==='float'?.1:1))}">`;
  else if(field.type==='textarea'||String(value??'').length>60||/prompt|描述|要求/.test(`${field.key} ${field.label}`))input=`<textarea id="${esc(id)}" data-field="${esc(field.id)}" spellcheck="false" rows="${field.advanced?3:5}" placeholder="描述你希望得到的画面">${esc(value)}</textarea>`;
  else input=`<input id="${esc(id)}" data-field="${esc(field.id)}" value="${esc(value)}" spellcheck="false">`;
  if(field.editable===false)input=input.replace(/<(input|textarea|select) /,'<$1 disabled ');
  const optimizable=isWorkflow(card)&&field.editable!==false&&descField(card)?.id===field.id,previous=card.promptOptimization;
  const tools=optimizable?`<span class="prompt-tools"><button type="button" class="icon-button small" data-optimize-prompt="${card.id}" title="优化提示词 · 使用设置中的 AI 接口和参考方式" aria-label="优化提示词"${promptOptimizer.busy?' disabled':''}>${icon('sparkle')}</button>${previous?.fieldId===field.id?`<button type="button" class="icon-button small" data-undo-prompt="${card.id}" title="撤销上次优化" aria-label="撤销上次优化"${String(value??'')!==previous.after?' disabled':''}>${icon('undo')}</button>`:''}</span>`:'';
  const translation=optimizable&&previous?.fieldId===field.id&&previous.translation?`<details class="saved-prompt-translation" data-prompt-translation><summary>${String(value??'')===previous.translationFor?'中文对照':'中文对照（对应优化初稿）'}</summary><p>${esc(previous.translation)}</p></details>`:'';
  return `<label class="field" for="${esc(id)}"><span class="field-heading"><span>${label}</span>${tools}</span>${input}${help?`<small>${esc(help)}</small>`:''}</label>${translation}`;
}
function renderInspector() { drawInspector();const card=selectedCard();if(isWorkflow(card))$('.reference-list',$('#inspector'))?.insertAdjacentHTML('beforebegin',referenceSlotsHtml(card));if(!card&&state.rightTab==='settings')state.rightTab='media';updateRightPanel(); }
function drawInspector() {
  const card=selectedCard(),panel=$('#inspector');
  if(!card){panel.innerHTML='';return;}
  if(isJoin(card)){panel.innerHTML=`<div class="inspector-head"><h2>${esc(card.name)}</h2></div><div class="inspector-form"><label class="field"><span>名称</span><input data-rename="${card.id}" value="${esc(card.name)}"></label><button class="icon-button" data-delete="${card.id}" title="移除节点" aria-label="移除节点">${icon('trash')}</button></div>`;return;}
  if(!isWorkflow(card)){const sourceLinked=state.project.edges.some(edge=>edge.to===card.id&&edge.result);panel.innerHTML=`<div class="inspector-head"><span class="inspector-type">${icon(typeIcon(card))}${card.fromJob?'生成结果':'本地素材'}</span><h2>${esc(card.name)}</h2></div><div class="inspector-form">${card.type==='video'?`<video class="media-large" src="${esc(card.url)}" controls></video>`:`<img class="media-large" src="${esc(card.url)}" alt="${esc(card.name)}">`}<label class="field"><span>素材名称</span><input data-rename="${card.id}" value="${esc(card.name)}"></label><div class="media-actions"><a class="muted-link icon-button" href="${esc(card.url)}" download="${esc(card.name)}" target="_blank" rel="noopener" title="保存素材" aria-label="保存素材">${icon('download')}</a><button class="icon-button" data-archive="${card.id}" title="收纳到素材栏" aria-label="收纳到素材栏">${icon('folder')}</button>${sourceLinked?`<button class="icon-button" data-unlink-source="${card.id}" title="断开来源连线" aria-label="断开来源连线">${icon('link')}</button>`:''}</div></div>`;return;}
  const fields=fieldsOf(card),quickIds=new Set(quickFields(card).map(field=>field.id)),normal=fields.filter(f=>!f.advanced&&f.editable!==false&&!quickIds.has(f.id)), advanced=fields.filter(f=>f.advanced),refs=connectedRefs(card);const lastJob=state.jobs.filter(j=>jobForProject(j)&&j.instance_id===card.id).at(-1);const busy=jobBusy(lastJob);
  panel.innerHTML=`<div class="inspector-head"><span class="inspector-type">${icon(typeIcon(card))}${briefName(card)}</span><h2>${esc(card.name)}</h2><p>${modelOf(card)} · 完整工作流已保留</p></div><div class="inspector-form">${card.error?`<div class="error-banner">${esc(card.error)}</div>`:''}${advanced.length?`<details class="advanced-fields" open><summary>详细生成参数</summary>${advanced.map(f=>fieldHtml(card,f)).join('')}</details>`:''}${normal.map(f=>fieldHtml(card,f)).join('')}<div class="field"><span class="field-label">参考素材 <span class="count">${refs.length}</span></span><div class="reference-list">${refs.map(ref=>`<div class="reference-row">${ref.type==='image'?`<img src="${esc(ref.url)}" alt="">`:icon(ref.type==='video'?'video':'canvas')}<span>${esc(ref.name)}</span><button data-unlink="${ref.id}" title="移除参考关系" aria-label="移除参考关系">×</button></div>`).join('')}</div><button class="reference-add" id="add-reference">${icon('plus')}上传图片或动作视频</button><small>也可以从素材卡的圆点拖出连线。</small></div>${continuationHtml(card)}<div class="raw-summary">${card.workflow?.nodes?.length||card.node_count||'完整'} 个专业步骤 · 节点、连线和子图保留<br><button class="muted-link" id="inspect-professional">查看完整专业工作流</button></div><div class="workflow-dependencies"><button class="small-button" data-check-workflow="${card.id}">${icon('check')}检查节点与模型</button>${card.dependencyStatus?`<p class="field-help">${esc(card.dependencyStatus.ok?'节点和模型已就绪':[...(card.dependencyStatus.missingNodes||[]).map(name=>'缺少节点：'+name),...(card.dependencyStatus.missingModels||[]).map(item=>'缺少模型：'+item.name)].join('；'))}</p>`:''}${(card.warnings||[]).map(message=>`<p class="field-help">${esc(message)}</p>`).join('')}</div><button class="danger-button" data-delete="${card.id}">${icon('trash')}移除当前卡片</button></div><div class="inspector-footer"><button class="primary-button" data-run="${card.id}"${busy?' disabled':''}>${icon('play')}${busy?'等待生成完成':lastJob?.outputs?.length?'再次生成':'开始生成'}</button><p class="field-help">保留工作流与参考素材，可反复生成；每次作品分别保存。</p></div>`;
}
async function addWorkflow(id,position,select=true) {
  const project=state.project;
  try {
    const entry=state.catalog.find(w=>w.id===id)||{};const detail=await api(`/api/workflows/${encodeURIComponent(id)}`);if(state.project!==project||state.canvasBusy)return;const p=position||centerPoint();const same=state.project.cards.filter(c=>c.type==='workflow').length;
    const card={id:uid(),type:'workflow',workflow_id:id,name:prettyName(detail.name?detail:entry),category:detail.category||entry.category,description:entry.description,models:entry.models||[],kind:entry.kind||kindOf(entry),workflow:clone(detail.workflow),compatibility:clone(detail.compatibility||{}),warnings:clone(detail.warnings||[]),fields:clone(detail.fields||entry.fields||[]),values:{},x:Math.round(p.x-147+(same?Math.min(same,5)*15:0)),y:Math.round(p.y-125+(same?Math.min(same,5)*15:0))};
    fieldsOf(card).filter(f=>f.editable!==false).forEach(f=>card.values[f.id]=clone(f.value??''));state.project.cards.push(card);renderCards();if(select)selectCard(card.id);projectChanged();return card;
  } catch(error){if(state.project===project)toast(`无法加载工作流：${error.message}`,5000);}
}
function quickWorkflow(kind,position) {
  const match=state.catalog.filter(w=>kindOf(w)===kind&&!/旧版|进阶|项目/.test(w.category||''));
  const preferred=kind==='video'?match.find(w=>/多图.*均衡/.test(w.name)):kind==='motion'?match.find(w=>/按秒/.test(w.name)):match.find(w=>/快速8步/.test(w.name));
  const item=preferred||match[0];if(item)return addWorkflow(item.id,position);else toast('此类工作流暂未载入，可从工作流库导入。');
}
async function uploadFiles(files,position,referenceTo,fieldId) {
  const project=state.project;
  const accepted=[...files].filter(f=>/^image\/|^video\//.test(f.type));if(!accepted.length){toast('请选择图片或视频文件');return;}
  const p=position||centerPoint();toast(`正在添加 ${accepted.length} 份素材…`);
  for(let i=0;i<accepted.length;i++) {
    const file=accepted[i];try {const asset=await api('/api/assets',{method:'POST',body:file,timeoutMs:120000,headers:{'X-Filename':encodeURIComponent(file.name),'Content-Type':file.type||'application/octet-stream'}});if(state.project!==project||state.canvasBusy)continue;const card={id:uid(),type:asset.type|| (file.type.startsWith('video/')?'video':'image'),assetId:asset.id,url:asset.url,name:asset.name||file.name,x:Math.round(p.x-136+i*40),y:Math.round(p.y-120+i*40)};state.project.cards.push(card);if(referenceTo&&cardById(referenceTo)){const refField=i===0?fieldId:undefined;if(refField)state.project.edges=state.project.edges.filter(e=>!(e.to===referenceTo&&e.fieldId===refField));state.project.edges.push({id:uid(),from:card.id,to:referenceTo,...(refField?{fieldId:refField}:{})});}renderCards();selectCard(referenceTo&&cardById(referenceTo)?referenceTo:card.id);projectChanged();}catch(error){toast(`素材添加失败：${error.message}`,5000);}
  }
  await refreshMedia();if(state.project!==project)toast('素材已存入素材库，可拖到需要的画布');
}
function removeCard(id) {const removed=cardById(id);if(removed?.outputKey){state.project.dismissedOutputKeys||=[];if(!state.project.dismissedOutputKeys.includes(removed.outputKey))state.project.dismissedOutputKeys.push(removed.outputKey);}state.project.cards=state.project.cards.filter(c=>c.id!==id);state.project.edges=state.project.edges.filter(e=>e.from!==id&&e.to!==id);if(state.selected===id)state.selected=null;if(state.bridgeActive===id)state.bridgeActive=null;renderCards();renderInspector();projectChanged();}
function createsCycle(from,to) {const pending=[to],seen=new Set();while(pending.length){const id=pending.pop();if(id===from)return true;if(seen.has(id))continue;seen.add(id);state.project.edges.filter(edge=>edge.from===id).forEach(edge=>pending.push(edge.to));}return false;}
function connectCards(from,to) {
  const source=cardById(from),target=cardById(to);if(!source||!target||(!isWorkflow(target)&&!isJoin(target)))return false;
  if(createsCycle(from,to)){toast('这条连线会形成循环');return false;}
  if(isJoin(target)&&!videoReference(source)&&!(isWorkflow(source)&&kindOf(source)!=='image'&&kindOf(source)!=='edit')&&!isJoin(source)){toast('拼接只接受视频');return false;}
  if(state.project.edges.some(edge=>edge.from===from&&edge.to===to)){toast('这份素材已经连接');return false;}
  state.project.edges.push({id:uid(),from,to});renderCards();selectCard(to);projectChanged();return true;
}
function updateField(el) {
  const identity=el.closest?.('[data-quick-card]')?.dataset.quickCard,card=identity?cardById(identity):selectedCard(),field=fieldsOf(card).find(f=>f.id===el.dataset.field);if(!field)return;
  if(field.editable===false)return;let value=el.type==='checkbox'?el.checked:el.value;if(el.type==='number'){if(el.value==='')return;value=Number(el.value);if(!Number.isFinite(value))return;}
  card.values[field.id]=value;card.modified=true;if(!identity)renderSingleCard(card.id);projectChanged();
  const translationSummary=$('[data-prompt-translation] summary');if(translationSummary&&card.promptOptimization?.fieldId===field.id)translationSummary.textContent=String(value)===card.promptOptimization.translationFor?'中文对照':'中文对照（对应优化初稿）';
  // 只在用户明确打开这张图后推送，避免初次选择覆盖 ComfyUI 里尚未保存的图。
  if(state.bridgeActive===card.id){clearTimeout(bridgeTimer);bridgeTimer=setTimeout(()=>loadCardIntoEngine(card,false),600);}
}
function referencePayload(card) {
  return state.project.edges.filter(e=>e.to===card.id&&!e.result).flatMap(edge=>{const source=cardById(edge.from);if(!source)return[];return (isWorkflow(source)||isJoin(source))?(source.outputs||[]).map(o=>({assetId:o.assetId||o.id,url:o.url,type:o.type,name:o.name,fieldId:edge.fieldId})): [{assetId:source.assetId,url:source.url,type:source.type,name:source.name,fieldId:edge.fieldId}];});
}
async function checkWorkflow(id) {
  const project=state.project,card=cardById(id);if(!isWorkflow(card))return;
  if(!state.connected){toast('请先连接 ComfyUI，再检查节点和模型。');return;}
  toast('正在检查工作流所需节点与模型…');
  try{const result=await api('/api/preflight',{method:'POST',body:canvasJSON.stringify({workflow:card.workflow,values:card.values,checkReferences:false})});
    if(state.project!==project||cardById(id)!==card)return;
    card.dependencyStatus=result;renderInspector();toast(result.ok?'节点和模型检查通过；生成时会继续检查参考素材。':'检测到缺少的节点或模型，请查看详细设置。',6000);
  }catch(error){toast(`无法检查依赖：${error.message}`,6000);}
}
async function runCard(id) {
  const project=state.project,card=cardById(id);if(state.canvasBusy||!isWorkflow(card))return;
  const busy=state.jobs.some(j=>jobForProject(j)&&j.instance_id===id&&jobBusy(j));if(busy)return;
  if(!state.connected){toast('本地引擎尚未连接，点击右上角连接状态可启动。',5000);$('#connection-dialog').showModal();return;}
  card.error=null;selectCard(id);const loadingJob={id:`pending-${uid()}`,canvas_id:project.id,instance_id:id,name:card.name,status:'pending',progress:0,outputs:[]};state.jobs.push(loadingJob);renderCards();renderInspector();renderJobs();
  try {const result=await api('/api/run',{method:'POST',timeoutMs:180000,body:canvasJSON.stringify({workflow_id:card.workflow_id,workflow:card.workflow,values:card.values,references:referencePayload(card),...(card.continuation?{continuation:continuationPayload(card)}:{}),instance_id:card.id,canvas_id:project.id})});loadingJob.id=result.job_id||result.id;loadingJob.status=result.status||'queued';toast('已加入本机生成队列');await refreshJobs();}
  catch(error){const missing=error.data?.preflight?.missingReferences||[];const detail=missing.length?` 请添加：${missing.map(r=>r.label||r.name).join('、')}`:'';const message=error.timedOut?'提交响应超时，尚未确认任务是否开始。请查看任务记录或专业引擎队列，确认后再生成，避免重复提交。':`生成未能开始：${error.message}${detail}`;loadingJob.status='error';loadingJob.error=message;card.error=message;toast(message,9000);if(state.project===project){renderCards();renderInspector();}renderJobs();}
}
const statusNames={pending:'准备任务',submitting:'提交任务中',queued:'排队中',submitted:'已交给引擎',running:'生成中',completed:'已完成',complete:'已完成',success:'已完成',failed:'生成失败',error:'生成失败',cancelled:'已取消',canceled:'已取消'};
function renderJobs() {
  $('#task-count').textContent=state.jobs.length;const waiting=state.jobs.filter(j=>jobBusy(j)&&j.status!=='running').length;const running=state.jobs.filter(j=>j.status==='running').length;$('#queue-summary').textContent=running?`正在生成 ${running} 个 · 等待 ${waiting} 个`:waiting?`${waiting} 个任务等待中`:'本机串行运行';
  $('#task-list').innerHTML=state.jobs.length?[...state.jobs].reverse().map(job=>{const card=jobForProject(job)?cardById(job.instance_id):null,out=job.outputs?.[0],bad=['error','failed'].includes(job.status),progress=jobPercent(job);return`<button class="task-item" data-task-canvas="${esc(job.canvas_id||'')}" data-task-card="${esc(job.instance_id||'')}" title="${esc(job.error||statusNames[job.status]||job.status)}"><span class="task-thumb">${out?.type==='image'?`<img src="${esc(out.url)}" alt="生成结果">`:icon(out?.type==='video'?'video':card?typeIcon(card):'canvas')}</span><span class="task-details"><strong>${esc(job.name||card?.name||'生成任务')}</strong><p class="${bad?'error':''}">${esc(bad?readableJobError(job.error):(statusNames[job.status]||job.status))}${job.status==='running'?` · ${progress}%`:''}</p>${jobBusy(job)?`<span class="task-progress"><span style="width:${Math.max(3,progress)}%"></span></span>`:''}</span></button>`;}).join(''):'<div class="history-empty">开始生成后，进度和作品会留在这里。</div>';
}
function importResults(jobs) {
  if(state.canvasBusy)return;
  let changed=false;
  [...jobs].sort((a,b)=>String(a.createdAt||'').localeCompare(String(b.createdAt||''))).forEach(job=>{if(!jobForProject(job))return;const source=cardById(job.instance_id)||state.project.cards.find(card=>isJoin(card)&&card.legacyJobId===job.id);if(!source)return;if(job.outputs?.length)source.outputs=clone(job.outputs);
    (job.outputs||[]).forEach((out,index)=>{const outputKey=`${job.id}:${index}`;if(!out.url||state.project.dismissedOutputKeys?.includes(outputKey)||state.project.cards.some(c=>c.outputKey===outputKey))return;const point=vacantPosition({x:source.x+370,y:source.y});const card={id:uid(),type:out.type==='video'?'video':'image',url:out.url,name:out.name||`${source.name} · 结果 ${index+1}`,assetId:out.assetId||out.id,fromJob:job.id,outputKey,...point};state.project.cards.push(card);state.project.edges.push({id:uid(),from:source.id,to:card.id,result:true});changed=true;});
  });if(changed){renderCards();projectChanged();toast('生成完成，作品已添加到画布');}
}
async function refreshJobs() {try{const data=await api('/api/jobs');const fresh=data.items||data.jobs||[];const locals=state.jobs.filter(j=>String(j.id).startsWith('pending-'));const previous=canvasJSON.stringify(state.jobs);state.jobs=[...fresh,...locals];importResults(fresh);if(previous!==canvasJSON.stringify(state.jobs)){renderJobs();renderCards();if(selectedCard()&&!editingField())renderInspector();await refreshMedia();}renderMedia();}catch(error){console.warn('任务状态读取失败',error.message);}}
async function refreshStatus() {
  if(statusRequest)return statusRequest;
  statusRequest=updateStatus();
  try{return await statusRequest;}finally{statusRequest=null;}
}
async function updateStatus() {
  const wasConnected=state.connected;
  try {const data=await api('/api/comfy/status',{timeoutMs:8000}),startup=data.startup||{};state.serviceConnected=true;state.connected=!!data.connected;state.bridgeConnected=!!data.bridgeConnected;state.starting=!state.connected&&(engineStartPending||startup.status==='starting');state.engineUrl=data.url||(startup.url?.startsWith('http://')?startup.url:null)||state.engineUrl;if(document.activeElement!==$('#engine-url'))$('#engine-url').value=state.engineUrl;const failed=!state.connected&&startup.status==='error',label=state.connected?'本地引擎已连接':state.starting?'引擎启动中':failed?'引擎启动失败':'本地引擎未连接';$('#connection-label').textContent=label;$('#connection').className=`connection ${state.connected?'connected':'offline'}`;$('#connection-detail').textContent=state.connected?`已连接 ${state.engineUrl}${state.bridgeConnected?'，专业同步桥已就绪。':'。正在等待专业同步桥连接。'}`:state.starting?'正在自动启动本机 ComfyUI，等待引擎就绪后会自动连接。':failed?`${startup.message||'ComfyUI 未能启动。'}${startup.log?` 日志：${startup.log}`:''}`:'ComfyUI 尚未运行。可在设置中填写 ComfyUI 路径，或手动启动后连接。';$('#reconnect-engine').textContent=state.connected?'重新连接':state.starting?'引擎启动中…':failed?'重试启动':'启动本机引擎';$('#reconnect-engine').disabled=state.starting&&!state.connected;$('#bridge-label').textContent=state.bridgeConnected?'专业同步桥已连接':'等待专业同步桥连接';
    if(state.connected){const frame=$('#comfy-frame');if(engineFrameDisconnected||reloadEngineFrame||frame.dataset.engineUrl!==state.engineUrl){frame.src=state.engineUrl;frame.dataset.engineUrl=state.engineUrl;reloadEngineFrame=false;engineFrameDisconnected=false;}}else engineFrameDisconnected=true;
    if(state.connected&&!wasConnected){renderCards();renderJobs();if(selectedCard()&&!isWorkflow(selectedCard()))renderInspector();}
  }catch(error){state.serviceConnected=false;state.connected=false;state.bridgeConnected=false;state.starting=false;$('#connection-label').textContent='画布服务恢复中';$('#connection').className='connection offline';$('#connection-detail').textContent='画布后台暂时断开，软件会自动恢复连接。若长时间未恢复，请重新打开桌面的映流。你的画布草稿仍保留。';$('#reconnect-engine').disabled=false;$('#reconnect-engine').textContent='重新连接服务';$('#bridge-label').textContent='等待画布服务恢复';}
}
async function pollConnection() {
  if(pollRequest)return pollRequest;
  clearTimeout(pollTimer);
  pollRequest=(async()=>{await Promise.allSettled([refreshStatus(),refreshJobs()]);await refreshBridge();})();
  try{await pollRequest;}finally{pollRequest=null;pollTimer=setTimeout(pollConnection,2200);}
}
async function refreshBridge() {
  if(!state.connected||state.canvasBusy)return;
  if(pendingBridgeWrite)return pendingBridgeWrite;
  const project=state.project;
  try {
    const data=await api('/api/comfy/bridge');if(state.project!==project||state.canvasBusy)return;
    state.bridgeConnected=!!data.clientConnected;$('#bridge-label').textContent=state.bridgeConnected?'专业同步桥已连接':'等待专业同步桥连接';if(data.revision!=null)state.bridgeRevision=data.revision;
    if(typeof data.workflowId==='string'&&data.workflowId.startsWith('continuation-')&&data.workflowId!==state.bridgeActive&&
       (state.bridgeActive||state.bridgeTemporary===data.workflowId)) {
      state.bridgeActive=null;state.bridgeHash=null;state.bridgeTemporary=data.workflowId;
      $('#bridge-label').textContent='专业窗口为接续运行图，重新打开卡片可同步';return;
    }
    if(state.bridgeTemporary&&data.workflowId!==state.bridgeTemporary)state.bridgeTemporary=null;
    const card=state.bridgeActive?cardById(state.bridgeActive):null;if(!card||!data.workflow||data.workflowId!==card.id)return;
    const serialized=canvasJSON.stringify(data.workflow);if(state.bridgeHash===serialized)return;state.bridgeHash=serialized;
    // Finish this graph's mapping before a save or canvas switch can leave it behind.
    const operation=(async()=>{
      const workflow=clone(data.workflow);let prepared,error;
      try{prepared=await api('/api/prepare-workflow',{method:'POST',body:canvasJSON.stringify({workflow,values:{}})});}catch(problem){error=problem;}
      if(state.project!==project||state.bridgeHash!==serialized)return;
      card.workflow=workflow;card.fields=clone(prepared?.fields||[]);card.values={};fieldsOf(card).filter(field=>field.editable!==false).forEach(field=>card.values[field.id]=clone(field.value??''));
      if(error)card.error=`专业图已保留，中文参数映射更新失败：${error.message}`;
      renderSingleCard(card.id);if(state.selected===card.id)renderInspector();projectChanged();
    })();
    pendingBridgeWrite=operation;try{await operation;}finally{if(pendingBridgeWrite===operation)pendingBridgeWrite=null;}
  }catch(error){console.warn('同步桥读取失败',error.message);}
}
async function loadCardIntoEngine(card,announce=true) {
  if(!isWorkflow(card)){toast('请先选择一张工作流卡片');return;}
  if(!Array.isArray(card.workflow?.nodes)){toast('这是 API 工作流，可直接点击生成；完整节点布局请导入普通工作流 JSON。',6500);return;}
  if(!state.connected){toast('请先启动本机引擎');$('#connection-dialog').showModal();return;}
  const project=state.project;if(state.canvasBusy||cardById(card.id)!==card)return;
  try{const result=await api('/api/comfy/bridge',{method:'POST',body:canvasJSON.stringify({opId:uid(),type:'load',revision:state.bridgeRevision,workflow_id:card.workflow_id,workflow:card.workflow,workflowId:card.id,values:card.values,references:referencePayload(card)})});if(state.project!==project)return;state.bridgeTemporary=null;state.bridgeActive=card.id;state.bridgeHash=canvasJSON.stringify(card.workflow);if(result.revision!=null)state.bridgeRevision=result.revision;if(announce)toast('已发送到专业模式；专业图的修改会同步回这张卡片');}
  catch(error){if(state.project!==project)return;if(error.status===409){state.bridgeActive=null;toast('专业图已发生变化，已保留两边内容。再次打开前请确认专业图已保存。',7000);await refreshBridge();}else toast(`专业工作流未能打开：${error.message}`,6000);}
}
async function setMode(mode) {state.mode=mode;$('#canvas-area').classList.toggle('is-professional',mode==='professional');$$('[data-mode]').forEach(b=>{b.classList.toggle('active',b.dataset.mode===mode);b.setAttribute('aria-pressed',String(b.dataset.mode===mode));});$('#professional-view').classList.toggle('active',mode==='professional');$('#professional-view').setAttribute('aria-hidden',String(mode!=='professional'));$('#simple-view').style.visibility=mode==='professional'?'hidden':'visible';if(mode==='professional'&&!state.bridgeTemporary&&selectedCard()?.type==='workflow'&&state.bridgeActive!==state.selected)await loadCardIntoEngine(selectedCard());}
async function startEngine({automatic=false}={}) {
  if(!automatic&&state.serviceConnected===false){
    try{if(window.top===window&&window.chrome?.webview?.postMessage){window.chrome.webview.postMessage({type:'service',action:'recover'});toast('正在恢复画布服务，请稍候');}else toast('请重新打开桌面的映流以恢复画布服务',7000);}
    catch{toast('请重新打开桌面的映流以恢复画布服务',7000);}
    return;
  }
  if(engineStartPending)return;engineStartPending=true;
  if(!automatic){const url=$('#engine-url').value.trim();try{await api('/api/settings',{method:'PUT',body:canvasJSON.stringify({comfy_url:url})});state.engineUrl=url;}catch(error){engineStartPending=false;toast(`连接地址未能保存：${error.message}`,7000);return;}if(state.connected){await refreshBridge();if(state.dirty)await saveProject(false);}reloadEngineFrame=true;}
  state.starting=true;$('#reconnect-engine').disabled=true;$('#reconnect-engine').textContent='正在启动…';$('#connection-detail').textContent='正在启动本机 ComfyUI，请稍候。';
  try{const result=await api('/api/comfy/start',{method:'POST',body:canvasJSON.stringify({automatic})});if(result.url?.startsWith('http://'))state.engineUrl=result.url;if(!automatic)toast(result.status==='ready'?'本机引擎已就绪':result.status==='manual'?result.message:'正在启动本机引擎，就绪后会自动连接');}
  catch(error){state.starting=false;$('#reconnect-engine').disabled=false;toast(`启动失败：${error.message}`,7000);}
  finally{engineStartPending=false;await refreshStatus();}
}
function setLibrary(open) {$('#library').classList.toggle('hidden',!open);$('#library-toggle').classList.toggle('active',open);$('#library-toggle').setAttribute('aria-expanded',String(open));storeUi();requestAnimationFrame(drawConnections);}
function exportProject() {state.project.name=$('#project-name').value.trim()||'我的创作';const blob=new Blob([canvasJSON.stringify(state.project,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`${state.project.name.replace(/[\\/:*?"<>|]/g,'_')}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);toast('已导出项目，包含工作流原图和参数');}
async function importFile(file,workflowOnly=false) {
  if(!file||state.importing||state.canvasBusy)return;
  const project=state.project;
  try {
    if(file.size>32*1024*1024)throw new Error('工作流文件超过 32 MB，请先移除内嵌素材。');
    const text=await file.text(),data=canvasJSON.parse(text);if(state.project!==project||state.canvasBusy)return;
    if(!data||typeof data!=='object'||Array.isArray(data))throw new Error('请选择 ComfyUI 工作流或映流画布 JSON。');
    if(workflowOnly&&Array.isArray(data.cards))throw new Error('这是映流画布，请使用顶部的“导入”。');
    if(Array.isArray(data.cards)&&Array.isArray(data.edges)){
      const imported=normalizeProject(data);state.importing=true;clearTimeout(saveTimer);clearTimeout(bridgeTimer);
      // Import into a separate document so a blocked download cannot destroy the old canvas.
      if(!state.canvasesAvailable)throw new Error('请重新打开新版映流，再将画布导入为独立画布。');
      if(pendingBridgeWrite)await pendingBridgeWrite;
      if(pendingSave)await pendingSave;
      if(!await saveProject(false,true))throw new Error('当前画布未能保存到本机，请重试保存后再导入');
      clearTimeout(saveTimer);
      const created=await api('/api/canvases',{method:'POST',body:canvasJSON.stringify({name:String(imported.name||file.name.replace(/\.json$/i,'')||'导入画布').slice(0,80)})});
      const target=created.project||created;
      const saved=await api(`/api/canvases/${encodeURIComponent(target.id)}`,{method:'PUT',body:canvasJSON.stringify({...imported,id:target.id,name:target.name,revision:target.revision,importMetadata:{...imported.importMetadata,sourceRevision:imported.revision??null}})});
      updateCanvasItem(saved);renderCanvasTabs();
      await api('/api/canvases/active',{method:'PUT',body:canvasJSON.stringify({id:saved.id})});
      activateCanvas(saved);toast('已导入为独立画布，原画布继续保留');
    }else{
      state.importing=true;const result=await api('/api/import-workflow',{method:'POST',body:text,headers:{'X-Workflow-Name':encodeURIComponent(file.name.replace(/\.json$/i,'')||'导入工作流')}});if(result.id){const catalog=await api('/api/catalog');state.catalog=catalog.items||catalog;renderCatalog();if(state.project===project&&!state.canvasBusy)await addWorkflow(result.id);}else throw new Error('导入后没有返回工作流编号');const notice=$('#workflow-import-status');if(notice){notice.hidden=false;notice.textContent=[result.duplicate?'工作流已存在，已从工作流库添加。':'已保留完整工作流。',...(result.warnings||[])].join(' ');}toast(result.duplicate?'已添加已有工作流':'工作流已导入，所需模型与节点可在详细设置中检查');
    }
  }catch(error){toast(`无法导入：${error.message}`,6000);}finally{state.importing=false;}
}
function mediaAction(event) {
  const unlink=event.target.closest('[data-unlink-source]');if(unlink){disconnectSource(unlink.dataset.unlinkSource);return true;}
  const archive=event.target.closest('[data-archive]');if(archive){archiveCard(archive.dataset.archive);return true;}
  return false;
}
function bindNodeMenu(viewport) {
  const menu=$('#node-menu');if(!menu)return;let point;
  viewport.addEventListener('dblclick',event=>{
    if(event.target.closest('.card,button,input,textarea,select,video,a,[data-edge],summary'))return;
    event.preventDefault();point=worldPoint(event.clientX,event.clientY);menu.hidden=false;
    menu.style.left=`${Math.max(8,Math.min(event.clientX,window.innerWidth-menu.offsetWidth-8))}px`;
    menu.style.top=`${Math.max(8,Math.min(event.clientY,window.innerHeight-menu.offsetHeight-8))}px`;
  });
  menu.addEventListener('click',event=>{const action=event.target.closest('[data-create-node]');if(!action)return;menu.hidden=true;const kind=action.dataset.createNode;if(kind==='join')addJoinNode(point);else if(kind==='media'){state.uploadReference=null;state.uploadPosition=point;$('#media-input').click();}else quickWorkflow(kind,point);});
  document.addEventListener('pointerdown',event=>{if(!event.target.closest('#node-menu'))menu.hidden=true;});
}
function bindMediaEvents() {
  const right=$('#right-panel');
  const toggleRight=()=>setRightOpen(right.classList.contains('is-collapsed'));
  $('#right-toggle').onclick=toggleRight;$('#right-compact-toggle').onclick=toggleRight;
  $$('[data-right-tab]').forEach(button=>button.onclick=()=>setRightTab(button.dataset.rightTab));
  $('#media-search').oninput=event=>{state.mediaSearch=event.target.value;renderMedia();};
  $('#media-refresh').onclick=refreshMedia;
  $('#media-upload').onclick=()=>{state.uploadReference=null;$('#media-input').click();};
  $('#media-panel').addEventListener('click',event=>{
    const filter=event.target.closest('[data-media-filter]');if(filter){state.mediaFilter=filter.dataset.mediaFilter;renderMedia();return;}
  });
  $('#media-list').addEventListener('dragstart',event=>{const item=event.target.closest('[data-media-key]');if(!item||!mediaByKey(item.dataset.mediaKey))return;draggedMediaKey=item.dataset.mediaKey;event.dataTransfer.setData(MEDIA_DRAG,draggedMediaKey);event.dataTransfer.effectAllowed='copy';});
  $('#media-list').addEventListener('dragend',()=>{draggedMediaKey=null;clearMediaDrop();});
  right.addEventListener('dragover',event=>{
    if(event.dataTransfer.types.includes(MEDIA_DRAG)){
      event.preventDefault();event.dataTransfer.dropEffect='none';
    }else if(event.dataTransfer.types.includes('Files')){event.preventDefault();event.dataTransfer.dropEffect='copy';}
  });
  right.addEventListener('dragleave',event=>{if(!right.contains(event.relatedTarget))clearMediaDrop();});
  right.addEventListener('drop',event=>{
    if(event.dataTransfer.types.includes(MEDIA_DRAG)){event.preventDefault();draggedMediaKey=null;clearMediaDrop();}
    else if(event.dataTransfer.types.includes('Files')){event.preventDefault();uploadFiles(event.dataTransfer.files);}
  });
}
function bindEvents() {
  window.CanvasSkins?.init();
  bindGlassLighting();
  bindMediaEvents();
  bindPromptOptimizer();
  bindCanvasTabs();
  $('#import-workflow').onclick=()=>$('#workflow-input').click();$('#workflow-input').onchange=event=>{importFile(event.target.files[0],true);event.target.value='';};
  $('#project-name').addEventListener('input',()=>{state.project.name=$('#project-name').value;projectChanged();});$('#save-project').onclick=()=>saveProject();$('#export-project').onclick=exportProject;$('#import-project').onclick=()=>$('#project-input').click();$('#project-input').onchange=e=>{importFile(e.target.files[0]);e.target.value='';};
  $('#library-toggle').onclick=()=>setLibrary($('#library').classList.contains('hidden'));$('#library-close').onclick=()=>setLibrary(false);$('#new-workflow').onclick=()=>{setLibrary(true);$('#workflow-search').focus();};$('#workflow-search').oninput=e=>{state.search=e.target.value;renderCatalog();};
  $('#category-tabs').onclick=e=>{const b=e.target.closest('[data-category]');if(b){state.category=b.dataset.category;renderCatalog();}};$('#workflow-list').onclick=e=>{const b=e.target.closest('[data-workflow]');if(b)addWorkflow(b.dataset.workflow);};
  $$('[data-quick]').forEach(b=>b.onclick=()=>quickWorkflow(b.dataset.quick));$('#upload-media').onclick=()=>{state.uploadReference=null;$('#media-input').click();};$('#media-input').onchange=e=>{uploadFiles(e.target.files,null,state.uploadReference);e.target.value='';state.uploadReference=null;};
  $('#help-toggle').onclick=()=>$('#help-dialog').showModal();$('#connection').onclick=()=>$('#connection-dialog').showModal();$('#reconnect-engine').onclick=startEngine;
  $$('[data-mode]').forEach(b=>b.onclick=()=>setMode(b.dataset.mode));$('#send-to-engine').onclick=()=>loadCardIntoEngine(selectedCard());$('#open-engine').onclick=()=>window.open(state.engineUrl,'_blank','noopener');
  $('#zoom-in').onclick=()=>setZoom(state.project.viewport.zoom*1.15);$('#zoom-out').onclick=()=>setZoom(state.project.viewport.zoom/1.15);$('#zoom-value').onclick=()=>setZoom(1);$('#fit-view').onclick=fitView;
  $('#arrange-canvas').onclick=arrangeCanvas;$('#undo-arrange').onclick=undoArrange;
  const history=$('#history-panel'),historyToggle=$('#history-toggle');
  if(typeof history.showPopover==='function')history.addEventListener('toggle',event=>historyToggle.setAttribute('aria-expanded',String(event.newState==='open')));
  else {history.hidden=true;historyToggle.onclick=()=>{history.hidden=!history.hidden;historyToggle.setAttribute('aria-expanded',String(!history.hidden));};document.addEventListener('pointerdown',event=>{if(!history.contains(event.target)&&!historyToggle.contains(event.target)){history.hidden=true;historyToggle.setAttribute('aria-expanded','false');}});}
  $('#task-list').onclick=async e=>{const el=e.target.closest('[data-task-card]');if(!el||state.canvasBusy)return;if(el.dataset.taskCanvas&&el.dataset.taskCanvas!==state.project.id&&!await changeCanvas(el.dataset.taskCanvas))return;const card=cardById(el.dataset.taskCard);if(card){selectCard(card.id);const r=$('#viewport').getBoundingClientRect();state.project.viewport.x=r.width/2-card.x*state.project.viewport.zoom-147*state.project.viewport.zoom;state.project.viewport.y=r.height/2-card.y*state.project.viewport.zoom-120*state.project.viewport.zoom;applyViewport();projectChanged();}};
  $('#cards').addEventListener('input',updateJoinRange);
  $('#cards').addEventListener('input',event=>{if(event.target.dataset.field)updateField(event.target);});
  $('#cards').addEventListener('change',event=>{if(event.target.dataset.continuationSelect){if(!setContinuation(event.target.dataset.continuationSelect,event.target.value))toast('无法选择这段视频，请重试。');renderInspector();}});
  $('#cards').addEventListener('focusout',event=>{if(event.target.dataset.field)requestAnimationFrame(()=>{if(!editingField()){renderCards();renderInspector();}});});
  $('#cards').addEventListener('toggle',drawConnections,true);
  $('#cards').addEventListener('click',e=>{const card=e.target.closest('.card'),details=e.target.closest('[data-details],.card-more'),optimize=e.target.closest('[data-optimize-prompt]'),undo=e.target.closest('[data-undo-prompt]');if(optimize||undo){e.preventDefault();if(optimize)optimizePrompt(optimize.dataset.optimizePrompt);else undoPromptOptimization(undo.dataset.undoPrompt);return;}if(details&&card){selectCard(card.dataset.id);setRightTab('settings');return;}if(joinNodeAction(e)||mediaAction(e))return;const run=e.target.closest('[data-run]');if(run){runCard(run.dataset.run);return;}if(card)selectCard(card.dataset.id);});
  $('#inspector').addEventListener('input',e=>{if(e.target.dataset.field)updateField(e.target);if(e.target.dataset.rename){const card=selectedCard();if(card){card.name=e.target.value;renderSingleCard(card.id);projectChanged();}}});
  $('#inspector').addEventListener('change',e=>{if(e.target.dataset.field)updateField(e.target);if(e.target.dataset.continuationSelect){if(!setContinuation(e.target.dataset.continuationSelect,e.target.value))toast('无法选择这段视频，请重试。');renderSingleCard(e.target.dataset.continuationSelect);renderInspector();}});
  $('#inspector').addEventListener('focusout',event=>{if(event.target.dataset.field)requestAnimationFrame(()=>{if(!editingField())renderCards();});});
  $('#inspector').addEventListener('click',e=>{const optimize=e.target.closest('[data-optimize-prompt]'),undo=e.target.closest('[data-undo-prompt]');if(optimize||undo){e.preventDefault();if(optimize)optimizePrompt(optimize.dataset.optimizePrompt);else undoPromptOptimization(undo.dataset.undoPrompt);return;}if(joinNodeAction(e)||mediaAction(e))return;const run=e.target.closest('[data-run]');if(run){runCard(run.dataset.run);return;}const del=e.target.closest('[data-delete]');if(del){removeCard(del.dataset.delete);return;}const unlink=e.target.closest('[data-unlink]');if(unlink){state.project.edges=state.project.edges.filter(x=>!(x.from===unlink.dataset.unlink&&x.to===state.selected));renderCards();renderInspector();projectChanged();return;}if(e.target.closest('#add-reference')){state.uploadReference=state.selected;$('#media-input').click();}if(e.target.closest('#inspect-professional'))setMode('professional');if(e.target.closest('[data-check-workflow]'))checkWorkflow(state.selected);});
  $('#inspector').addEventListener('click',e=>{const slot=e.target.closest('[data-reference-upload]');if(slot){state.uploadReference=state.selected;state.uploadFieldId=slot.dataset.referenceUpload;const field=fieldsOf(selectedCard()).find(f=>f.id===state.uploadFieldId);$('#media-input').accept=field?.type==='video'?'video/*':'image/*';$('#media-input').click();}});
  $('#media-input').onchange=e=>{uploadFiles(e.target.files,state.uploadPosition,state.uploadReference,state.uploadFieldId);e.target.value='';state.uploadPosition=null;state.uploadReference=null;state.uploadFieldId=null;$('#media-input').accept='image/*,video/*';};
  const viewport=$('#viewport');
  bindNodeMenu(viewport);
  viewport.addEventListener('pointerdown',e=>{
    if(e.button!==0&&e.button!==1)return;const cardEl=e.target.closest('.card'),port=e.target.closest('[data-port]');
    if(port&&port.dataset.port==='output'){const from=cardEl.dataset.id;state.connecting={from,point:worldPoint(e.clientX,e.clientY)};port.classList.add('connecting');viewport.setPointerCapture(e.pointerId);e.preventDefault();return;}
    if(e.target.closest('button,input,textarea,select,video,a,summary,label,.card-quick'))return;
    if(cardEl&&!state.space&&e.button===0){selectCard(cardEl.dataset.id);const card=cardById(cardEl.dataset.id);state.drag={type:'card',id:card.id,startX:e.clientX,startY:e.clientY,x:card.x,y:card.y};$(`.card[data-id="${card.id}"]`)?.classList.add('dragging');}
    else {if(!state.space&&e.button===0&&e.target.closest('[data-edge]'))return;state.drag={type:'pan',startX:e.clientX,startY:e.clientY,x:state.project.viewport.x,y:state.project.viewport.y};viewport.classList.add('panning');if(!state.space&&e.button===0)selectCard(null);}
    viewport.setPointerCapture(e.pointerId);e.preventDefault();
  });
  viewport.addEventListener('pointermove',e=>{if(state.connecting){state.connecting.point=worldPoint(e.clientX,e.clientY);drawConnections();return;}const d=state.drag;if(!d)return;const dx=e.clientX-d.startX,dy=e.clientY-d.startY;if(Math.abs(dx)+Math.abs(dy)>4)d.moved=true;if(d.type==='card'){const card=cardById(d.id);card.x=Math.round(d.x+dx/state.project.viewport.zoom);card.y=Math.round(d.y+dy/state.project.viewport.zoom);const el=$(`.card[data-id="${d.id}"]`);el.style.left=`${card.x}px`;el.style.top=`${card.y}px`;highlightMediaDrop(document.elementFromPoint(e.clientX,e.clientY),card);drawConnections();}else{state.project.viewport.x=d.x+dx;state.project.viewport.y=d.y+dy;applyViewport();}});
  const endPointer=e=>{if(state.connecting){const target=document.elementFromPoint(e.clientX,e.clientY)?.closest('.card');if(target)connectCards(state.connecting.from,target.dataset.id);state.connecting=null;$$('.port.connecting').forEach(el=>el.classList.remove('connecting'));drawConnections();}if(state.drag){const drag=state.drag;if(e.type!=='pointercancel'&&drag.type==='card'){dropCanvasCardOnRight(drag,document.elementFromPoint(e.clientX,e.clientY));if(!drag.moved)focusNode(drag.id);}$$('.card.dragging').forEach(el=>el.classList.remove('dragging'));state.drag=null;viewport.classList.remove('panning');clearMediaDrop();projectChanged();}if(viewport.hasPointerCapture(e.pointerId))viewport.releasePointerCapture(e.pointerId);};
  viewport.addEventListener('pointerup',endPointer);viewport.addEventListener('pointercancel',endPointer);viewport.addEventListener('wheel',e=>{if(e.target.closest('textarea,select,video,.card-quick'))return;e.preventDefault();setZoom(state.project.viewport.zoom*Math.exp(-e.deltaY*.0013),{x:e.clientX,y:e.clientY});},{passive:false});
  $('#connections').onclick=e=>{const edge=e.target.closest('[data-edge]');if(edge)disconnectEdge(edge.dataset.edge);};
  let dragDepth=0;const acceptsDrop=e=>e.dataTransfer.types.includes('Files')||e.dataTransfer.types.includes(MEDIA_DRAG);viewport.addEventListener('dragenter',e=>{if(acceptsDrop(e)){e.preventDefault();dragDepth++;$('#drop-hint').classList.add('visible');}});viewport.addEventListener('dragover',e=>{if(acceptsDrop(e)){e.preventDefault();e.dataTransfer.dropEffect='copy';}});viewport.addEventListener('dragleave',()=>{if(--dragDepth<=0){dragDepth=0;$('#drop-hint').classList.remove('visible');}});viewport.addEventListener('drop',e=>{if(!acceptsDrop(e))return;e.preventDefault();dragDepth=0;$('#drop-hint').classList.remove('visible');if(e.dataTransfer.types.includes(MEDIA_DRAG)){const key=e.dataTransfer.getData(MEDIA_DRAG),point=worldPoint(e.clientX,e.clientY),target=e.target.closest('.join-card');if(target){dropMediaOnJoin(key,target,point);return;}addMediaToCanvas(key,point);return;}uploadFiles(e.dataTransfer.files,worldPoint(e.clientX,e.clientY));});
  window.addEventListener('keydown',e=>{const editing=/INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName)||document.activeElement?.isContentEditable;if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='s'){e.preventDefault();saveProject();return;}if(editing||$('dialog[open]'))return;if(e.code==='Space'){e.preventDefault();state.space=true;viewport.classList.add('space-pan');}if((e.key==='Delete'||e.key==='Backspace')&&state.selected){e.preventDefault();removeCard(state.selected);}if(e.key==='Escape'){$('#node-menu')?.setAttribute('hidden','');state.connecting=null;drawConnections();selectCard(null);}if(e.key==='0'&&(e.ctrlKey||e.metaKey)){e.preventDefault();fitView();}});window.addEventListener('keyup',e=>{if(e.code==='Space'){state.space=false;viewport.classList.remove('space-pan');}});window.addEventListener('blur',()=>{state.space=false;state.drag=null;viewport.classList.remove('space-pan','panning');});
  window.addEventListener('beforeunload',safeStorageSave);window.addEventListener('resize',drawConnections);
}
function bindGlassLighting() {
  if(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)return;
  const selector='button,.topbar,.library,.inspector,.history-panel,.card,dialog,.zoom-controls';
  let frame=0,active=null,point=null;
  document.addEventListener('pointermove',event=>{
    if(event.pointerType==='touch'||event.buttons)return;
    point={x:event.clientX,y:event.clientY,target:event.target};
    if(frame)return;
    frame=requestAnimationFrame(()=>{
      frame=0;const target=point.target.closest?.(selector);
      if(active!==target){active?.classList.remove('glass-lit');active=target;}
      if(!target||target.disabled)return;
      const box=target.getBoundingClientRect();if(!box.width||!box.height)return;
      const x=Math.max(0,Math.min(100,(point.x-box.left)/box.width*100));
      const y=Math.max(0,Math.min(100,(point.y-box.top)/box.height*100));
      target.style.setProperty('--light-x',`${x.toFixed(1)}%`);target.style.setProperty('--light-y',`${y.toFixed(1)}%`);
      target.style.setProperty('--light-angle',`${Math.atan2(y-50,x-50)*180/Math.PI+180}deg`);
      target.classList.add('glass-lit');
    });
  },{passive:true});
  document.addEventListener('pointerleave',()=>{active?.classList.remove('glass-lit');active=null;},{passive:true});
}
async function boot() {
  bindEvents();
  restoreUi();loadRecoveries();setCanvasBusy(true);
  const results=await Promise.allSettled([api('/api/catalog'),api('/api/project'),api('/api/settings'),api('/api/canvases')]);
  if(results[0].status==='fulfilled'){const data=results[0].value;state.catalog=Array.isArray(data)?data:(data.items||[]);}else $('#workflow-list').innerHTML='<div class="list-empty">工作流库暂时无法读取。请检查本地服务后刷新。</div>';
  const index=results[3].status==='fulfilled'?results[3].value:null;
  state.canvasesAvailable=!!(index&&Array.isArray(index.items)&&index.active_id);
  if(state.canvasesAvailable)state.canvases=index.items;
  let project=results[1].status==='fulfilled'?results[1].value:null;if(project?.project)project=project.project;
  const activeId=index?.active_id||project?.id;
  if(state.canvasesAvailable&&project?.id!==activeId){try{project=await api(`/api/canvases/${encodeURIComponent(activeId)}`);project=project.project||project;}catch{project=null;}}
  let recovered=false;
  if(!project?.cards||!project?.edges){
    try {const id=activeId||localStorage.getItem(ACTIVE_CANVAS_KEY),local=canvasJSON.parse(localStorage.getItem(id?`${LOCAL_KEY}:${id}`:LOCAL_KEY));if(local?.cards&&local.edges&&(!id||local.id===id)){project=local;recovered=true;}}catch(error){console.warn('本地草稿无效',error);}
  }
  if(!project?.cards||!project?.edges)throw new Error('未能读取本机画布，请重新打开软件或刷新；已有内容保留在本机。');
  if(results[2].status==='fulfilled'){const configured=results[2].value.comfy_url||results[2].value.comfyUrl;if(configured&&configured!=='auto')state.engineUrl=configured;}
  const resolved=recovered?{project,dirty:true}:resolveProjectDraft(project);
  activateCanvas(resolved.project,{dirty:resolved.dirty});renderCatalog();state.booted=true;setCanvasBusy(false);
  notifyStartup('ready');
  state.project.cards.filter(isWorkflow).forEach(c=>fieldsOf(c).filter(f=>f.editable===false).forEach(f=>delete c.values?.[f.id]));
  if(recovered)setSaveStatus('已恢复此画布的浏览器备份，请保存');
  if(!project.legacyJoinMigrated&&state.project.legacyJoinMigrated)projectChanged();
  if(!state.canvasesAvailable)toast('当前后台需更新；重新打开桌面上的映流后，即可使用多画布。',6500);
  await refreshMedia();await Promise.allSettled([refreshStatus(),refreshJobs()]);await startEngine({automatic:true});await pollConnection();
}
window.CanvasApp={state,changeCanvas,addWorkflow,selectCard,runCard,connectCards,saveProject,fitView,refreshJobs,refreshStatus,refreshBridge,refreshMedia,addMediaToCanvas,archiveCard,disconnectSource,disconnectEdge,setRightTab,setRightOpen,arrangeCanvas,undoArrange,addJoinNode,submitJoin,optimizePrompt,applyPromptOptimization,cancelPromptOptimization,undoPromptOptimization};
boot().catch(error=>{console.error(error);notifyStartup('error',error.message);toast(`画布载入失败：${error.message}`,10000);});
