const S={templates:[],videos:[],images:[],models:[],mode:'t2v',refs:[],first:null,last:null,curOutput:null};
const STAGES=[
  {k:'queue',label:'排队',base:2},
  {k:'load',label:'加载模型',base:7},
  {k:'encode',label:'编码提示词',base:15},
  {k:'sample',label:'扩散采样',base:22},
  {k:'decode',label:'VAE 解码',base:83},
  {k:'save',label:'封装保存',base:94}
];
const T={
  jobId:null,job:null,output:null,events:[],stage:0,stageKey:'idle',pct:0,hasPct:false,detail:'',
  sample:null,lastLogPct:0,startedAt:0,elapsed:0,sampleStart:0,lastStatus:'',
  ws:null,wsUrl:'',wsReady:false,wstried:0,reconnectTimer:null,pollTimer:null,wsFails:0,
  demo:false,demoTimers:[],demoInterval:null,autoScroll:true,jobs:[],filter:'all',
  renderQueued:false,lastLogKey:'',logStore:{}
};
const FRIENDLY={minimax_h3_t2v:'文生视频',minimax_h3_fl2v:'首尾帧',minimax_h3_r2v:'参考图',qwen_image:'Qwen 文生图',qwen_image_edit:'Qwen 图像编辑',minimax_h3_image:'H3 静帧'};
const NODE_STAGE={'1':'load','2':'load','3':'load','4':'load','10':'encode','9':'sample','11':'decode','12':'decode','13':'save','14':'save'};
const NODE_NAME={'1':'加载 UNet','2':'加载文本编码器','3':'加载视频 VAE','4':'加载音频 VAE','10':'编码提示词与潜变量','9':'扩散采样','11':'视频 VAE 解码','12':'音频 VAE 解码','13':'封装音视频','14':'保存视频'};

const $=id=>document.getElementById(id);
const $$=(sel,root)=>Array.from((root||document).querySelectorAll(sel));
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
function pad(n){return String(n).padStart(2,'0')}
function fmtClock(s){s=Math.max(0,Math.floor(s||0));const h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;return h?h+':'+pad(m)+':'+pad(x):pad(m)+':'+pad(x)}
function parseTime(v){if(!v)return 0;const t=Date.parse(v);return isNaN(t)?0:t}
function fmtDate(v){const t=parseTime(v);if(!t)return '--';const d=new Date(t);return pad(d.getMonth()+1)+'-'+pad(d.getDate())+' '+pad(d.getHours())+':'+pad(d.getMinutes())}
function toast(m){const t=$('toast');t.textContent=m;t.classList.add('on');clearTimeout(t._h);t._h=setTimeout(()=>t.classList.remove('on'),2800)}
async function api(path,opts){const r=await fetch(path,opts);if(!r.ok){let d='';try{d=await r.text()}catch(e){}throw new Error(d||('HTTP '+r.status))}return r.json()}
function fmtMB(mb){return mb>=1024?(mb/1024).toFixed(1)+' GB':Math.round(mb)+' MB'}
function badge(status){const cls=status==='completed'?'ok':(status==='failed'||status==='cancelled'||status==='aborted')?'fail':(status==='running'||status==='in_progress')?'run':'wait';return '<span class="badge '+cls+'">'+esc(status)+'</span>'}
function pickRes(){
  const v=$('resPreset').value;
  if(v==='custom'){return [parseInt($('cw').value)||608,parseInt($('ch').value)||352]}
  const [w,h]=v.split('x').map(Number);return [w,h];
}
$('resPreset').onchange=()=>$('customRes').classList.toggle('hidden',$('resPreset').value!=='custom');

/* ---------- 硬件监控（2s） ---------- */
async function tickHw(){
  try{
    const h=await api('/api/hardware');
    const g=h.gpu||{};const m=h.memory||{};
    if(g.error){$('stText').textContent='GPU 读取失败';$('stDot').classList.add('bad')}
    else{$('stText').textContent='服务正常';$('stDot').classList.remove('bad');
      $('gUtil').textContent=g.util_pct;$('gVram').textContent=(g.vram_used_mb/1024).toFixed(1)+'/'+(g.vram_total_mb/1024).toFixed(1);
      $('gTemp').textContent=Math.round(g.temp_c);$('gPower').textContent=Math.round(g.power_w);
      $('hpName').textContent=g.name;$('hpVram').textContent=g.vram_used_mb+' / '+g.vram_total_mb+' MB';
      $('hpUtil').textContent=g.util_pct+'%';$('hpTP').textContent=Math.round(g.temp_c)+'°C / '+Math.round(g.power_w)+'W';
      $('hpBar').style.width=Math.min(100,g.vram_used_mb/g.vram_total_mb*100).toFixed(1)+'%';}
    $('gRam').textContent=(m.ram_free_gb??'--');$('hpRam').textContent=(m.ram_free_gb??'--')+' / '+(m.ram_total_gb??'--')+' GB';
    $('hpCommit').textContent=(m.commit_used_gb??'--')+' / '+(m.commit_total_gb??'--')+' GB';
    $('hpQueue').textContent=(h.queue?.running??0)+' / '+(h.queue?.pending??0);
    $('hpTime').textContent=h.time||'--';
    $('tQueue').textContent=(h.queue?.running??0)+' / '+(h.queue?.pending??0);
  }catch(e){
    $('stText').textContent='服务断开';$('stDot').classList.add('bad');
  }
}

/* ---------- 初始化 ---------- */
async function bootstrap(){
  try{const t=await api('/api/templates');S.templates=t.templates||[]}catch(e){}
  try{const v=await api('/api/videos');S.videos=v.videos||[]}catch(e){}
  try{const m=await api('/api/upscale/models');S.models=m.models||[]}catch(e){}
  try{const md=await api('/api/models');fillModelSelects(md)}catch(e){}
  fillSrcSelects();renderGallery();renderRecent();renderChips();
  loadSettings();
  loadProjects();
  applyLang(localStorage.getItem('asterLang')||'zh');
  await initRealtime();
  await loadTasks();
  await restoreActive();
}
function fillModelSelects(md){
  const put=(id,list)=>{const sel=$(id);if(!sel)return;sel.innerHTML='<option value="">自动（模板默认）</option>';for(const n of (list||[])){const o=document.createElement('option');o.value=n;o.textContent=n;sel.appendChild(o)}};
  put('unetSel',md.unet);put('clipSel',md.clip);put('vaeSel',md.vae);
}
function fillSrcSelects(){
  for(const id of ['uSrc','eSrc']){
    const sel=$(id);const cur=sel.value;sel.innerHTML='';
    for(const v of S.videos){const o=document.createElement('option');o.value=v.path;o.textContent=v.name+' · '+fmtMB(v.mb);sel.appendChild(o)}
    if(cur)sel.value=cur;
  }
  const um=$('uModel');um.innerHTML='';
  for(const m of S.models){const o=document.createElement('option');o.value=m;o.textContent=m;um.appendChild(o)}
  const pref=S.models.find(x=>x.includes('anime_6B'));if(pref)um.value=pref;
}
function renderChips(){
  const chips=['新海诚风格黄昏天台，积雨云与夕阳光束，白裙少女倚栏远望，镜头缓慢推进',
    '夜空银河与城市灯海，少女伸手接住发光花瓣，镜头缓缓拉远升空',
    '雨夜车窗，霓虹倒影，列车驶过，钢琴与雨声',
    '夏日海边，积雨云，蝉鸣，阳光透过云隙'];
  $('chips').innerHTML=chips.map((c,i)=>'<span class="chip" data-i="'+i+'">'+
    ['黄昏天台','星空灯海','雨夜列车','夏日海边'][i]+'</span>').join('');
  $('chips').querySelectorAll('.chip').forEach(el=>{el.onclick=()=>{$('prompt').value=chips[+el.dataset.i]}});
}

/* ---------- Tab ---------- */
function setTab(name){
  $$('.side .item').forEach(el=>el.classList.toggle('on',el.dataset.tab===name));
  $$('.view').forEach(el=>el.classList.toggle('on',el.id==='view-'+name));
  if(name==='gallery')renderGallery();
  if(name==='tasks'){renderLive();renderLogs();loadTasks()}
  if(name==='upscale')refreshUpscaleJobs();
  if(name==='edit')buildEditForm();
  if(name==='image'){initImagePage()}
  if(name==='film'){pollFilm()}
  if(name==='settings')loadSettings();
  if(name==='models')loadModels();
  if(name==='projects')loadProjects();
  if(name==='canvas'){const f=$('comfyCanvas');if(f&&!f.dataset.loaded){f.src='http://127.0.0.1:8188/';f.dataset.loaded='1'}}
  window.scrollTo({top:0,behavior:'smooth'});
}
$$('.side .item').forEach(el=>el.onclick=()=>setTab(el.dataset.tab));
let PROJECTS=[],PRESETS=[];
async function loadProjects(){
  try{const r=await api('/api/projects');PROJECTS=r.projects||[];renderProjects()}catch(e){}
  try{const r=await api('/api/presets');PRESETS=r.presets||[];renderPresets()}catch(e){}
}
function renderProjects(){
  const box=$('projectList');if(!box)return;
  box.innerHTML=PROJECTS.length?PROJECTS.map(p=>'<div style="padding:8px 0;border-top:1px solid var(--line)"><b>'+esc(p.name)+'</b><div class="muted" style="font-size:11px">'+esc(p.description||'')+' · '+(p.items||[]).length+' 项</div></div>').join(''):'<div class="muted">暂无项目</div>';
}
function renderPresets(){
  const box=$('presetList'),sel=$('presetSel');
  if(box)box.innerHTML=PRESETS.map(p=>'<div style="padding:6px 0;border-top:1px solid var(--line);font-size:12px"><b>'+esc(p.name)+'</b><div class="muted">'+esc((p.prompt||'').slice(0,80))+'</div></div>').join('')||'<div class="muted">暂无预设</div>';
  if(sel){const cur=sel.value;sel.innerHTML='<option value="">不使用预设</option>'+PRESETS.map(p=>'<option value="'+esc(p.id)+'">'+esc(p.name)+'</option>').join('');sel.value=cur}
}
if($('projCreate'))$('projCreate').onclick=async()=>{
  const name=$('projName').value.trim();if(!name){$('projMsg').textContent='请输入项目名称';return}
  try{await api('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,description:$('projDesc').value})});$('projMsg').textContent='已创建';$('projName').value='';$('projDesc').value='';loadProjects()}catch(e){$('projMsg').textContent='失败：'+e.message}
};
if($('presetCreate'))$('presetCreate').onclick=async()=>{
  const name=$('presetName').value.trim(),prompt=$('presetPrompt').value.trim();
  if(!name||!prompt)return;
  try{await api('/api/presets',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,prompt})});$('presetName').value='';$('presetPrompt').value='';loadProjects();toast('预设已保存')}catch(e){}
};
if($('presetSel'))$('presetSel').onchange=()=>{
  const p=PRESETS.find(x=>x.id===$('presetSel').value);if(!p)return;
  const ta=$('prompt');ta.value=(ta.value?ta.value+' ':'')+p.prompt;ta.focus();
};

