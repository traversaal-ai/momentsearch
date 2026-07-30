/* /demo — the open door: search the four shared sample talks.
 *
 * Read-only and stateless by design: no sessions, no uploads, no saved history.
 * Anonymous callers are the default tenant, which OWNS the samples, so no
 * sign-in is needed to search them.
 */
let CITES=[], SAMPLE_IDS=[];

const UNDERLINE = `<svg viewBox="0 0 320 14" preserveAspectRatio="none"><path d="M4 9C70 3 154 2 316 6"/></svg>`;
const TITLE=[{text:"A Deep Dive into"},{text:"LLMs.",coral:true}];
let _w=0;
$("#heroTitle").innerHTML = TITLE.map(p=>p.text.split(" ").map(w=>{
  const inner = p.coral ? `<span class="mark text-coral">${esc(w)}${UNDERLINE}</span>` : esc(w);
  return `<span class="word" style="--i:${_w++}">${inner}</span>`;
}).join(" ")).join(" ");

wireModal();
wireNav();   // top-right: "Sign in" or, if already signed in, "Workspace →"

/* ---------- which model is answering ---------- */
(async function loadConfig(){
  try{
    const c=await apiJSON("/api/config"); const b=$("#llmBadge");
    if(c.llm_configured){
      b.textContent="LLM: "+(c.llm_model||c.llm_provider);
      b.className="ml-auto text-xs px-3 py-1 rounded-full border border-[#cfe8d6] text-[#1f7a43]";
    } else {
      b.textContent="No LLM — moments only";
      b.className="ml-auto text-xs px-3 py-1 rounded-full border border-[#e7c46a] text-[#8a6d1a] bg-[#fbf2d8]";
    }
  }catch{ $("#llmBadge").textContent="API offline"; }
})();

/* ---------- what's in the demo ---------- */
(async function loadVideos(){
  const box=$("#videos");
  try{
    const {videos}=await apiJSON("/api/videos");
    const shown=videos.filter(v=>v.is_sample);
    SAMPLE_IDS=shown.filter(v=>v.status==="indexed").map(v=>v.id);
    if(!shown.length){
      box.innerHTML='<span class="text-muted text-sm">The sample talks index themselves on first start — they’ll appear here in a few minutes.</span>';
      return;
    }
    box.innerHTML=shown.map(v=>{
      const b=statusBadge(v);
      return `<span class="chip" style="cursor:default">
        <span class="mr-1.5 ${b.c}" title="${esc(b.label)}">${b.icon}</span>${esc(v.title||v.id)}
        <span class="${b.c}"> · ${esc(b.label)}</span></span>`;
    }).join("");
  }catch{
    box.innerHTML='<span class="text-coral2 text-sm">Couldn’t load the demo videos.</span>';
  }
})();

/* ---------- ask ---------- */
const qEl=$("#q");
function autogrow(){ qEl.style.height="auto"; qEl.style.height=Math.min(qEl.scrollHeight,160)+"px"; }
qEl.addEventListener("input",()=>{ autogrow(); ghostSync(); });
qEl.addEventListener("focus",ghostSync);
qEl.addEventListener("blur",ghostSync);
qEl.addEventListener("keydown",e=>{ if(e.key==="Enter"&&!e.shiftKey){ e.preventDefault(); ask(); }});
document.addEventListener("keydown",e=>{
  const tag=(document.activeElement||{}).tagName||"";
  if(e.key==="/" && document.activeElement!==qEl && !/^(INPUT|TEXTAREA)$/.test(tag)){
    e.preventDefault(); qEl.focus();
  }
});
$("#go").onclick=ask;

let _ctl=null;
async function ask(){
  const q=qEl.value.trim(); if(!q) return;
  if(_ctl) _ctl.abort();
  const ctl=_ctl=new AbortController();
  document.body.classList.add("asking");
  window.scrollTo({top:0, behavior: REDUCED?"auto":"smooth"});
  $("#resultsWrap").classList.remove("hidden");
  $("#article").innerHTML=`<div class="space-y-3"><div class="shimmer h-6 w-2/3 rounded"></div><div class="shimmer h-4 w-full rounded"></div><div class="shimmer h-4 w-5/6 rounded"></div></div>`;
  $("#sources").innerHTML=skeletons(3);
  $("#go").disabled=true; $("#go").textContent="…"; $("#go").classList.add("busy");
  try{
    const r=await apiJSON("/api/ask",{method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({question:q, video_ids:SAMPLE_IDS}), signal:ctl.signal});
    CITES=r.citations||[];
    let html="";
    if(r.answer) html+=`<div class="prose-body fade-in">${renderMarkdown(r.answer)}</div>`;
    if(r.note) html+=`<p class="text-xs text-muted mt-3">${esc(r.note)}</p>`;
    if(!r.answer && !CITES.length) html='<p class="text-muted">No matching moments in these talks.</p>';
    $("#article").innerHTML=html;
    $("#sources").innerHTML=CITES.map((c,i)=>momentCard(c,i)).join("");
    $$("#sources .source").forEach(el=>el.onclick=()=>openMoment(CITES,+el.dataset.n));
  }catch(e){
    if(e.name==="AbortError") return;
    $("#article").innerHTML=`<p class="text-coral2">Error: ${esc(e.message)}</p>`;
    $("#sources").innerHTML="";
  }finally{
    if(_ctl===ctl){ $("#go").disabled=false; $("#go").textContent="→"; $("#go").classList.remove("busy"); }
  }
}
function skeletons(n){
  return Array.from({length:n},()=>`
    <div class="bg-card border border-line rounded-2xl overflow-hidden">
      <div class="shimmer aspect-video"></div>
      <div class="p-3 space-y-2"><div class="shimmer h-3 w-1/3 rounded"></div><div class="shimmer h-3 w-4/5 rounded"></div></div>
    </div>`).join("");
}
/* [n] pills in the answer open their moment */
$("#article").addEventListener("click",e=>{
  const c=e.target.closest(".cite"); if(c) openMoment(CITES,+c.dataset.n);
});

/* ---------- typewriter placeholder + example chips ---------- */
const EXAMPLES=["a diagram of the attention mechanism","an animation of a neural network",
  "a slide listing examples of large language models","a person speaking on stage next to slides"];
function ghostIdle(){ return !qEl.value && document.activeElement!==qEl; }
function ghostSync(){ $("#ghost").classList.toggle("hide", !ghostIdle()); }
(function typewriter(){
  const ghost=$("#ghost");
  if(REDUCED){ ghost.textContent="Ask about anything shown on screen…"; return; }
  const caret='<span class="caret"></span>';
  let i=0, ch=0, dir=1;
  (function tick(){
    if(!ghostIdle()) return setTimeout(tick,400);
    const full=EXAMPLES[i%EXAMPLES.length];
    ch+=dir;
    ghost.innerHTML=esc(full.slice(0,Math.max(ch,0)))+caret;
    if(dir>0&&ch>=full.length){ dir=-1; return setTimeout(tick,2000); }
    if(dir<0&&ch<=0){ dir=1; i++; return setTimeout(tick,320); }
    setTimeout(tick, dir>0?46:22);
  })();
})();

$("#examples").innerHTML=EXAMPLES.map(q=>`<span class="chip">${esc(q)}</span>`).join("");
$$("#examples .chip").forEach(c=>c.onclick=()=>{
  qEl.value=c.textContent; autogrow(); ghostSync(); ask();
});