let MODELS=[],MODEL_SOURCE='local';
async function loadModels(){
  const grid=$('modelGrid');if(!grid)return;
  grid.innerHTML='<div class="muted">加载中…</div>';
  try{
    if($('modelSource'))MODEL_SOURCE=$('modelSource').value;
    if(MODEL_SOURCE==='market'){
      const r=await api('/api/models/market');MODELS=r.models||[];
      if($('modelMsg'))$('modelMsg').textContent=(r.count||0)+' 个模型'+(r.stale?'（缓存）':'');
    }else{
      const s=await api('/api/setup/status');MODELS=s.models||[];
      if($('modelMsg'))$('modelMsg').textContent=MODELS.length+' 个本地条目';
    }
    renderModels();
  }catch(e){grid.innerHTML='<div class="muted">加载失败：'+esc(e.message)+'</div>'}
}
function renderModels(){
  const grid=$('modelGrid');if(!grid)return;
  const q=($('modelSearch').value||'').toLowerCase();
  const f=$('modelFilter').value;
  const list=MODELS.filter(m=>{
    if(f==='installed'&&!m.installed)return false;
    const kind=(m.kind||m.type||'').toLowerCase();
    if(f==='video'&&!(kind.includes('video')||kind.includes('h3')))return false;
    if(f==='image'&&!(kind.includes('image')||kind.includes('qwen')||kind.includes('moss')))return false;
    return !q||(m.name+' '+m.target+' '+m.id+' '+(m.base||'')).toLowerCase().includes(q);
  });
  grid.innerHTML=list.map(m=>'<div class="card" style="padding:12px">'+
    '<div style="display:flex;justify-content:space-between;gap:8px"><b>'+esc(m.name)+'</b>'+
    '<span class="badge '+(m.installed?'ok':'wait')+'">'+(m.installed?'已安装':'未安装')+'</span></div>'+
    '<div class="muted" style="font-size:12px;margin-top:6px">'+esc(m.kind||m.type||'')+' · '+esc(m.target||m.save_path||'')+' · '+esc(String(m.size_gb||m.size||'?'))+'</div>'+
    (m.base?'<div class="muted" style="font-size:11px">基础模型：'+esc(m.base)+'</div>':'')+
    '<div class="muted" style="font-size:11px;margin-top:4px">许可：'+esc(m.license||'见上游')+'</div>'+
    '<div class="row" style="margin-top:8px;gap:6px">'+
    (m.license_url||m.reference?'<a class="btn sm ghost" target="_blank" rel="noopener" href="'+esc(m.license_url||m.reference)+'">许可/来源</a>':'')+
    (!m.installed?(m.source==='comfyui-manager'
      ?'<button class="btn sm" onclick="downloadMarket(\''+esc(m.id)+'\')">下载</button>'
      :'<button class="btn sm" onclick="downloadModel(\''+esc(m.id)+'\')">下载</button>'):'')+
    '</div></div>').join('')||'<div class="muted">没有匹配的模型</div>';
}
async function downloadModel(id){
  $('modelMsg').textContent='下载已开始：'+id;
  try{await api('/api/setup/download',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids:[id]})});toast('下载任务已提交')}
  catch(e){$('modelMsg').textContent='下载失败：'+e.message}
}
async function downloadMarket(id){
  const m=MODELS.find(x=>x.id===id);if(!m)return;
  $('modelMsg').textContent='下载已开始：'+m.name;
  try{
    const r=await api('/api/models/market/download',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:m.url,save_path:m.save_path,filename:m.filename})});
    if(!r.ok)throw new Error(r.error||'下载失败');
    toast('市场模型下载已开始');
  }catch(e){$('modelMsg').textContent='下载失败：'+e.message}
}
if($('modelSource'))$('modelSource').onchange=loadModels;
$('modelSearch')&&$('modelSearch').addEventListener('input',renderModels);
$('modelFilter')&&$('modelFilter').addEventListener('change',renderModels);
$('modelRefresh')&&($('modelRefresh').onclick=()=>loadModels());

function reloadCanvas(){
  const f=$('comfyCanvas');
  if(!f){toast('画布尚未加载');return}
  f.src='http://127.0.0.1:8188/?t='+Date.now();
  f.dataset.loaded='1';
  toast('画布已刷新');
}
const _initTab=new URLSearchParams(location.search).get('tab');
if(_initTab){setTimeout(()=>{try{setTab(_initTab)}catch(e){}},50)}

/* ---------- 模式与上传 ---------- */
function setMode(m){S.mode=m;$$('.modes .m').forEach(el=>el.classList.toggle('on',el.dataset.mode===m));
  const imgMode=(m==='qwen_image'||m==='qwen_edit'||m==='h3_image');
  const qwenFam=(m==='qwen_image'||m==='qwen_edit');
  $('imgArea').classList.toggle('hidden',!(m==='r2v'||m==='qwen_edit'||SIMPLE));
  $('frameArea').classList.toggle('hidden',m!=='fl2v');
  $('lora').disabled=(m==='r2v'||qwenFam);
  const durWrap=$('dur').closest('div');if(durWrap)durWrap.style.display=imgMode?'none':'';
  const tp=$('twoPass');const tpWrap=tp?tp.closest('.dgrid'):null;if(tpWrap)tpWrap.style.display=imgMode?'none':'';
  $('optimize').disabled=qwenFam;
  if(qwenFam){$('steps').value=25;setRes('1024x1024')}
  else if(m==='h3_image'){$('steps').value=8;setRes('768x768')}
}
$$('.modes .m').forEach(el=>el.onclick=()=>setMode(el.dataset.mode));
/* ---------- 首次启动：提示方式 ---------- */
let SETUP=null;
function renderSetup(s){
  SETUP=s;
  $('setupStep1').classList.add('hidden');
  const p=s.profile||{};
  const gpu=(p.gpu||{}).name||'未检测到 NVIDIA GPU';
  $('setupProfile').innerHTML='CPU '+(p.cpu_count||'?')+' 线程 · 内存 '+(p.ram_gb||'?')+'GB · 显存 '+
    ((p.vram_total_mb||0)/1024).toFixed(1)+'GB<br>GPU：'+gpu+'<br>检测模式：'+(p.detected_mode||p.mode||'?')+
    ' · 当前模式：'+(p.mode||'?');
  $('setupMode').value=p.mode_override||'auto';
  $('setupModels').innerHTML=(s.models||[]).map(m=>{
    const checked=m.installed?'checked':'';
    return '<label style="display:flex;gap:6px;align-items:flex-start;font-size:12px;padding:4px 0">'+
      '<input type="checkbox" data-mid="'+esc(m.id)+'" '+checked+'>'+
      '<span>'+esc(m.name)+'<br><span class="muted" style="font-size:10px">'+
      (m.installed?'已安装':'未安装')+' · '+esc(m.target)+' · '+m.size_gb+'GB</span></span></label>';
  }).join('')||'<div class="muted">模型目录为空</div>';
  $('setupStep2').classList.remove('hidden');
}
async function initSetup(){
  try{
    const s=await api('/api/setup/status');
    if(s.first_run||s.needs_setup){$('modeModal').style.display='flex';
      if(!s.first_run)renderSetup(s);
      return}
    if(s.doctor&&!s.doctor.ok){const first=(s.doctor.issues||[])[0];if(first)toast(first.message)}
  }catch(e){}
}
$$('#modeModal [data-style]').forEach(b=>b.onclick=async()=>{
  const style=b.dataset.style;
  $('modeHint').textContent='保存中…';
  try{
    const r=await api('/api/setup/mode',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({style})});
    if(r.ok){const s=await api('/api/setup/status');renderSetup(s);toast(style==='easy'?'已选择易懂版':'已选择专业版')}
    else $('modeHint').textContent='保存失败：'+(r.error||'未知');
  }catch(e){$('modeHint').textContent='保存失败：'+e.message}
});
$('setupSave').onclick=async()=>{
  const ids=$$('#setupModels input[data-mid]:checked').map(x=>x.dataset.mid);
  const mode=$('setupMode').value;
  $('setupMsg').textContent='保存中…';
  try{
    await api('/api/setup/models',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids})});
    await api('/api/setup/hostmode',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode})});
    $('modeModal').style.display='none';toast('设置已保存');
  }catch(e){$('setupMsg').textContent='保存失败：'+e.message}
};
$('setupDownload').onclick=async()=>{
  const ids=$$('#setupModels input[data-mid]:checked').map(x=>x.dataset.mid);
  if(!ids.length){$('setupMsg').textContent='请先勾选模型';return}
  $('setupMsg').textContent='下载已开始…';
  try{
    await api('/api/setup/download',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids})});
    const timer=setInterval(async()=>{
      try{
        const d=await api('/api/setup/download');
        if(!d.running){clearInterval(timer);const res=d.result||{};const err=(res.errors||[])[0];
          $('setupMsg').textContent=err?('失败：'+(err.error||'')):'下载完成';if(!err)setTimeout(()=>initSetup(),800);}
        else $('setupMsg').textContent='下载中…';
      }catch(e){clearInterval(timer);$('setupMsg').textContent='状态查询失败'}
    },2000);
  }catch(e){$('setupMsg').textContent='下载失败：'+e.message}
};
setTimeout(initSetup,300);

/* ---------- 精简 / 专业 ---------- */
const QMAP={
  fast:{w:608,h:352,steps:4,lora:'minimax_h3\\minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors',profile:'draft'},
  standard:{w:960,h:544,steps:8,lora:'none',profile:'standard'},
  high:{w:1280,h:736,steps:8,lora:'none',profile:'high'}
};
let SIMPLE=localStorage.getItem('studioSimple')!=='0';
function hideBox(id,hide){const el=$(id);if(!el)return;const b=el.closest('div');if(b)b.style.display=hide?'none':'';}
function applySimpleUI(){
  $('simpleRow').classList.toggle('hidden',!SIMPLE);
  ['resPreset','profile','steps','lora','optimize','twoPass','twoPassScale','seed'].forEach(id=>hideBox(id,SIMPLE));
  const det=document.querySelector('#view-create details.details');if(det)det.style.display=SIMPLE?'none':'';
  $('customRes').style.display=(SIMPLE||$('resPreset').value!=='custom')?'none':'';
  $('imgArea').classList.toggle('hidden',!(SIMPLE||S.mode==='r2v'||S.mode==='qwen_edit'));
  $$('#uiMode [data-ui]').forEach(b=>b.classList.toggle('primary',(b.dataset.ui==='simple')===SIMPLE));
}
$$('#uiMode [data-ui]').forEach(b=>b.onclick=()=>{SIMPLE=b.dataset.ui==='simple';localStorage.setItem('studioSimple',SIMPLE?'1':'0');applySimpleUI()});
applySimpleUI();
async function upload(file){
  const fd=new FormData();fd.append('file',file);
  const r=await api('/api/upload',{method:'POST',body:fd});
  ASSETS=null;
  return r;
}
$('refUp').onclick=()=>$('refFile').click();
$('firstUp').onclick=()=>$('firstFile').click();
$('lastUp').onclick=()=>$('lastFile').click();
$('refFile').onchange=async e=>{for(const f of e.target.files){const r=await upload(f);S.refs.push(r)}renderRefThumbs()};

/* 提示词框：粘贴/拖拽图片即上传为参考图 */
async function addImageFiles(files){
  const imgs=[...files].filter(f=>f&&f.type&&f.type.startsWith('image/'));
  if(!imgs.length)return 0;
  $('imgArea').classList.remove('hidden');
  for(const f of imgs){const r=await upload(f);S.refs.push(r)}
  renderRefThumbs();toast('已添加 '+imgs.length+' 张参考图');return imgs.length;
}
$('prompt').addEventListener('paste',async e=>{
  const items=[...(e.clipboardData?.items||[])];
  const files=items.filter(i=>i.kind==='file'&&i.type.startsWith('image/')).map(i=>i.getAsFile()).filter(Boolean);
  if(!files.length)return;
  e.preventDefault();
  await addImageFiles(files);
});
$('prompt').addEventListener('dragover',e=>{if([...(e.dataTransfer?.items||[])].some(i=>i.kind==='file'))e.preventDefault()});
$('prompt').addEventListener('drop',async e=>{
  const files=[...(e.dataTransfer?.files||[])];
  if(!files.some(f=>f.type&&f.type.startsWith('image/')))return;
  e.preventDefault();
  await addImageFiles(files);
});
function renderRefThumbs(){
  $('refThumbs').innerHTML=S.refs.map((x,i)=>'<div class="thumb"><img src="/api/media?path='+encodeURIComponent(x.path)+'"><b data-r="'+i+'">×</b></div>').join('');
  $('refThumbs').querySelectorAll('b').forEach(b=>b.onclick=()=>{S.refs.splice(+b.dataset.r,1);renderRefThumbs()});
}
$('firstFile').onchange=async e=>{if(!e.target.files[0])return;S.first=await upload(e.target.files[0]);
  $('firstThumb').innerHTML='<div class="thumb"><img src="/api/media?path='+encodeURIComponent(S.first.path)+'"><b onclick="S.first=null;this.parentNode.remove()">×</b></div>'};
$('lastFile').onchange=async e=>{if(!e.target.files[0])return;S.last=await upload(e.target.files[0]);
  $('lastThumb').innerHTML='<div class="thumb"><img src="/api/media?path='+encodeURIComponent(S.last.path)+'"><b onclick="S.last=null;this.parentNode.remove()">×</b></div>'};

let ASSETS=null;
function closeAt(){const p=$('atPop');if(p)p.classList.add('hidden')}
async function openAt(){
  try{
    if(!ASSETS){const r=await api('/api/assets');ASSETS=r.assets||[]}
    const pop=$('atPop');const btn=$('atBtn');const rect=btn.getBoundingClientRect();
    pop.style.left=Math.max(12,rect.left)+'px';pop.style.top=(rect.bottom+6)+'px';
    pop.innerHTML=ASSETS.length?ASSETS.map(a=>'<div data-name="'+esc(a.name)+'" style="cursor:pointer;border:1px solid #2c3550;border-radius:8px;overflow:hidden;background:#0f1424"><img src="/api/media?path='+encodeURIComponent(a.path)+'" style="width:100%;height:78px;object-fit:cover;display:block"><div style="font-size:10px;padding:4px;color:#9fb0d0;word-break:break-all">'+esc(a.name)+'</div></div>').join(''):'<div class="muted" style="grid-column:1/-1;padding:8px">input 目录暂无图片，先上传参考图</div>';
    pop.querySelectorAll('[data-name]').forEach(el=>el.onclick=()=>insertMention(el.dataset.name));
    pop.classList.remove('hidden');
  }catch(e){toast('素材加载失败')}
}
function insertMention(name){
  const ta=$('prompt');const pos=ta.selectionStart??ta.value.length;
  const left=ta.value.slice(0,pos).replace(/@[^\s@]*$/,'');
  ta.value=left+'@'+name+' '+ta.value.slice(pos);ta.focus();
  const a=(ASSETS||[]).find(x=>x.name===name);if(a&&!S.refs.some(x=>x.name===name))S.refs.push(a);
  renderRefThumbs();closeAt();
}
$('atBtn').onclick=e=>{e.preventDefault();openAt()};
function maybeAt(){
  const ta=$('prompt');if(!ta)return;
  const pos=ta.selectionStart??ta.value.length;
  const before=ta.value.slice(0,pos);
  if(/[@＠][^\s@＠]*$/.test(before))openAt();
}
$('prompt').addEventListener('input',maybeAt);
$('prompt').addEventListener('keyup',maybeAt);
document.addEventListener('click',e=>{if(!$('atPop').contains(e.target)&&e.target!==$('atBtn'))closeAt()});

const TPL_MAP={t2v:'minimax_h3_t2v',fl2v:'minimax_h3_fl2v',r2v:'minimax_h3_r2v',qwen_image:'qwen_image',qwen_edit:'qwen_image_edit',h3_image:'minimax_h3_image'};
function isImgMode(m){return m==='qwen_image'||m==='qwen_edit'||m==='h3_image'}
function retrySafe(w,h){const s=$('resPreset');const v=w+'x'+h;if([...s.options].some(o=>o.value===v)){setRes(v)}else{$('cw').value=w;$('ch').value=h;setRes('custom')}submitGenerate()}
function isImgPath(p){return /\.(png|jpe?g|webp)$/i.test(String(p||''))}
function setRes(v){const s=$('resPreset');if(![...s.options].some(o=>o.value===v))return;s.value=v;if(s.onchange)s.onchange()}
/* ---------- 生成 ---------- */
function getDur(){
  const v=$('dur')?$('dur').value:'5';
  if(v==='custom'){const n=parseFloat($('durCustom')?.value);return isNaN(n)?5:n}
  const n=parseFloat(v);return isNaN(n)?5:n
}
$('dur')&&($('dur').onchange=()=>{const c=$('durCustom');if(c)c.classList.toggle('hidden',$('dur').value!=='custom')});

async function submitGenerate(){
  const prompt=$('prompt').value.trim();
  if(!prompt){toast('请先填写提示词');return}
  const [w,h]=pickRes();
  let tpl=TPL_MAP[S.mode]||'minimax_h3_t2v';
  const img=isImgMode(S.mode);const qwenFam=(S.mode==='qwen_image'||S.mode==='qwen_edit');
  const p={prompt,width:w,height:h,steps:parseInt($('steps').value),seed:parseInt($('seed').value)};
  // 上传的参考图 + @素材 统一带上
  if(!img&&S.refs.length){
    p.ref_images=[...new Set([...(p.ref_images||[]),...S.refs.map(x=>x.name)])];
    if(tpl==='minimax_h3_t2v'||tpl==='minimax_h3_fl2v')tpl='minimax_h3_r2v';
  }
  // 客户端兜底解析 @素材，保证引用一定随请求发送
  const atNames=[...prompt.matchAll(/@([^\s@]+)/g)].map(m=>m[1]);
  if(!img&&atNames.length){
    p.ref_images=[...new Set([...(S.refs||[]).map(x=>x.name),...atNames])];
    if(tpl==='minimax_h3_t2v'||tpl==='minimax_h3_fl2v')tpl='minimax_h3_r2v';
  }
  if(!img){
    p.profile=$('profile').value;p.duration_s=getDur();p.optimize=$('optimize').value;
    if($('twoPass')?.value==='1'){p.two_pass=true;const ts=parseFloat($('twoPassScale')?.value);p.two_pass_scale=isNaN(ts)?1.9:ts}
  }else if(S.mode==='h3_image'){p.optimize=$('optimize').value}
  if(!img&&S.mode!=='r2v'){
    p.lora_name=$('lora').value;
    const ls=parseFloat($('loraStrength').value);if(!isNaN(ls))p.lora_strength=ls;
  }
  if(S.mode==='r2v'){p.ref_image_size=$('refSize').value;
    p.ref_images=[...new Set([...(S.refs||[]).map(x=>x.name),...(p.ref_images||[])])];}
  if(S.mode==='qwen_edit'&&S.refs.length)p.images=S.refs.map(x=>x.name);
  if(S.mode==='fl2v'){if(S.first)p.first_frame=S.first.name;if(S.last)p.last_frame=S.last.name}
  if(!img&&SIMPLE){
    const q=QMAP[$('simpleQuality').value]||QMAP.standard;
    p.width=q.w;p.height=q.h;p.steps=q.steps;p.profile=q.profile;
    p.optimize='max';p.lora_name=q.lora;
  }
  const sm=$('sampler').value,sc=$('scheduler').value;if(sm)p.sampler=sm;if(sc)p.scheduler=sc;
  const u=$('unetSel').value,c=$('clipSel').value,vv=$('vaeSel').value;
  if(u)p.unet_name=u;if(c)p.clip_name=c;if(vv)p.vae_name=vv;
  if(p.optimize&&p.optimize!=='off'&&!qwenFam){
    p.attn_head_chunks=parseInt($('headChunks').value)||4;
    p.ff_chunks=parseInt($('ffChunks').value)||2;
    p.ff_seq_threshold=parseInt($('ffSeq').value)||4096;
    p.te_speed_device=$('teDevice').value||'auto';
  }
  $('genBtn').disabled=true;$('genMsg').textContent='提交中…';
  try{
    const r=await api('/api/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({template:tpl,params:p})});
    if(!r.ok){
      const errs=(r.validation?.errors||[]).slice(0,3).join('；');
      const greasons=(r.guard?.reasons||[]).slice(0,2).join('；');
      const msg=(r.error||r.reason||r.hint||'未知')+(greasons?' · '+greasons:'')+(errs?' · '+errs:'');
      const safe=(S.mode==='qwen_image'||S.mode==='qwen_edit')?[1024,1024]:(S.mode==='h3_image'?[768,768]:[608,352]);
      const pxLimit=isImgMode(S.mode)?4194304:2088960;
      const bigImg=w*h>pxLimit;
      const hint=bigImg?('当前 '+w+'×'+h+'='+(w*h)+' px 超过安全上限 '+pxLimit+' px；'):'';
      $('genMsg').innerHTML='失败：'+esc(hint+msg)+
        ' <a href="javascript:void(0)" style="color:#6cf" onclick="retrySafe('+safe[0]+','+safe[1]+')">用 '+safe[0]+'×'+safe[1]+' 重试</a>';
      toast('生成失败：'+(hint+msg).slice(0,120));
      return
    }
    const refN=(p.ref_images||[]).length;
    $('genMsg').textContent='已提交 '+(r.job_id||'').slice(0,8)+(refN?(' · 引用 '+refN+' 张'):'');toast('已提交生成任务');
    if(r.queued){
      $('genMsg').textContent='已排队：'+(r.queue_id||'')+'（前方 '+((r.position||1)-1)+' 个）';
      toast('已加入队列，空闲后自动开始');return
    }
    if(r.segmented){
      $('genMsg').textContent=r.message||'已自动转分段模式';toast('已转分段模式');
      return
    }
    attachJob(r.job_id,{template:tpl,fresh:true});
  }catch(e){$('genMsg').textContent='失败：'+e.message;toast('提交失败')}
  finally{$('genBtn').disabled=false}
}
async function planPreview(){
  const prompt=$('prompt').value.trim()||'test prompt';
  const [w,h]=pickRes();
  const tpl=TPL_MAP[S.mode]||'minimax_h3_t2v';
  const img=isImgMode(S.mode);
  const p={prompt,width:w,height:h,steps:parseInt($('steps').value)};
  if(!img){p.profile=$('profile').value;p.duration_s=getDur();p.optimize=$('optimize').value}
  else if(S.mode==='h3_image'){p.optimize=$('optimize').value}
  if(!img&&S.mode!=='r2v')p.lora_name=$('lora').value;
  if(S.mode==='qwen_edit'&&S.refs.length)p.images=S.refs.map(x=>x.name);
  $('planMsg').textContent='校验中…';
  try{
    const r=await api('/api/plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({template:tpl,params:p})});
    const v=r.validation||{},g=r.guard||{},c=(g.snapshot||{}).cost||{};
    let txt=(r.ok?'✓ 校验通过':'✗ 校验失败')+' · validation='+(v.ok?'ok':('errors:'+((v.errors||[]).length)))+
      ' · risk='+(g.risk||'?')+' · eta≈'+(c.eta_minutes??'?')+'min · '+(w+'×'+h+' '+(c.frames??'')+'帧');
    if(v.errors&&v.errors.length)txt+='\n'+v.errors.slice(0,3).join('\n');
    $('planMsg').textContent=txt;
  }catch(e){$('planMsg').textContent='校验失败：'+e.message}
}

/* ---------- 图像生成（专用表单/进度） ---------- */
const I={mode:'qwen_image',refs:[],loaded:false};
const IMG_TPL={qwen_image:'qwen_image',qwen_image_edit:'qwen_image_edit',h3_image:'minimax_h3_image',moss_image:'moss_image',moss_image_edit:'moss_image_edit'};
function planMsgClear(){const e=$('igenMsg');if(e)e.textContent=''}
function setImgMode(m){
  I.mode=m;
  $$('#imgModes .m').forEach(el=>el.classList.toggle('on',el.dataset.imode===m));
  const edit=(m==='qwen_image_edit');
  $('irefArea').classList.toggle('hidden',!edit);
  $('ipromptLab').textContent=edit?'编辑指令':'提示词';
  $('isteps').value=(m==='h3_image')?8:((m==='moss_image_edit')?30:25);
  isetSize(m==='h3_image'?768:1024,m==='h3_image'?768:1024);
  const cfgWrap=$('icfg');if(cfgWrap){cfgWrap.disabled=(m!=='moss_image'&&m!=='moss_image_edit');cfgWrap.value=(m==='moss_image_edit')?'4.0':'2.5';}
  planMsgClear();
}
function isetSize(w,h){$('iw').value=w;$('ih').value=h;const v=w+'x'+h;const s=$('ires');if([...s.options].some(o=>o.value===v))s.value=v;else s.value='custom'}
function fillModelSelect(id,items){const s=$(id);if(!s)return;s.innerHTML=items.map(v=>'<option value="'+(v==='（默认）'?'':esc(v))+'">'+esc(v)+'</option>').join('')}
async function initImagePage(){
  if(!I.loaded){
    try{const m=await api('/api/models');fillModelSelect('iunet',['（默认）'].concat(m.unet||[]));fillModelSelect('iclips',['（默认）'].concat(m.clip||[]));fillModelSelect('ivaes',['（默认）'].concat(m.vae||[]))}catch(e){}
    I.loaded=true;
  }
  renderRecent();renderLive();
}
$('imgModes').addEventListener('click',e=>{const t=e.target.closest('[data-imode]');if(t)setImgMode(t.dataset.imode)});
$('ires').onchange=()=>{const v=$('ires').value;if(v==='custom')return;const [w,h]=v.split('x').map(Number);$('iw').value=w;$('ih').value=h};
$('irefUp').onclick=()=>$('irefFile').click();
$('irefFile').onchange=async e=>{for(const f of e.target.files){if(I.refs.length>=10){toast('最多 10 张参考图');break}const r=await upload(f);I.refs.push(r)}renderIRefs()};
function renderIRefs(){
  $('irefThumbs').innerHTML=I.refs.map((x,i)=>'<div class="thumb"><img src="/api/media?path='+encodeURIComponent(x.path)+'"><b data-r="'+i+'">×</b></div>').join('');
  $('irefThumbs').querySelectorAll('b').forEach(b=>b.onclick=()=>{I.refs.splice(+b.dataset.r,1);renderIRefs()});
}
function imgParams(){
  const w=parseInt($('iw').value)||1024,h=parseInt($('ih').value)||1024;
  const p={prompt:$('iprompt').value.trim(),width:w,height:h,steps:parseInt($('isteps').value)||25};
  const sd=parseInt($('iseed').value);if(!isNaN(sd))p.seed=sd;
  if(I.mode==='qwen_image_edit'){
    p.images=I.refs.map(x=>x.name);
    p.cache_device=$('icacheDev').value;p.cache_dtype=$('icacheDT').value;
  }
  if(I.mode==='h3_image')p.optimize='max';
  if(I.mode==='moss_image'||I.mode==='moss_image_edit'){const cf=parseFloat($('icfg').value);if(!isNaN(cf))p.cfg=cf;}
  if(I.mode==='moss_image_edit'){p.ref_images=I.refs.map(x=>x.name);}
  const neg=$('ineg').value.trim();if(neg)p.negative_prompt=neg;
  const sm=$('isampler').value.trim(),sc=$('ischedule').value.trim();if(sm)p.sampler=sm;if(sc)p.scheduler=sc;
  const lo=$('ilora').value.trim();if(lo){p.lora_name=lo;const ls=parseFloat($('ilorastr').value);if(!isNaN(ls))p.lora_strength=ls}
  const u=$('iunet').value,c=$('iclips').value,v=$('ivaes').value;if(u)p.unet_name=u;if(c)p.clip_name=c;if(v)p.vae_name=v;
  return {template:IMG_TPL[I.mode],params:p,w:w,h:h};
}
async function submitImage(){
  const prompt=$('iprompt').value.trim();if(!prompt){toast('请先填写提示词');return}
  if(I.mode==='qwen_image_edit'&&!I.refs.length){toast('图像编辑至少需要 1 张参考图');return}
  if(I.mode==='moss_image_edit'&&!I.refs.length){toast('MOSS 编辑需要 1 张源图');return}
  const {template,params,w,h}=imgParams();
  $('igenBtn').disabled=true;$('igenMsg').textContent='提交中…';
  try{
    const r=await api('/api/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({template,params})});
    if(!r.ok){
      const errs=(r.validation?.errors||[]).slice(0,2).join('；');
      const gr=(r.guard?.reasons||[]).slice(0,2).join('；');
      const pxLimit=(I.mode==='h3_image')?2097152:4194304;
      const pxHint=(w*h>pxLimit)?('当前 '+w+'×'+h+'='+(w*h)+' px 超过上限 '+pxLimit+' px；'):'';
      $('igenMsg').textContent='失败：'+pxHint+(r.error||r.reason||'')+(gr?' · '+gr:'')+(errs?' · '+errs:'');
      toast('生成失败');return
    }
    const warn=(r.warnings&&r.warnings.length)?r.warnings[0]:((r.resolved&&r.resolved.warnings&&r.resolved.warnings.length)?r.resolved.warnings[0]:'');
    $('igenMsg').textContent='已提交 '+(r.job_id||'').slice(0,8)+(warn?(' · '+warn):'');
    attachJob(r.job_id,{template,fresh:true});
  }catch(e){$('igenMsg').textContent='失败：'+e.message}
  finally{$('igenBtn').disabled=false}
}
async function planImage(){
  const {template,params}=imgParams();
  $('igenMsg').textContent='校验中…';
  try{
    const r=await api('/api/plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({template,params})});
    const v=r.validation||{},g=r.guard||{},c=(g.snapshot||{}).cost||{};
    let txt=(r.ok?'✓ 校验通过':'✗ 校验失败')+' · risk='+(g.risk||'?')+' · eta≈'+(c.eta_minutes??'?')+'min · '+params.width+'×'+params.height;
    if(v.errors&&v.errors.length)txt+=' · '+v.errors.slice(0,2).join('；');
    $('igenMsg').textContent=txt;
  }catch(e){$('igenMsg').textContent='校验失败：'+e.message}
}

/* ---------- 长片成片 ---------- */
const F={refs:[],output:null,timer:null};
$('frefUp').onclick=()=>$('frefFile').click();
$('frefFile').onchange=async e=>{
  for(const file of e.target.files){if(F.refs.length>=4){toast('最多 4 张参考图');break}const r=await upload(file);F.refs.push(r)}
  renderFRefs();e.target.value='';
};
function renderFRefs(){
  $('frefThumbs').innerHTML=F.refs.map((x,i)=>'<div class="thumb"><img src="/api/media?path='+encodeURIComponent(x.path)+'"><b data-r="'+i+'">×</b></div>').join('');
  $('frefThumbs').querySelectorAll('b').forEach(b=>b.onclick=()=>{F.refs.splice(+b.dataset.r,1);renderFRefs()});
}
async function startFilm(){
  const prompt=$('fprompt').value.trim();
  if(!prompt){toast('请先填写提示词');return}
  const [w,h]=$('fsize').value.split('x').map(Number);
  $('fstart').disabled=true;$('fmsg').textContent='提交中…';
  try{
    const r=await api('/api/film/start',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({prompt,refs:F.refs.map(x=>x.name),segments:parseInt($('fseg').value),
        frames:124,width:w,height:h,steps:parseInt($('fsteps').value),mode:$('fmode').value})});
    if(!r.ok){$('fmsg').textContent='失败：'+(r.error||'未知');toast('启动失败');return}
    $('fmsg').textContent='已启动 '+r.tag;toast('长片任务已启动');
    pollFilm();clearInterval(F.timer);F.timer=setInterval(pollFilm,4000);
  }catch(e){$('fmsg').textContent='失败：'+e.message}
  finally{$('fstart').disabled=false}
}
async function cancelFilm(){
  try{await api('/api/film/cancel',{method:'POST'});toast('已发送取消');pollFilm()}catch(e){toast('取消失败')}
}
async function pollFilm(){
  try{
    const d=await api('/api/film/state');
    if(d.ok&&d.state)renderFilm(d.state);
  }catch(e){}
}
function renderFilm(st){
  const total=st.segments_total||1;
  const done=(st.segments_done||0)+(st.status==='done'?0:0);
  const pct=st.status==='done'?100:Math.round(done/total*100);
  $('fbar').style.width=pct+'%';
  $('fsegNow').textContent=(st.current_segment||0)+' / '+total;
  $('felapsed').textContent=fmtClock(st.elapsed_s||0);
  $('fstate').textContent=st.status||'--';
  $('fdetail').textContent=st.message||'';
  const cls=st.status==='done'?'ok':(st.status==='running'||st.status==='concat'?'run':'wait');
  $('fbadge').className='badge '+cls;$('fbadge').textContent=({done:'完成',running:'生成中',concat:'拼接中',failed:'失败',cancelled:'已取消',timeout:'超时'}[st.status]||st.status||'空闲');
  if(st.output){
    F.output=st.output;
    const u='/api/media?path='+encodeURIComponent(st.output);
    if($('fvideo').getAttribute('src')!==u)$('fvideo').src=u;
    $('fresult').classList.add('on');
  }
  if(['done','failed','cancelled','timeout'].includes(st.status)){clearInterval(F.timer);F.timer=null;$('fstart').disabled=false}
}

/* ---------- 实时任务引擎 ---------- */
function stageIndex(k){const i=STAGES.findIndex(s=>s.k===k);return i<0?T.stage:i}
function setStage(k,detail,silent){
  const i=stageIndex(k);
  if(i>=T.stage){T.stage=i;T.stageKey=k}
  if(detail)T.detail=detail;
  if(!silent)scheduleLive();
}
function statusMeta(st){
  if(T.demo)return {text:T.demoDone?'演示完成':'演示运行中',cls:T.demoDone?'ok':'run',dot:T.demoDone?'':'on'};
  if(!st)return {text:'等待任务',cls:'wait',dot:''};
  if(st==='queued')return {text:'排队中',cls:'wait',dot:'warn'};
  if(st==='running'||st==='in_progress')return {text:'生成中',cls:'run',dot:'on'};
  if(st==='completed')return {text:'已完成',cls:'ok',dot:''};
  if(st==='cancelled')return {text:'已取消',cls:'fail',dot:'bad'};
  if(st==='aborted')return {text:'已中止',cls:'fail',dot:'bad'};
  if(st==='failed')return {text:'失败',cls:'fail',dot:'bad'};
  return {text:st,cls:'wait',dot:''};
}
function templateName(tpl){return FRIENDLY[tpl]||tpl||'未选择模板'}
function scheduleLive(){if(T.renderQueued)return;T.renderQueued=true;requestAnimationFrame(()=>{T.renderQueued=false;renderLive()})}
function renderLive(){
  const st=T.job?.status||'';
  const meta=statusMeta(st);
  const pct=Math.max(0,Math.min(100,Math.round(T.pct)));
  const active=!!T.jobId||T.demo;
  const running=T.demo?(!!T.demoRunning):(st==='running'||st==='queued'||st==='in_progress');
  const measured=!!T.hasPct;
  const pctText=measured?pct:'--';
  $$('[data-live="state"]').forEach(el=>el.textContent=meta.text);
  $$('[data-live="dot"]').forEach(el=>el.className='livedot '+meta.dot);
  $$('[data-live="badge"]').forEach(el=>{el.className='badge '+(T.demo?'demo':meta.cls);el.textContent=T.demo?'演示':meta.text});
  $$('[data-live="pct"]').forEach(el=>el.textContent=pctText);
  $$('[data-live="pctLabel"]').forEach(el=>el.textContent=st==='completed'?'生成完成':(measured?'采样进度':'等待采样'));
  $$('[data-live="bar"]').forEach(el=>el.style.width=(measured?pct:0)+'%');
  const etaEl=$('ieta');if(etaEl){etaEl.textContent=(measured&&pct>2&&T.elapsed>0)?('≈'+fmtClock(Math.round(T.elapsed*(100-pct)/pct))):'--'}
  $$('[data-live="track"]').forEach(el=>{el.classList.toggle('live',running&&measured);el.classList.toggle('indet',running&&!measured);el.classList.toggle('fail',meta.cls==='fail')});
  $$('[data-live="ring"]').forEach(el=>el.classList.toggle('moving',running&&!measured));
  $$('[data-live="ringFill"]').forEach(el=>{el.style.strokeDashoffset=measured?String(339.3*(1-pct/100)):'0'});
  $$('[data-live="stages"]').forEach(el=>el.style.setProperty('--p',Math.min(100,T.stage/STAGES.length*100)));
  $$('[data-stage-step]').forEach(el=>{
    const i=stageIndex(el.dataset.stageStep);
    el.classList.toggle('done',i<T.stage||(st==='completed'&&i===STAGES.length-1));
    el.classList.toggle('now',i===T.stage&&running);
  });
  $$('[data-live="elapsed"]').forEach(el=>el.textContent=fmtClock(T.elapsed));
  $$('[data-live="id"]').forEach(el=>el.textContent=T.jobId?('#'+T.jobId.slice(0,8)):'--');
  $$('[data-live="template"]').forEach(el=>el.textContent=templateName(T.job?.template));
  $$('[data-live="sample"]').forEach(el=>el.textContent=T.sample?(T.sample.value+' / '+T.sample.max):'--');
  $$('[data-live="stage"]').forEach(el=>el.textContent=STAGES[T.stage]?.label||(st==='completed'?'完成':'等待'));
  $$('[data-live="detail"]').forEach(el=>el.textContent=T.detail||(active?'正在等待引擎事件…':'提交任务后，这里会实时显示采样进度与事件日志。'));
  $$('[data-live="demoNote"]').forEach(el=>el.classList.toggle('on',T.demo));
  $$('[data-live="transport"]').forEach(el=>{el.className='transport '+(T.wsReady?'ok':'');el.textContent=T.wsReady?'事件通道在线':'轮询模式'});
  const resultOn=!!T.output;
  $$('[data-live="result"]').forEach(el=>el.classList.toggle('on',resultOn));
  const imgOut=resultOn&&isImgPath(T.output);
  $$('[data-live="video"]').forEach(el=>{const u='/api/media?path='+encodeURIComponent(T.output||'');el.style.display=(resultOn&&!imgOut)?'':'none';if(resultOn&&!imgOut&&el.getAttribute('src')!==u)el.src=u});
  $$('[data-live="image"]').forEach(el=>{const u='/api/media?path='+encodeURIComponent(T.output||'');el.style.display=(resultOn&&imgOut)?'':'none';if(resultOn&&imgOut&&el.getAttribute('src')!==u)el.src=u});
  $$('[data-live="cockpit"]').forEach(el=>el.classList.toggle('is-live',running));
  const pill=$('taskPill');
  if(pill){
    const show=active&&running;
    pill.style.display=show?'flex':'none';
    if(show){
      const stageLabel=STAGES[T.stage]?.label||'处理中';
      $('taskPillText').textContent=(T.demo?'演示 · ':'')+stageLabel+(measured?' · '+pct+'%':'');
    }
  }
  const nav=$('navDot');if(nav)nav.classList.toggle('on',!!running);
  $$('[data-live="cancel"]').forEach(el=>el.classList.toggle('hidden',!(running&&(T.jobId||T.demoRunning))));
}
function renderLogs(){
  $$('[data-log]').forEach(box=>{
    const mini=box.dataset.log==='mini';
    const rows=mini?T.events.slice(-7):T.events;
    if(!rows.length){box.innerHTML='<div class="empty">'+(mini?'暂无事件 · 点击演示动效查看效果':'等待任务事件…')+'</div>';return}
    box.innerHTML=rows.map(e=>{
      const detail=e.detail?' <span class="d">'+esc(e.detail)+'</span>':'';
      return '<div class="logline '+e.kind+'"><span class="t">'+esc(e.time)+'</span><span class="k"></span><span class="m">'+esc(e.msg)+detail+'</span></div>';
    }).join('');
    if(T.autoScroll)box.scrollTop=box.scrollHeight;
  });
}
function logEvent(kind,msg,detail){
  const d=new Date();
  T.events.push({time:pad(d.getHours())+':'+pad(d.getMinutes())+':'+pad(d.getSeconds()),kind,msg,detail:detail||''});
  if(T.events.length>260)T.events.splice(0,T.events.length-260);
  renderLogs();
}
function toggleAutoScroll(){T.autoScroll=!T.autoScroll;$('autoScrollBtn').classList.toggle('on',T.autoScroll);$('autoScrollBtn').textContent=T.autoScroll?'自动滚动':'已暂停'}
async function copyLogs(){
  const text=T.events.map(e=>'['+e.time+'] '+e.msg+(e.detail?' '+e.detail:'')).join('\n');
  if(!text){toast('暂无日志');return}
  try{await navigator.clipboard.writeText(text);toast('日志已复制')}
  catch(e){const ta=document.createElement('textarea');ta.value=text;document.body.appendChild(ta);ta.select();document.execCommand('copy');ta.remove();toast('日志已复制')}
}
function clearLogs(){T.events=[];renderLogs();toast('日志已清空')}

function attachJob(id,opts){
  if(!id)return;
  opts=opts||{};
  if(T.jobId===id&&T.job&&!opts.force){
    if(opts.fresh){
      T.job.template=opts.template||T.job.template;
      if(!T.events.some(e=>e.msg==='任务已提交'))logEvent('task','任务已提交','#'+id.slice(0,8)+' · '+templateName(T.job.template));
    }
    renderLive();return
  }
  stopDemo(true);
  T.jobId=id;T.job={id,status:'queued',template:opts.template||''};T.output=null;T.sample=null;
  T.stage=0;T.stageKey='queue';T.pct=0;T.hasPct=false;T.detail='任务已进入本地队列';T.lastLogPct=0;
  T.startedAt=Date.now();T.elapsed=0;T.lastStatus='queued';T.events=[];T.lastLogKey='';
  if(opts.fresh){logEvent('task','任务已提交','#'+id.slice(0,8)+' · '+templateName(opts.template||''));logEvent('sys','等待 ComfyUI 接收任务…')}
  else{logEvent('sys','已连接到任务','#'+id.slice(0,8)+'（早期事件不会补发）')}
  scheduleLive();startPoll();
}
function restoreActive(){
  return api('/api/jobs?limit=20&status=queued,running').then(r=>{
    const j=(r.jobs||[])[0];
    if(j){attachJob(j.id,{template:j.template,force:true});return}
    // 兜底：直接读 ComfyUI 实时队列（覆盖脚本直连提交、非本页面提交的任务）
    return api('/api/queue').then(q=>{
      const run=(q.running||[])[0];
      if(run){const pid=Array.isArray(run)?run[1]:run;if(pid)attachJob(String(pid),{force:true})}
    });
  }).catch(()=>{});
}
function startPoll(){
  clearInterval(T.pollTimer);
  pollOnce();
  T.pollTimer=setInterval(pollOnce,2200);
}
async function pollOnce(){
  if(!T.jobId||T.demo)return;
  let r;
  try{r=await api('/api/jobs/'+encodeURIComponent(T.jobId))}catch(e){return}
  const job=r.job||{};const prog=r.progress||{};
  T.job=job;
  if(job.started_at)T.startedAt=parseTime(job.started_at)||T.startedAt;
  if(typeof prog.elapsed_s==='number'&&prog.elapsed_s>T.elapsed)T.elapsed=prog.elapsed_s;
  if(job.status&&job.status!==T.lastStatus){
    if(job.status==='running'&&T.stage<=0){setStage('load','ComfyUI 已开始执行');logEvent('stage','开始执行工作流')}
    if(job.status==='queued'){T.detail='等待 GPU 空闲…'}
    T.lastStatus=job.status;
  }
  const le=String(prog.last_event||'');
  if(le==='execution_start')setStage('load','工作流已启动（轮询模式，采样进度需事件通道）');
  else if(le==='execution_success'){T.hasPct=true;T.pct=100;setStage('save','输出封装完成')}
  else if(le==='execution_error')T.detail='执行错误，等待任务状态同步';
  if(le&&le!==T.lastLogKey){T.lastLogKey=le;logEvent('sys','引擎事件',le)}
  if(prog.last_event&&prog.completed){T.detail='最近事件：'+prog.last_event}
  if(job.status==='completed'){
    finishJob('completed',job);return
  }
  if(job.status==='failed'||job.status==='cancelled'||job.status==='aborted'){
    finishJob(job.status,job,r.progress);return
  }
  scheduleLive();
}
function finishJob(status,job){
  clearInterval(T.pollTimer);T.pollTimer=null;
  const out=(job.outputs||[]).find(o=>String(o.path||o.abs_path||'').toLowerCase().endsWith('.mp4'))||(job.outputs||[])[0];
  const path=out&&(out.filename_abs||out.abs_path||out.path);
  if(status==='completed'){
    T.pct=100;T.hasPct=true;T.stage=STAGES.length;T.stageKey='done';
    T.detail='生成完成 · '+(path?path.split('\\').pop():'输出已保存');
    logEvent('done','任务完成','输出 '+(path?path.split('\\').pop():'已写入'));
    if(path){T.output=path;S.curOutput=path;refreshVideos()}
    toast('生成完成');
  }else{
    T.detail='任务'+({failed:'失败',cancelled:'已取消',aborted:'已中止'}[status]||status);
    logEvent(status==='failed'?'error':'warn','任务'+({failed:'失败',cancelled:'已取消',aborted:'已中止'}[status]||status),job.error||'');
    toast('任务'+({failed:'失败',cancelled:'已取消',aborted:'已中止'}[status]||status));
  }
  T.job=job;T.lastStatus=status;
  scheduleLive();setTimeout(()=>loadTasks(),300);
}
async function cancelActive(){
  if(T.demo){stopDemo();return}
  if(!T.jobId){toast('当前没有运行中的任务');return}
  try{await api('/api/jobs/'+encodeURIComponent(T.jobId)+'/cancel',{method:'POST'});logEvent('warn','已发送取消请求');toast('正在取消任务…');setTimeout(pollOnce,500)}
  catch(e){toast('取消失败：'+e.message)}
}
async function initRealtime(){
  try{
    const r=await api('/api/realtime');T.wsUrl=r.ws_url||'';
    if(T.wsUrl&&!/^wss?:/i.test(T.wsUrl))T.wsUrl=(location.protocol==='https:'?'wss://':'ws://')+location.host+T.wsUrl;
  }catch(e){T.wsUrl=''}
  if(T.wsUrl)checkAndConnect();
}
async function checkAndConnect(){
  if(T.wsReady)return;
  try{const st=await api('/api/status');if(!st.comfy?.up){clearTimeout(T.reconnectTimer);T.reconnectTimer=setTimeout(checkAndConnect,15000);return}}catch(e){}
  connectWs();
}
function connectWs(){
  if(!T.wsUrl||T.wsReady)return;
  try{
    const ws=new WebSocket(T.wsUrl);T.ws=ws;
    ws.onopen=()=>{T.wsReady=true;T.wsFails=0;logEvent('sys','实时事件通道已连接');scheduleLive();clearTimeout(T.reconnectTimer)};
    ws.onmessage=e=>{let m;try{m=JSON.parse(e.data)}catch(err){return};handleWs(m)};
    ws.onclose=()=>{T.wsReady=false;scheduleLive();scheduleReconnect()};
    ws.onerror=()=>{try{ws.close()}catch(err){}};
  }catch(e){scheduleReconnect()}
}
function scheduleReconnect(){
  clearTimeout(T.reconnectTimer);
  const delay=Math.min(60000,4000*Math.pow(1.6,T.wsFails++));
  T.reconnectTimer=setTimeout(checkAndConnect,delay);
}
function handleWs(m){
  const type=m.type||m.event;const data=m.data||m;
  if(!type)return;
  if(type==='status'){
    const q=(data.status||{}).exec_info||{};
    return;
  }
  if(type==='execution_start'){
    if(data.prompt_id&&(!T.jobId||T.jobId!==data.prompt_id)){
      if(!T.demo){attachJob(data.prompt_id,{})}
    }
    if(data.prompt_id===T.jobId){setStage('load','工作流已启动');logEvent('stage','execution_start','工作流已启动')}
    return;
  }
  if(data.prompt_id&&T.jobId&&data.prompt_id!==T.jobId)return;
  if(type==='execution_cached'){
    const n=(data.nodes||[]).length;
    if(n)logEvent('sys','节点缓存命中','跳过 '+n+' 个已缓存节点');
    return;
  }
  if(type==='executing'){
    const node=String(data.node||'');
    if(!node){setStage('save','正在收尾…');return}
    const k=NODE_STAGE[node]||'sample';
    const name=NODE_NAME[node]||('节点 '+node);
    setStage(k,'正在执行：'+name);
    if(node==='9')logEvent('stage','进入扩散采样');
    else if(k==='decode')logEvent('stage','进入 VAE 解码');
    else if(k==='save')logEvent('stage','封装并保存视频');
    else logEvent('sys',name);
    return;
  }
  if(type==='progress'){
    if(!data.max)return;
    const value=Number(data.value)||0,max=Number(data.max)||0;
    if(!max)return;
    T.sample={value,max};
    T.stage=stageIndex('sample');T.stageKey='sample';T.hasPct=true;
    T.pct=(value/max)*100;
    T.detail='扩散采样 '+value+' / '+max+' 步';
    const p=Math.round(value/max*100);
    if(!T.lastLogPct||p>=T.lastLogPct+20||p>=100){
      T.lastLogPct=p;logEvent('progress','采样 '+value+'/'+max,'· '+p+'%');
    }
    scheduleLive();
    return;
  }
  if(type==='executed'){
    const node=String(data.node||'');
    if(NODE_STAGE[node]&&node!=='9')logEvent('sys',(NODE_NAME[node]||('节点 '+node))+' 完成');
    return;
  }
  if(type==='execution_success'){
    if(!T.demo){T.hasPct=true;T.pct=100;setStage('save','输出封装完成');logEvent('done','execution_success','ComfyUI 已完成');setTimeout(pollOnce,550)}
    return;
  }
  if(type==='execution_error'){
    const msg=(data.exception_message||data.exception_type||'未知错误');
    logEvent('error','执行错误',String(msg).slice(0,220));T.detail='执行错误：'+String(msg).slice(0,100);
    scheduleLive();return;
  }
  if(type==='execution_interrupted'){
    logEvent('warn','执行被中断');T.detail='执行被中断';scheduleLive();
  }
}

/* ---------- 演示模式 ---------- */
function clearDemoTimers(){
  T.demoTimers.forEach(clearTimeout);T.demoTimers=[];
  if(T.demoInterval){clearInterval(T.demoInterval);T.demoInterval=null}
  if(T.demoInterval2){clearInterval(T.demoInterval2);T.demoInterval2=null}
}
function stopDemo(quiet){
  if(!T.demo)return;
  clearDemoTimers();T.demo=false;T.demoRunning=false;T.demoDone=false;
  T.jobId=null;T.job=null;T.output=null;T.events=[];T.pct=0;T.hasPct=false;T.stage=0;T.sample=null;T.detail='';
  scheduleLive();renderLogs();
  if(!quiet)toast('已退出演示');
}
function demoTimer(fn,ms){T.demoTimers.push(setTimeout(fn,ms))}
function startDemo(){
  if(T.jobId&&T.job&&['running','queued'].includes(T.job.status)){toast('真实任务运行中，无法演示');return}
  stopDemo(true);T.demo=true;T.demoRunning=true;T.demoDone=false;T.jobId=null;T.job=null;T.output=null;
  T.events=[];T.pct=0;T.hasPct=false;T.stage=0;T.stageKey='queue';T.detail='演示：任务已进入队列';T.sample=null;T.lastLogPct=0;T.elapsed=0;T.lastLogKey='';
  logEvent('task','[演示] 任务已提交','#demo · 文生视频 608×352 · 5s');
  logEvent('sys','[演示] 等待 ComfyUI 接收任务…');
  setStage('queue','演示：排队中',true);scheduleLive();
  demoTimer(()=>{setStage('load','演示：加载 UNet / CLIP / VAE');logEvent('stage','[演示] 开始执行工作流');logEvent('sys','[演示] 加载模型权重')},900);
  demoTimer(()=>{setStage('encode','演示：编码提示词与潜变量');logEvent('sys','[演示] 文本编码完成 · 构建 124 帧潜变量')},2200);
  demoTimer(()=>{
    if(!T.demo)return;
    setStage('sample','演示：扩散采样');logEvent('stage','[演示] 进入扩散采样');
    let v=0;const max=20;
    T.demoInterval2=setInterval(()=>{
      if(!T.demo){clearInterval(T.demoInterval2);return}
      v++;T.sample={value:v,max};T.stage=stageIndex('sample');T.hasPct=true;T.pct=(v/max)*100;T.detail='扩散采样 '+v+' / '+max+' 步';
      if(v===1||v===5||v===10||v===15||v===20)logEvent('progress','采样 '+v+'/'+max,'· '+Math.round(v/max*100)+'%');
      scheduleLive();
      if(v>=max){clearInterval(T.demoInterval2);T.demoInterval2=null}
    },520);
  },3300);
  demoTimer(()=>{setStage('decode','演示：视频 / 音频 VAE 解码');logEvent('stage','[演示] 进入 VAE 解码')},11800);
  demoTimer(()=>{setStage('save','演示：封装音视频');logEvent('sys','[演示] CreateVideo · SaveVideo')},13700);
  demoTimer(()=>{
    T.pct=100;T.hasPct=true;T.stage=STAGES.length;T.demoRunning=false;T.demoDone=true;T.detail='演示完成 · 输出示例已生成';
    logEvent('done','[演示] 任务完成','示例输出 demo.mp4');
    clearInterval(T.demoInterval);T.demoInterval=null;scheduleLive();toast('演示完成');
  },15300);
}

/* ---------- 任务历史 ---------- */
async function loadTasks(){
  try{
    const r=await api('/api/jobs?limit=50');
    T.jobs=r.jobs||[];
    const done=T.jobs.filter(j=>j.status==='completed').length;
    const fail=T.jobs.filter(j=>['failed','cancelled','aborted'].includes(j.status)).length;
    $('tTotal').textContent=T.jobs.length;$('tDone').textContent=done;$('tFail').textContent=fail;
    renderTaskList();
  }catch(e){}
}
function renderTaskList(){
  const box=$('taskList');if(!box)return;
  let jobs=T.jobs;
  if(T.filter==='running')jobs=jobs.filter(j=>['queued','running','in_progress'].includes(j.status));
  else if(T.filter==='completed')jobs=jobs.filter(j=>j.status==='completed');
  else if(T.filter==='failed')jobs=jobs.filter(j=>['failed','cancelled','aborted'].includes(j.status));
  if(!jobs.length){box.innerHTML='<div class="empty-state"><div class="orb"></div>没有符合条件的任务</div>';return}
  box.innerHTML=jobs.map(j=>{
    const st=j.status||'queued';const icon=st==='completed'?'✓':st==='running'?'▶':st==='failed'?'!':st==='cancelled'?'×':'◷';
    const out=(j.outputs||[])[0];const fileName=out?String(out.filename_abs||out.abs_path||out.path||'').split('\\').pop():'';
    const time=j.created_at?fmtDate(j.created_at):fmtDate(j.started_at);
    const active=j.id===T.jobId?' on':'';
    const srcTag=j.source==='comfy'?'<span style="font-size:10px;color:#0a7;margin-left:6px">[ComfyUI 实时]</span>':(j.stale?'<span style="font-size:10px;color:#c80;margin-left:6px">[本地·可能已结束]</span>':'');
    return '<div class="taskrow '+st+active+'" data-id="'+esc(j.id)+'">'+
      '<div class="ticon">'+icon+'</div>'+
      '<div class="tmain"><div class="ttop"><b>#'+esc(j.id.slice(0,8))+'</b>'+badge(st)+'<span class="tpl">'+esc(templateName(j.template))+'</span></div>'+
      '<div class="tsub">'+esc(time)+' · '+(fileName?esc(fileName):'—')+srcTag+'</div></div>'+
      '<button class="btn xs ghost" data-open="'+esc(j.id)+'">查看</button></div>';
  }).join('');
  $$('#taskList .taskrow').forEach(row=>row.onclick=e=>{if(e.target.closest('[data-open]'))e.stopPropagation();openJob(row.dataset.id)});
}
$$('#taskFilters .tfilter').forEach(el=>el.onclick=()=>{
  T.filter=el.dataset.filter;$$('#taskFilters .tfilter').forEach(x=>x.classList.toggle('on',x===el));renderTaskList();
});
async function openJob(id){
  setTab('tasks');
  try{
    const r=await api('/api/jobs/'+encodeURIComponent(id));
    const j=r.job||{};
    if(id===T.jobId){renderLive();return}
    stopDemo(true);
    T.jobId=id;T.job=j;T.output=null;T.sample=null;T.hasPct=false;T.lastLogPct=0;T.lastStatus=j.status;T.lastLogKey='';
    T.stage=j.status==='completed'?STAGES.length:(j.status==='running'?Math.max(T.stage,1):0);
    T.pct=j.status==='completed'?100:0;
    if(j.status==='completed')T.hasPct=true;
    T.detail=j.status==='completed'?'历史任务已完成':(j.status==='running'?'正在追踪历史任务…':'历史任务：'+j.status);
    T.events=[];logEvent('sys','载入历史任务','#'+id.slice(0,8)+' · '+templateName(j.template));
    if(j.status==='completed'){const out=(j.outputs||[])[0];const p=out&&(out.filename_abs||out.abs_path||out.path);if(p){T.output=p;S.curOutput=p}}
    if(j.error)logEvent('error','任务错误',String(j.error).slice(0,200));
    scheduleLive();renderTaskList();
    if(j.status==='running'||j.status==='queued'){startPoll()}
    else{clearInterval(T.pollTimer);T.pollTimer=null}
  }catch(e){toast('读取任务失败：'+e.message)}
}

/* ---------- 作品 ---------- */
function cardHTML(v){
  const nm=esc(v.name);const p=encodeURIComponent(v.path);
  const media=isImgPath(v.path)?('<img src="/api/media?path='+p+'" loading="lazy">'):('<video src="/api/media?path='+p+'" preload="metadata" muted playsinline></video>');
  return '<div class="vcard" data-path="'+esc(v.path)+'">'+media+
    '<div class="meta"><b>'+nm+'</b><span>'+fmtMB(v.mb)+'</span></div></div>';
}
function bindCards(root){root.querySelectorAll('.vcard').forEach(el=>{el.onclick=()=>openPlayer(el.dataset.path)})}
const G={items:[],offset:0,page:24,total:0,busy:false,filter:'all'};
const GALLERY_FILTERS=[['all','全部'],['image','图片'],['video','视频'],['qwen_t2i','Qwen 文生图'],['qwen_edit','Qwen 编辑'],['h3_still','H3 静帧'],['h3_video','H3 视频']];
function renderGalleryFilters(counts){
  const el=$('galleryFilters');if(!el)return;
  el.innerHTML=GALLERY_FILTERS.map(([k,label])=>{const n=(counts&&counts[k])||0;return '<span class="chip'+(G.filter===k?' on':'')+'" data-f="'+k+'">'+label+' <b>'+n+'</b></span>'}).join('');
  el.querySelectorAll('[data-f]').forEach(c=>c.onclick=()=>{if(G.filter===c.dataset.f)return;G.filter=c.dataset.f;loadGallery(true)});
}
function renderRecent(){const r=$('recent');if(!r)return;r.innerHTML=G.items.slice(0,4).map(v=>cardHTML(v)).join('');bindCards(r)}
function appendGallery(items){
  const seen=new Set(G.items.map(x=>x.path));
  for(const it of items){if(!seen.has(it.path)){G.items.push(it);seen.add(it.path)}}
  const g=$('galleryGrid');if(!g)return;
  g.innerHTML=G.items.map(v=>cardHTML(v)).join('')||'<div class="muted">暂无作品</div>';
  bindCards(g);
  const more=$('galleryMore');if(more)more.style.display=(G.items.length<G.total)?'':'none';
  const c=$('galleryCount');if(c)c.textContent=G.items.length+' / '+G.total+' · '+(GALLERY_FILTERS.find(x=>x[0]===G.filter)||['','全部'])[1];
  renderRecent();
}
async function loadGallery(reset){
  if(G.busy&&!reset)return;
  if(reset){G.items=[];G.offset=0;G.busy=false}
  G.busy=true;
  try{
    const r=await api('/api/gallery?limit='+G.page+'&offset='+G.offset+'&category='+encodeURIComponent(G.filter));
    G.total=r.total||0;
    renderGalleryFilters(r.counts||{});
    appendGallery(r.items||[]);
    G.offset=G.items.length;
  }catch(e){toast('作品加载失败：'+e.message)}
  finally{G.busy=false}
}
function renderGallery(){loadGallery(true)}
async function refreshVideos(){
  try{const v=await api('/api/videos');S.videos=v.videos||[];fillSrcSelects()}catch(e){}
  loadGallery(true);
}



/* ---------- 播放器 ---------- */
let previewList=[],previewIdx=0,imgZoom=1;
function previewListFor(path){
  const all=(typeof G!=='undefined'&&G.items)?G.items.map(x=>x.path):[];
  return all.includes(path)?all:[path];
}
function openPlayer(path){
  if(!path)return;
  previewList=previewListFor(path);
  previewIdx=Math.max(0,previewList.indexOf(path));
  $('modal').classList.add('on');
  showPreview(path);
}
function showPreview(path){
  currentModalPath=path;imgZoom=1;
  const u='/api/media?path='+encodeURIComponent(path);
  const isImg=isImgPath(path);
  $('modalName').textContent=path.split('\\').pop();
  const ctr=$('modalCounter');
  if(ctr){const nav=(previewList.length>1)?((previewIdx+1)+' / '+previewList.length+' · ← → 切换'):'';
    ctr.textContent=nav+(isImg?' · 滚轮缩放':'');}
  $('modalDl').href=u;$('modalDl').setAttribute('download',path.split('\\').pop());
  const op=$('modalOpen');if(op)op.href=u+'&inline=1';
  const im=$('modalImg'),vd=$('modalVideo');
  if(isImg){
    im.src=u;im.style.display='block';im.style.transform='scale(1)';
    vd.style.display='none';vd.pause();vd.removeAttribute('src');
  }else{
    im.style.display='none';im.removeAttribute('src');
    vd.style.display='block';vd.src=u;vd.play().catch(()=>{});
  }
}
function previewStep(d){
  if(previewList.length<2)return;
  previewIdx=(previewIdx+d+previewList.length)%previewList.length;
  showPreview(previewList[previewIdx]);
}
function setImgZoom(z){imgZoom=Math.min(4,Math.max(0.5,z));const im=$('modalImg');if(im)im.style.transform='scale('+imgZoom+')'}
function closeModal(){
  $('modal').classList.remove('on');
  const vd=$('modalVideo');vd.pause();vd.removeAttribute('src');
  const im=$('modalImg');if(im){im.removeAttribute('src');im.style.display='none';im.style.transform='scale(1)'}
  imgZoom=1;previewList=[];previewIdx=0;
}
document.addEventListener('keydown',e=>{
  const m=$('modal');if(!m||!m.classList.contains('on'))return;
  if(e.key==='Escape'){closeModal();return}
  if(e.key==='ArrowLeft'){previewStep(-1);e.preventDefault()}
  if(e.key==='ArrowRight'){previewStep(1);e.preventDefault()}
});
$('modal').addEventListener('wheel',e=>{
  const im=$('modalImg');if(!im||im.style.display==='none')return;
  e.preventDefault();setImgZoom(imgZoom*(e.deltaY<0?1.1:1/1.1));
},{passive:false});
$('modal').addEventListener('click',e=>{
  const im=$('modalImg');if(e.target!==im)return;
  setImgZoom(imgZoom>=4?1:imgZoom*1.5);
});
let currentModalPath='';

/* ---------- 超分 ---------- */
async function submitUpscale(){
  const body={src:$('uSrc').value,model:$('uModel').value,scale_out:parseFloat($('uScale').value),crf:parseInt($('uCrf').value)};
  if(!body.src){toast('请选择源视频');return}
  try{const r=await api('/api/upscale',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    toast('超分任务已开始');$('uMsg').textContent='任务 '+r.job_id;refreshUpscaleJobs();
  }catch(e){$('uMsg').textContent='失败：'+e.message;toast('超分失败')}
}
async function refreshUpscaleJobs(){
  try{const r=await api('/api/upscale/jobs?limit=20');const box=$('uJobs');box.innerHTML='';
    for(const j of (r.jobs||[])){
      const el=document.createElement('div');el.className='job';
      el.innerHTML='<span>'+esc(j.id)+'</span><span class="grow">'+esc((j.src||'').split('\\').pop())+'</span><span>'+esc(j.model)+'</span>'+badge(j.status);
      if(j.output){const b=document.createElement('button');b.className='btn sm';b.textContent='播放';b.onclick=()=>openPlayer(j.output);el.appendChild(b)}
      box.appendChild(el);
    }
    if(!(r.jobs||[]).length)box.innerHTML='<div class="muted">暂无任务</div>';
  }catch(e){}
}
function upscaleFromPath(path){if(!path){toast('没有可超分的视频');return}setTab('upscale');$('uSrc').value=path;toast('已选择源视频，选择模型后开始')}
function gotoEdit(path){setTab('edit');if(path)$('eSrc').value=path}

/* ---------- 剪辑 ---------- */
const ACTIONS={
  trim:{label:'裁剪 trim',fields:[['start_s','开始秒','number',0],['end_s','结束秒','number',5]]},
  concat:{label:'拼接 concat',fields:[['inputs','额外视频路径(每行一个)','textarea',''],['crossfade_s','交叉淡化秒','number',0]]},
  transform:{label:'缩放 transform',fields:[['width','宽','number',1280],['height','高','number',720],['pad','补边(true/false)','text','false'],['fps','帧率','number',24]]},
  mux_audio:{label:'混音 mux_audio',fields:[['audio','音频文件路径','text',''],['volume','音量','number',1.0],['offset_s','偏移秒','number',0],['replace','替换原音轨(true/false)','text','true']]},
  burn_subtitles:{label:'字幕 burn_subtitles',fields:[['srt_path','SRT 路径','text',''],['font_size','字号','number',24],['margin_v','底部边距','number',30]]},
  overlay:{label:'叠加 overlay',fields:[['image_path','图片路径','text',''],['position','位置','select','bottom-right'],['scale','缩放','number',0.2],['opacity','不透明度','number',1.0],['margin','边距','number',16]]},
  speed:{label:'变速 speed',fields:[['factor','倍速','number',1.5]]},
  extract_frames:{label:'抽帧 extract_frames',fields:[['fps','频率','number',1],['out_dir','输出目录','text','']]},
  extract_audio:{label:'抽音频 extract_audio',fields:[['dst','输出路径','text','']]},
  thumbnail:{label:'封面 thumbnail',fields:[['at_s','时间秒','number',1],['width','宽','number',640]]},
  to_gif:{label:'GIF to_gif',fields:[['fps','帧率','number',12],['width','宽','number',480]]}
};
function buildEditForm(){
  const sel=$('eAction');sel.innerHTML=Object.keys(ACTIONS).map(k=>'<option value="'+k+'">'+ACTIONS[k].label+'</option>').join('');
  sel.onchange=renderEditParams;renderEditParams();
}
function renderEditParams(){
  const act=$('eAction').value;const def=ACTIONS[act];const box=$('eParams');box.innerHTML='';
  for(const [name,label,type,val] of def.fields){
    const w=document.createElement('div');w.style.minWidth='140px';
    let inp;
    if(type==='textarea'){inp=document.createElement('textarea');inp.style.minHeight='60px'}
    else if(type==='select'){inp=document.createElement('select');inp.innerHTML='<option>top-left</option><option>top-right</option><option>bottom-left</option><option selected>bottom-right</option><option>center</option>'}
    else{inp=document.createElement('input');inp.type=type==='number'?'number':'text';if(type==='number')inp.step='any'}
    inp.value=val;inp.dataset.k=name;
    if(type==='textarea'){w.style.flex='1'}
    w.innerHTML='<label class="lab">'+label+'</label>';w.appendChild(inp);box.appendChild(w);
  }
}
async function submitEdit(){
  const act=$('eAction').value;const kw={src:$('eSrc').value};
  if(!kw.src){toast('请选择源视频');return}
  $('eParams').querySelectorAll('[data-k]').forEach(el=>{
    let v=el.value;if(v==='')return;
    if(el.type==='number')v=parseFloat(v);
    if(v==='true')v=true;else if(v==='false')v=false;
    if(el.dataset.k==='inputs'){v=v.split('\n').map(s=>s.trim()).filter(Boolean)}
    kw[el.dataset.k]=v;
  });
  $('eMsg').textContent='执行中…';
  try{const r=await api('/api/edit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:act,kwargs:kw})});
    $('eOut').textContent=JSON.stringify(r,null,1);$('eMsg').textContent=r.ok?'完成':'失败';toast(r.ok?'剪辑完成':'剪辑失败');refreshVideos();
  }catch(e){$('eMsg').textContent='失败：'+e.message;toast('剪辑失败')}
}

/* ---------- 设置 ---------- */
const NAV_ICONS={create:'✦',image:'▤',film:'🎞',tasks:'◉',canvas:'▣',gallery:'▦',projects:'▣',models:'◈',upscale:'⤢',edit:'✂',settings:'⚙'};
async function applyLang(lang){
  try{
    const r=await api('/api/i18n?lang='+encodeURIComponent(lang));
    document.title=r.strings['app.title']||document.title;
    $$('.side .item').forEach(el=>{
      const key='nav.'+el.dataset.tab;const text=r.strings[key];
      if(text)el.innerHTML='<em>'+(NAV_ICONS[el.dataset.tab]||'·')+'</em>'+text;
    });
  }catch(e){}
}
if($('notifyTest'))$('notifyTest').onclick=async()=>{try{const r=await api('/api/notify/test',{method:'POST'});$('sMsg').textContent=r.webhook_configured?'测试已发送':'未配置 Webhook'}catch(e){$('sMsg').textContent='测试失败：'+e.message}};
async function loadSettings(){
  try{const s=await api('/api/settings');const sf=(s.settings||{}).safety||{};
    $('sMinRam').value=sf.min_ram_free_gb??8;$('sTripRam').value=sf.trip_ram_free_gb??3.5;
    $('sSingle').value=String(sf.single_flight??true);
    const pc=(s.settings||{}).process_cleanup||{};
    if($('sCleanup'))$('sCleanup').checked=pc.enabled!==false;
    if($('sAutoStop'))$('sAutoStop').checked=pc.auto_stop_encode!==false;
    if($('sLang'))$('sLang').value=localStorage.getItem('asterLang')||'zh';
    if($('sWebhook'))$('sWebhook').value=((s.settings||{}).notify||{}).webhook||'';
  }catch(e){}
  try{const l=await api('/api/logs?kind=comfy_stderr&tail=12');$('sLog').textContent=l.tail||'--'}catch(e){}
}
async function saveSettings(){
  const values={safety:{min_ram_free_gb:parseFloat($('sMinRam').value),trip_ram_free_gb:parseFloat($('sTripRam').value),single_flight:$('sSingle').value==='true'},
    process_cleanup:{enabled:$('sCleanup')?$('sCleanup').checked:true,auto_stop_encode:$('sAutoStop')?$('sAutoStop').checked:true},
    ui:{lang:$('sLang')?$('sLang').value:'zh'},
    notify:{webhook:$('sWebhook')?$('sWebhook').value:''}};
  if($('sLang')){localStorage.setItem('asterLang',$('sLang').value);applyLang($('sLang').value)}
  try{const r=await api('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({values})});
    $('sMsg').textContent=r.ok?'已保存':'保存失败';toast('设置已保存');
  }catch(e){$('sMsg').textContent='失败：'+e.message}
}

/* ---------- 启动 ---------- */
async function freeModels(){
  const msg=$('freeMsg');
  if(msg)msg.textContent='释放中…';
  try{
    const r=await api('/api/free',{method:'POST'});
    if(msg)msg.textContent=r.ok?'已释放':'失败';
    toast(r.ok?'已释放 ComfyUI 模型/显存':'释放失败');
  }catch(e){
    if(msg)msg.textContent='失败';
    toast('释放失败：'+e.message);
  }
}
async function updateFilmPill(){
  try{
    const r=await api('/api/film');
    if(!r.ok||!r.state){$('filmPill').style.display='none';return}
    const s=r.state;
    const done=Object.values(s.segments||{}).filter(x=>x&&x.status==='completed').length;
    let txt=done+'/8 段';
    if(s.upscale){
      txt = s.upscale.ok ? '完成 · 4x 超分' : '60s 已拼 · 4x 超分失败';
    }else if(s.final&&s.final.ok){
      txt='60s 已拼 · 未超分';
    }else if(s.concat&&s.concat.ok){
      txt='拼接中';
    }else if(done>=8){
      txt='全部生成 · 准备拼接';
    }
    // 状态超过 6 小时未更新 → 标注「历史」，避免把旧任务显示成进行中
    const at=s.updated?Date.parse(s.updated):0;
    if(at&&Date.now()-at>6*3600*1000)txt+='（历史）';
    $('filmPill').style.display='';$('filmProg').textContent=txt;
  }catch(e){}
}
/* 右上角硬件/连接状态：独立启动，不依赖 bootstrap 成功（bootstrap 内任一渲染异常都不再拖死它） */
try{tickHw()}catch(e){console.error('tickHw init',e)}
setInterval(function(){try{tickHw()}catch(e){}},2000);
/* 右上角「当前任务」pill：实时反映 ComfyUI 队列/当前追踪任务（取代旧的成片状态位） */
async function updateTaskPill(){
  try{
    const q=await api('/api/queue');
    const running=(q.running||[]).length,pending=(q.pending||[]).length;
    const pill=$('taskStatusPill'),el=$('taskStatusText');
    if(!pill||!el)return;
    if(running>0){
      let txt='运行中';
      if(T.jobId&&['running','queued'].includes(T.job?.status)){
        txt=T.hasPct?'生成中 · '+Math.round(T.pct)+'%':'生成中';
      }
      el.textContent=(running>1?running+' 个任务 · ':'')+txt;
      pill.style.display='';
    }else if(pending>0){
      el.textContent=pending+' 个排队';pill.style.display='';
    }else if(T.jobId&&['running','queued'].includes(T.job?.status)){
      el.textContent=T.job.status==='queued'?'排队中':'生成中';pill.style.display='';
    }else{
      pill.style.display='none';
    }
  }catch(e){}
}
try{updateTaskPill()}catch(e){}
setInterval(function(){try{updateTaskPill()}catch(e){}},3000);
bootstrap().then(()=>{
  try{renderLive();renderLogs()}catch(e){console.error('render init',e)}
  setInterval(()=>{if(T.jobId&&(T.job?.status==='running'||T.job?.status==='queued'))T.elapsed++;if(T.demo&&T.demoRunning)T.elapsed++;scheduleLive()},1000);
  setInterval(()=>{if($('view-tasks')?.classList.contains('on'))loadTasks()},7000);
  setInterval(()=>{if(!T.jobId&&!T.demo)restoreActive()},8000);
  setInterval(()=>{if($('view-gallery')?.classList.contains('on'))refreshVideos()},9000);
  if(new URLSearchParams(location.search).get('demo')==='1')setTimeout(startDemo,450);
  const initTab=new URLSearchParams(location.search).get('tab');if(initTab)setTab(initTab);
}).catch(e=>toast('初始化失败：'+e.message));
