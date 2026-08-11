/* /app — three places, not a three-step tunnel:
 *
 *   1 SET UP    name the session, add videos (as many as you like). Each one
 *               starts processing the moment its bytes land. You leave when you
 *               press Next — nothing drags you away mid-upload.
 *   2 PROCESSING the queue drawn stage by stage. Auto-advances to Ask as soon as
 *               the FIRST video is searchable; there's nothing to wait for.
 *   3 ASK       the real workspace, independent of the setup flow: sessions on
 *               the left, search in the middle, this session's videos on the
 *               right — the processing ones included, so you can ask what's
 *               ready while the rest catches up.
 *
 * NAVIGATION IS YOURS. Opening /app always lands on SET UP — the flow from the
 * top — and the only way into a session's Ask page is to click that session,
 * which opens it on whichever page its own state has earned (openStep). The one
 * automatic move in the whole app is 2 → 3 when the first video becomes
 * searchable, and that only fires while you're watching page 2.
 *
 * There is no "onboarded" flag anywhere; page state is always derived from the
 * session, so nothing can go stale.
 *
 * One question at a time. Asking REPLACES the answer on screen — the server
 * still stores every turn with its citations, this UI just never shows a log.
 *
 * Single-user, so nothing gates this page (see ui/common.js).
 */
let SESSIONS=[], CUR=null;
let POLL=null, MAXMB=2048;
let SELECTED=new Set();          // which ready videos a question searches
let STEP=1;
let SHOWN=null;                  // the one answer on screen ({q, message}) or null
let UPLOADING=0;                 // in-flight browser uploads (blocks Next)

wireModal();
$("#avatar").textContent=USER_NAME.trim()[0].toUpperCase();
$("#whoName").textContent=USER_NAME;

const ready   = ()=> CUR ? CUR.videos.filter(v=>v.status==="indexed") : [];
const working = ()=> CUR ? CUR.videos.filter(v=>INFLIGHT.includes(v.status)) : [];
const failed  = ()=> CUR ? CUR.videos.filter(v=>v.status==="failed") : [];

/* Where a session opens. Something searchable → straight to Ask; work in
   flight → Processing; nothing at all → Set up. */
function openStep(){
  if(!CUR) return 1;
  if(ready().length) return 3;
  if(CUR.videos.length) return 2;
  return 1;
}
/* You can't visit a page the session hasn't earned: Processing needs something
   to process, Ask needs something searchable. */
function reachable(n){
  if(n===1) return true;
  if(n===2) return !!(CUR && CUR.videos.length);
  return ready().length>0;
}

function setStep(n){
  if(!reachable(n)) return;
  STEP=n;
  document.body.dataset.step=String(n);
  renderNav();
  if(n===1) renderStep1();
  if(n===2) renderPipeline();
  if(n===3){ renderAskVideos(); renderScopeNote(); renderStarters(); }
}

/* Three states, told apart without relying on colour alone: current is filled,
   reached is outlined in coral, unreached is dim and inert. The numerals stay
   numerals — swapping them for glyphs once you'd finished onboarding just made
   the nav look like it had failed to render. */
function renderNav(){
  $$(".stepBtn").forEach(b=>{
    const n=+b.dataset.step, ok=reachable(n);
    b.disabled=!ok;
    b.dataset.state = n===STEP ? "current" : (ok ? "done" : "ahead");
    b.setAttribute("aria-current", n===STEP ? "page" : "false");
  });
  const w=working().length;
  $("#navSub2").textContent = w ? `${w} in the queue` : "queue and progress";
}
$$(".stepBtn").forEach(b=>b.onclick=()=>{ if(!b.disabled) setStep(+b.dataset.step); });

/* ---------- which model is answering ---------- */
(async function loadConfig(){
  try{
    const c=await apiJSON("/api/config"); const b=$("#llmBadge");
    MAXMB=c.max_upload_mb||MAXMB;
    $("#upLimit").textContent=`up to ${MAXMB} MB each`;
    if(c.llm_configured){
      b.textContent="LLM: "+(c.llm_model||c.llm_provider);
      b.className="ml-auto text-xs px-3 py-1 rounded-full border border-[#cfe8d6] text-[#1f7a43] hidden sm:inline";
    } else {
      b.textContent="No LLM — moments only";
      b.className="ml-auto text-xs px-3 py-1 rounded-full border border-[#e7c46a] text-[#8a6d1a] bg-[#fbf2d8] hidden sm:inline";
    }
  }catch{ $("#llmBadge").textContent="API offline"; }
})();

/* ---------- sessions ---------- */
/* Which session to reopen when you come back.
 *
 * NOT simply the newest one. Sessions are ordered by updated_at, so creating a
 * session and walking away left an EMPTY session at row 0 — and every later
 * visit reopened it and asked you to add videos again, with the work you'd
 * already indexed nowhere in sight. So: the one you were last in, else the most
 * recent one that actually holds videos, and only then the newest.
 *
 * localStorage here is a UI preference (which folder was I in), not identity —
 * there is no session/auth state to keep, see ui/common.js. */
const LAST_KEY="ms_last_session";
function rememberSession(id){ try{ localStorage.setItem(LAST_KEY,id); }catch{} }
function savedSession(){ try{ return localStorage.getItem(LAST_KEY); }catch{ return null; } }

/* Re-read the list WITHOUT navigating. Counts change constantly (a video
   finishes, one is removed), and repainting the sidebar must never move you. */
async function refreshSessions(){
  const d=await apiJSON("/api/sessions");
  SESSIONS=d.sessions||[];
  renderSessionList();
  return SESSIONS;
}

/* Arriving at /app ALWAYS opens the setup page — the full 1-2-3 flow from the
 * top. It used to render page 1 and then jump to the Ask page a beat later once
 * the session loaded, which read as a blink that threw you somewhere you didn't
 * ask to go. Now there is no jump: the only way into a session's Ask page is to
 * click that session.
 *
 * The session it lands on is a real, writable one — never the read-only demo,
 * because the name box and the upload target on this page belong to it. If no
 * own session exists (you deleted them all), one is created rather than leaving
 * the page pointing at nothing. */
async function boot(){
  await refreshSessions();
  let own=SESSIONS.filter(s=>s.kind!=="demo");
  if(!own.length){
    await apiJSON("/api/sessions",{method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({title:"New session"})});
    own=(await refreshSessions()).filter(s=>s.kind!=="demo");
  }
  const saved=savedSession();
  const id=(saved && own.some(s=>s.id===saved)) ? saved : own[0].id;
  await openSession(id, 1);            // 1 = the setup page, always
}

/* A row is a div, not a button, because it holds its own delete control — a
   <button> inside a <button> is invalid HTML and the nested click target is
   unreliable. The demo session gets no ✕: it's the shared read-only corpus and
   the samples ride in it. */
function sessionRow(s){
  const on = CUR && s.id===CUR.id;
  return `<div class="sessRow group relative rounded-lg border transition ${
    on ? "bg-card border-coral" : "bg-transparent border-transparent hover:bg-card hover:border-line"
  }">
    <button class="sessBtn w-full text-left px-2.5 py-2 pr-8" data-id="${esc(s.id)}">
      <div class="flex items-center gap-1.5">
        <span class="text-[12.5px] font-600 truncate">${esc(s.title)}</span>
        ${s.kind==="demo" ? '<span class="text-[9px] px-1 rounded bg-coral/15 text-coral2 shrink-0">demo</span>' : ''}
      </div>
      <div class="text-[10.5px] text-muted mt-0.5">${s.video_count} video${s.video_count==1?"":"s"}</div>
    </button>
    ${s.kind==="demo" ? "" : `
      <!-- Visible at rest, not hover-only: a hover-revealed control does not
           exist at all on a touch screen. -->
      <button class="sessDel absolute right-1 top-1/2 -translate-y-1/2 w-6 h-6 rounded-md text-muted hover:text-coral2 hover:bg-paper2 opacity-40 group-hover:opacity-100 focus:opacity-100 transition text-[11px]"
        data-id="${esc(s.id)}" data-title="${esc(s.title)}"
        title="Delete “${esc(s.title)}” and its videos">✕</button>`}
  </div>`;
}
function renderSessionList(){
  const html=SESSIONS.map(sessionRow).join("");
  $("#sessionList").innerHTML=html;       // Ask page rail (wide screens)
  $("#sessionListMenu").innerHTML=html;   // header dropdown (every page, every width)
  $("#sessMenuLabel").textContent =
    SESSIONS.length>1 ? `Sessions · ${SESSIONS.length}` : "Sessions";
  // Clicking a session is the ONLY way into its Ask page — no forceStep, so it
  // opens on whatever page that session has earned.
  $$(".sessBtn").forEach(b=>b.onclick=()=>{
    $("#sessMenu").open=false;
    openSession(b.dataset.id);
  });
  $$(".sessDel").forEach(b=>b.onclick=e=>{
    e.stopPropagation();                  // don't also open the row
    deleteSession(b.dataset.id, b.dataset.title);
  });
}

/* forceStep pins the page (boot passes 1). Left out, the session opens on
   whichever page its own state earns — which is what clicking a session in the
   list should do: land you in the Ask page when it has something searchable. */
let _openSeq=0;
async function openSession(id, forceStep){
  // Last CLICK wins, not last HTTP response. Two opens can overlap — clicking a
  // session while boot's own open is still in flight is the easy way to do it —
  // and without this guard the slower response lands last and silently drags you
  // back to the session you just left.
  const seq=++_openSeq;
  const data=await apiJSON("/api/sessions/"+encodeURIComponent(id));
  if(seq!==_openSeq) return;        // superseded; drop this one entirely
  CUR=data;
  rememberSession(CUR.id);          // so coming back selects THIS one
  // Every ready video is in scope on open; uncheck what you want left out. No
  // leftover answer: this is a search box, so it opens empty rather than
  // resuming a transcript.
  SELECTED=new Set(ready().map(v=>v.id));
  clearAnswer();
  setName(CUR.title);
  $("#demoTag").classList.toggle("hidden", CUR.kind!=="demo");
  renderSessionList();
  setStep(forceStep || openStep());
  schedulePoll();
}

/* Nothing asked yet vs. an answer on screen — drives the vertical centring of
   the search column (app.css). */
function clearAnswer(){
  SHOWN=null;
  $("#answer").innerHTML="";
  $("#askHead").classList.remove("hidden");
  document.body.dataset.ans="0";
}

/* ---------- the session's name ----------
   Every .sessName input edits the same field and saves through one path, so the
   header box and the setup box can't drift apart. The header one also tracks its
   own width, so the `demo` chip stays next to the name. */
function setName(t){
  $$(".sessName").forEach(el=>{
    el.value=t;
    if(el.hasAttribute("size")) el.size=Math.max(8, Math.min(24, t.length+1));
  });
}

let _savedTitle="";
$$(".sessName").forEach(el=>{
  el.addEventListener("focus",()=>{ _savedTitle=el.value; });
  el.addEventListener("keydown",e=>{
    if(e.key==="Enter"){ e.preventDefault(); el.blur(); }
    if(e.key==="Escape"){ el.value=_savedTitle; el.blur(); }
  });
  el.addEventListener("blur",()=>saveTitle(el));
});
async function saveTitle(el){
  const t=el.value.trim();
  if(!CUR || !t || t===CUR.title){ if(CUR) el.value=CUR.title; return; }
  try{
    await apiJSON("/api/sessions/"+encodeURIComponent(CUR.id),{method:"PATCH",
      headers:{"Content-Type":"application/json"}, body:JSON.stringify({title:t})});
    CUR.title=t;
    const s=SESSIONS.find(x=>x.id===CUR.id); if(s) s.title=t;
    setName(t);
    renderSessionList();
    const tag=$("#titleSaved");                    // a quiet confirmation, then gone
    tag.style.opacity="1"; setTimeout(()=>{ tag.style.opacity="0"; }, 1200);
  }catch(e){ el.value=CUR.title; $("#ingestStatus").textContent="Couldn't rename: "+e.message; }
}

/* Both live in two places now (header dropdown + Ask rail), so wire by class. */
$$(".newSessionBtn").forEach(b=>b.onclick=async()=>{
  $("#sessMenu").open=false;
  // Reuse an existing empty session rather than stacking up more. Clicking
  // "+ New" twice used to leave two untouched "New session" rows sitting at the
  // top of the list, which is what hijacked the landing page.
  const spare=SESSIONS.find(s=>s.kind!=="demo" && (s.video_count||0)===0);
  let id=spare && spare.id;
  if(!id){
    const s=await apiJSON("/api/sessions",{method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({title:"New session"})});
    await refreshSessions();
    id=s.id;
  }
  await openSession(id, 1);          // a new session starts at Set up
  $("#nameBox").focus(); $("#nameBox").select();
});

/* Delete the session you're in (header dropdown + Ask rail). */
$$(".deleteSessionBtn").forEach(b=>b.onclick=()=>{ if(CUR) deleteSession(CUR.id, CUR.title); });

/* One place for the confirm + the aftermath, shared by the buttons above and the
   per-row ✕ in every session list. */
async function deleteSession(id, title){
  $("#sessMenu").open=false;
  if(!confirm(`Delete “${title}”?\n\nThis deletes the session and its videos — their frames, transcript and search index — unless a video is also in another session, which keeps it.\n\nThis can't be undone.`)) return;
  const wasCurrent = CUR && CUR.id===id;
  try{
    await apiJSON("/api/sessions/"+encodeURIComponent(id),{method:"DELETE"});
  }catch(e){ alert("Couldn't delete: "+e.message); return; }
  if(wasCurrent){
    CUR=null;
    await boot();                    // pick another session, land on Set up
  }else{
    await refreshSessions();         // deleting some OTHER session must not move you
  }
}

$("#addMore").onclick=()=>setStep(1);

/* The answer to "I want to see this work but I have no video to hand". */
$("#goSamples").onclick=()=>{
  const demo=SESSIONS.find(s=>s.kind==="demo");
  if(demo) openSession(demo.id);
};

/* ---------- page 1 ---------- */
function renderStep1(){
  const hatch=$("#sampleHatch");
  hatch.classList.toggle("hidden", !SESSIONS.some(s=>s.kind==="demo") || CUR?.kind==="demo");
  renderStep1Videos();
  renderStep1Foot();
  renderStep1Existing();
}

/* Sessions that already hold videos, offered right here. Without this, arriving
   on an empty session meant the only visible action was "add a video" even when
   you had indexed work one click away. */
function renderStep1Existing(){
  const box=$("#step1Existing");
  const others=SESSIONS.filter(s=>s.id!==CUR?.id && (s.video_count||0)>0);
  if(!others.length){ box.innerHTML=""; box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  box.innerHTML=`
    <div class="text-[11px] uppercase tracking-wider text-muted font-semibold mb-2">
      Or open a session you've already built
    </div>
    <div class="space-y-2">
      ${others.map(s=>`
        <button class="goSess w-full text-left bg-card border border-line hover:border-coral rounded-xl px-3.5 py-3 transition flex items-center gap-3" data-id="${esc(s.id)}">
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-1.5">
              <span class="text-[13.5px] font-600 truncate">${esc(s.title)}</span>
              ${s.kind==="demo"?'<span class="text-[9px] px-1 rounded bg-coral/15 text-coral2 shrink-0">demo</span>':""}
            </div>
            <div class="text-[11.5px] text-muted mt-0.5">${s.video_count} video${s.video_count==1?"":"s"} · ready to ask</div>
          </div>
          <span class="text-coral2 text-[13px] font-semibold shrink-0">Open →</span>
        </button>`).join("")}
    </div>`;
  $$("#step1Existing .goSess").forEach(b=>b.onclick=()=>openSession(b.dataset.id));
}

function renderStep1Videos(){
  const box=$("#step1Videos");
  // Drop the top margin when there's nothing here, so an empty session doesn't
  // show a stack of blank gaps between the drop zone and the Next button.
  box.classList.toggle("mt-7", !!(CUR && CUR.videos.length));
  if(!CUR || !CUR.videos.length){ box.innerHTML=""; return; }
  const n=CUR.videos.length;
  box.innerHTML=`
    <div class="text-[11px] uppercase tracking-wider text-muted font-semibold mb-2">
      ${n} video${n==1?"":"s"} in this session
    </div>
    <div class="space-y-2">${CUR.videos.map(v=>videoRow(v,{compact:true})).join("")}</div>`;
  wireVideoRows(box);
}

/* Next is a real decision point, which is why it's a button and not a redirect:
   uploads keep running while you're here, and leaving mid-batch is what hid the
   progress rows before. Disabled while bytes are still moving. */
function renderStep1Foot(){
  const box=$("#step1Foot");
  if(!CUR || !CUR.videos.length){
    box.innerHTML=`<p class="text-[12.5px] text-muted text-center">Add at least one video to continue.</p>`;
    return;
  }
  const r=ready().length, w=working().length;
  const label = r ? `Next: ask a question →`
                  : `Next: watch ${w||CUR.videos.length} process →`;
  box.innerHTML=`
    <button id="next1" class="w-full bg-coral hover:bg-coral2 disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold rounded-xl px-6 py-3.5 transition"
      ${UPLOADING?"disabled":""}>${UPLOADING?`Uploading ${UPLOADING} file${UPLOADING==1?"":"s"}…`:label}</button>
    <p class="text-[11.5px] text-muted text-center mt-2">
      ${UPLOADING ? "You can keep adding videos — this unlocks when the uploads finish."
                  : "Processing already started. Adding more later is one click from the Ask page."}
    </p>`;
  const b=$("#next1");
  if(b) b.onclick=()=>setStep(r?3:2);
}

/* ---------- video rows ---------- */
function videoRow(v,{compact}={}){
  const b=statusBadge(v);
  const yid=v.id.startsWith("yt_") ? v.id.slice(3) : null;
  const thumb=yid
    ? `<img loading="lazy" src="https://img.youtube.com/vi/${esc(yid)}/default.jpg" class="w-14 h-9 object-cover rounded-md border border-line shrink-0" onerror="this.style.opacity=0">`
    : `<div class="w-14 h-9 rounded-md border border-line bg-paper2 shrink-0"></div>`;
  const busy=INFLIGHT.includes(v.status);
  const pct=v.progress ? Math.round(v.progress*100) : null;
  const mini = busy && !compact
    ? `<div class="h-1 bg-paper2 rounded-full mt-1.5 overflow-hidden">
         <div class="h-full bg-coral rounded-full ${pct===null?"rail indet":""}" style="width:${pct===null?38:pct}%"></div>
       </div>` : "";
  return `<div class="bg-card border border-line rounded-xl p-2.5 flex gap-2.5 items-center">
    ${thumb}
    <div class="min-w-0 flex-1">
      <div class="text-[12.5px] font-600 leading-snug line-clamp-2">${esc(v.title||v.id)}</div>
      <div class="text-[11px] ${b.c} mt-0.5">${b.icon} ${esc(b.label)}${pct!==null&&busy?` · ${pct}%`:""}</div>
      ${mini}
    </div>
    <div class="flex gap-1 shrink-0">
      ${v.status==="failed"?`<button class="text-muted hover:text-coral2 text-xs" data-retry="${esc(v.id)}" title="Retry">↻</button>`:""}
      <button class="text-muted hover:text-coral2 text-xs" data-remove="${esc(v.id)}" title="Remove from this session (keeps the video in your workspace)">✕</button>
      ${v.is_sample?"":`<button class="text-muted hover:text-coral2 text-xs" data-del="${esc(v.id)}" data-title="${esc(v.title||v.id)}" title="Delete permanently — removes frames &amp; search vectors everywhere">🗑</button>`}
    </div>
  </div>`;
}

function wireVideoRows(box){
  box.querySelectorAll("[data-remove]").forEach(b=>b.onclick=async()=>{
    await apiJSON(`/api/sessions/${encodeURIComponent(CUR.id)}/videos/${encodeURIComponent(b.dataset.remove)}`,
      {method:"DELETE"});
    await refresh(); refreshSessions();
  });
  box.querySelectorAll("[data-retry]").forEach(b=>b.onclick=async()=>{
    await api("/api/videos/"+encodeURIComponent(b.dataset.retry)+"/retry",{method:"POST"});
    await refresh(); schedulePoll();
  });
  box.querySelectorAll("[data-del]").forEach(b=>b.onclick=async()=>{
    if(!confirm(`Delete “${b.dataset.title}” permanently?\n\nThis removes its frames and search vectors from your whole workspace. This can’t be undone.`)) return;
    try{ await apiJSON("/api/videos/"+encodeURIComponent(b.dataset.del),{method:"DELETE"}); }
    catch(e){ alert(e.message); return; }
    await refresh(); refreshSessions();
  });
}

/* Re-read this session and repaint whatever page is open, without moving you. */
async function refresh(){
  if(!CUR) return;
  const fresh=await apiJSON("/api/sessions/"+encodeURIComponent(CUR.id));
  CUR.videos=fresh.videos;
  ready().forEach(v=>SELECTED.add(v.id));   // newly-ready joins the search
  renderNav();
  if(STEP===1) renderStep1();
  if(STEP===2) renderPipeline();
  if(STEP===3){ renderAskVideos(); renderScopeNote(); renderStarters(); }
}

/* ---------- page 2: the pipeline, drawn ----------
   Which stage, and how far into it — the difference between a wait you can
   trust and a spinner. */
const STAGES=[
  {nm:"queued",  has:["pending","queued"]},
  {nm:"fetch",   has:["fetching"]},
  {nm:"frames",  has:["sampling"]},
  {nm:"embed",   has:["embedding"]},
];
function stageIndex(status){
  const i=STAGES.findIndex(s=>s.has.includes(status));
  return i<0 ? STAGES.length : i;              // indexed/skipped = past the end
}

function renderPipeline(){
  if(!CUR) return;
  const w=working(), f=failed(), r=ready();
  $("#step2Sub").textContent = w.length
    ? `Sampling frames, embedding them and pulling the transcript. You can leave this page — it keeps going.`
    : f.length && !r.length
      ? `Nothing finished indexing. Here's what happened.`
      : r.length ? `All done.` : `Nothing is being processed right now.`;

  $("#pipeline").innerHTML=CUR.videos.map(v=>{
    const cur=stageIndex(v.status);
    const done=v.status==="indexed"||v.status==="skipped";
    const pct=v.progress ? Math.round(v.progress*100) : null;
    const track=STAGES.map((s,i)=>{
      const on = done||i<cur ? "done" : (i===cur ? "now" : "ahead");
      const indet = on==="now" && pct===null ? " data-indet" : "";
      const style = on==="now" && pct!==null ? ` style="--p:${pct}%"` : "";
      return `<span class="stage" data-on="${on}"${indet}${style}>
        <span class="bar"><i></i></span><span class="nm">${s.nm}</span>
      </span>`;
    }).join("");
    const b=statusBadge(v);
    return `<div class="bg-card border border-line rounded-2xl p-4">
      <div class="flex items-center gap-2">
        <div class="text-[13.5px] font-600 leading-snug line-clamp-1 min-w-0 flex-1">${esc(v.title||v.id)}</div>
        <div class="text-[11.5px] ${b.c} shrink-0">${b.icon} ${esc(b.label)}${pct!==null&&!done?` · ${pct}%`:""}</div>
      </div>
      ${v.status==="failed"
        ? `<div class="mt-3 text-[12.5px] text-coral2 leading-relaxed">${esc(v.error||"Indexing failed.")}</div>
           <button class="mt-2 text-[12px] font-semibold text-coral2 hover:text-coral underline decoration-line" data-retry="${esc(v.id)}">Try again</button>`
        : `<div class="flex gap-2 mt-3">${track}</div>`}
    </div>`;
  }).join("");
  wireVideoRows($("#pipeline"));

  $("#step2Foot").innerHTML=`
    <button id="back1" class="text-[13px] text-muted hover:text-ink underline decoration-line">← add more videos</button>
    ${r.length?`<button id="toAsk" class="bg-coral hover:bg-coral2 text-white font-semibold rounded-xl px-6 py-3 transition">
        ${r.length} ready — ask a question →</button>`:""}`;
  const a=$("#toAsk"), b2=$("#back1");
  if(a) a.onclick=()=>setStep(3);
  if(b2) b2.onclick=()=>setStep(1);
}

/* ---------- page 3: the videos rail (ready AND processing) ---------- */
function renderAskVideos(){
  if(!CUR) return;
  const r=ready(), w=working(), f=failed();
  const section=(label,items,extra)=> items.length ? `
    <div>
      <div class="text-[10px] uppercase tracking-wider text-muted font-semibold mb-1.5">${label}</div>
      <div class="space-y-2">${items.map(extra).join("")}</div>
    </div>` : "";

  const html =
    section(`Searchable · ${r.length}`, r, v=>`
      <label class="bg-card border border-line rounded-xl p-2.5 flex gap-2 items-center cursor-pointer">
        <input type="checkbox" class="vidsel accent-coral w-4 h-4 shrink-0" data-sel="${esc(v.id)}" ${SELECTED.has(v.id)?"checked":""}
          title="Include this video in the search (unchecking doesn't delete it)">
        <div class="min-w-0 flex-1">
          <div class="text-[12px] font-600 leading-snug line-clamp-2 text-ink">${esc(v.title||v.id)}</div>
          <div class="text-[10.5px] text-[#1f7a43] mt-0.5">✓ ${v.frame_count||0} frames</div>
        </div>
      </label>`) +
    (w.length ? `<div class="pt-1">${section(`Processing · ${w.length}`, w, v=>videoRow(v))}
       <p class="text-[10.5px] text-muted mt-2 leading-relaxed">
         These join the search automatically the moment they finish — you can keep asking meanwhile.
       </p></div>` : "") +
    (f.length ? `<div class="pt-1">${section(`Failed · ${f.length}`, f, v=>videoRow(v))}</div>` : "") +
    (CUR.videos.length ? "" : `<p class="text-[12px] text-muted">No videos in this session yet.</p>`);

  // Rail on wide screens, collapsible list on phones — one render, two homes.
  $("#askVideos").innerHTML=html;
  $("#askVideosSmall").innerHTML=html;
  $("#vidMenuLabel").textContent=`Videos · ${r.length} searchable${w.length?`, ${w.length} processing`:""}`;

  // Both copies are live, and a checkbox in one has to move the other.
  $$(".vidsel").forEach(c=>c.onchange=()=>{
    if(c.checked) SELECTED.add(c.dataset.sel); else SELECTED.delete(c.dataset.sel);
    $$(`.vidsel[data-sel="${c.dataset.sel}"]`).forEach(o=>{ o.checked=c.checked; });
    renderScopeNote();
  });
  wireVideoRows($("#askVideos"));
  wireVideoRows($("#askVideosSmall"));
}

function renderScopeNote(){
  const r=ready(), sel=r.filter(v=>SELECTED.has(v.id)).length, w=working().length;
  const scope = !r.length ? "Nothing searchable yet"
    : sel===0 ? "No videos checked — check one on the right to search"
    : sel===r.length ? `Searching all ${r.length} video${r.length==1?"":"s"}`
    : `Searching ${sel} of ${r.length} videos`;
  $("#scopeNote").textContent = scope + (w ? ` · ${w} still processing` : "");
}

/* Starters exist for the blank page, so they go once there's an answer. */
function renderStarters(){
  const box=$("#starters");
  if(SHOWN || !ready().length){ box.innerHTML=""; return; }
  const S=["What is this about?","Show me a diagram","What's on the busiest slide?"];
  box.innerHTML=S.map(s=>`<span class="chip">${esc(s)}</span>`).join("");
  $$("#starters .chip").forEach(c=>c.onclick=()=>{ qEl.value=c.textContent; ghostSync(); send(); });
}

/* ---------- asking ---------- */
const qEl=$("#q");
function autogrow(){ qEl.style.height="auto"; qEl.style.height=Math.min(qEl.scrollHeight,150)+"px"; }
qEl.addEventListener("input",()=>{ autogrow(); ghostSync(); });
qEl.addEventListener("focus",ghostSync);
qEl.addEventListener("blur",ghostSync);
qEl.addEventListener("keydown",e=>{ if(e.key==="Enter"&&!e.shiftKey){ e.preventDefault(); send(); }});
$("#go").onclick=send;

/* What each server stage is called on screen. Plain language about what is
   happening to YOUR question — not the internal stage name. The server emits
   these as each stage actually begins (src/rag/search.py::on_stage), so the line
   you're looking at is genuinely the step being worked on. */
const STAGE_WORDS={
  embedding: "Understanding your question",
  searching: "Searching what's on screen and what's said",
  ranking:   "Picking the strongest moments",
  reading:   "Opening those moments",
  answering: "Writing the answer with citations",
};
let ASK_STAGES=[];          // [{key, label, detail, ms}] in the order they arrived

function renderStages(done){
  const box=$("#stages"); if(!box) return;
  box.innerHTML=ASK_STAGES.map((s,i)=>{
    const last = i===ASK_STAGES.length-1;
    const busy = last && !done;
    const icon = busy
      ? `<span class="dotPulse"></span>`
      : `<span class="text-[#1f7a43] text-[11px]">✓</span>`;
    return `<div class="flex items-center gap-2 ${busy?"":"opacity-60"}">
      <span class="w-4 flex justify-center shrink-0">${icon}</span>
      <span class="text-[12.5px] ${busy?"text-ink font-600":"text-muted"}">${esc(s.label)}</span>
      ${s.detail?`<span class="text-[11px] text-muted truncate">· ${esc(s.detail)}</span>`:""}
      ${s.ms!=null?`<span class="text-[11px] text-muted ml-auto tabular-nums shrink-0">${(s.ms/1000).toFixed(1)}s</span>`:""}
    </div>`;
  }).join("");
}

/* Ask over SSE so the stage list is live.
 *
 * fetch + a stream reader rather than EventSource, because EventSource can only
 * GET and the question travels in a POST body. Falls back to the plain /ask
 * endpoint if streaming is unavailable for any reason — the answer is what
 * matters; the progress display is a bonus and must never be the thing that
 * breaks asking. */
async function askStreaming(q){
  const body=JSON.stringify({question:q, video_ids:[...SELECTED]});
  const url=`/api/sessions/${encodeURIComponent(CUR.id)}/ask_stream`;
  let r;
  try{
    r=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body});
  }catch(e){ r=null; }
  if(!r || !r.ok || !r.body){
    // 404 (older server), a proxy that ate the stream, anything: just ask plainly.
    return apiJSON(`/api/sessions/${encodeURIComponent(CUR.id)}/ask`,
      {method:"POST",headers:{"Content-Type":"application/json"},body});
  }

  const reader=r.body.getReader(), dec=new TextDecoder();
  let buf="", started=0, out=null, err=null;
  for(;;){
    const {value,done}=await reader.read();
    if(done) break;
    buf+=dec.decode(value,{stream:true});
    // Frames are separated by a blank line; keep any partial frame in `buf`.
    const frames=buf.split("\n\n"); buf=frames.pop();
    for(const f of frames){
      const line=f.split("\n").find(l=>l.startsWith("data:"));
      if(!line) continue;
      let ev; try{ ev=JSON.parse(line.slice(5).trim()); }catch{ continue; }
      if(ev.type==="stage"){
        const now=performance.now();
        if(ASK_STAGES.length) ASK_STAGES[ASK_STAGES.length-1].ms=now-started;  // time the one just finished
        started=now;
        ASK_STAGES.push({key:ev.stage, label:STAGE_WORDS[ev.stage]||ev.stage, detail:ev.detail||"", ms:null});
        renderStages(false);
      }else if(ev.type==="done"){
        if(ASK_STAGES.length) ASK_STAGES[ASK_STAGES.length-1].ms=performance.now()-started;
        renderStages(true);
        out=ev;
      }else if(ev.type==="error"){
        err=ev.detail||"The server couldn't answer that.";
      }
    }
  }
  if(err) throw new Error(err);
  if(!out) throw new Error("The answer stream ended before an answer arrived.");
  return out;
}

let _sending=false;
async function send(){
  const q=qEl.value.trim();
  if(!q || !CUR || _sending) return;
  _sending=true;
  qEl.value=""; autogrow(); ghostSync();
  $("#go").disabled=true; $("#go").classList.add("busy");
  $("#askHead").classList.add("hidden");     // the answer gets the room
  $("#starters").innerHTML="";
  document.body.dataset.ans="1";             // search lifts to the top
  ASK_STAGES=[];
  $("#answer").innerHTML=`
    <div class="ans max-w-3xl mx-auto pt-2">
      <div class="text-[13px] text-muted mb-4">${esc(q)}</div>
      <div id="stages" class="bg-card border border-line rounded-2xl p-4 space-y-2.5 mb-5"></div>
      <div class="space-y-3">
        <div class="shimmer h-4 w-2/3 rounded"></div>
        <div class="shimmer h-4 w-full rounded"></div>
        <div class="shimmer h-4 w-4/6 rounded"></div>
      </div>
    </div>`;
  try{
    const d=await askStreaming(q);
    SHOWN={q, message:d.message};
    renderAnswer();
  }catch(e){
    // Setup problems (a missing embedder is the common one on a fresh clone)
    // come back as prose with a fix in it — give it room to be read.
    SHOWN=null;
    $("#answer").innerHTML=`<div class="ans max-w-3xl mx-auto pt-2">
      <div class="text-[13px] text-muted mb-3">${esc(q)}</div>
      <div class="bg-[#fbf2d8] border border-[#e7c46a] rounded-2xl p-4">
        <div class="text-[13px] font-600 text-[#8a6d1a] mb-1">Couldn't answer that</div>
        <p class="text-[13px] text-[#6b5a1a] leading-relaxed">${esc(e.message)}</p>
      </div>
    </div>`;
    $("#askHead").classList.remove("hidden");
  }finally{
    _sending=false;
    $("#go").disabled=false; $("#go").classList.remove("busy");
  }
}

function renderAnswer(){
  if(!SHOWN){ clearAnswer(); return; }
  document.body.dataset.ans="1";
  const m=SHOWN.message, cites=m.citations||[], note=(m.meta||{}).note;
  $("#answer").innerHTML=`
    <div class="ans max-w-3xl mx-auto pt-2">
      <div class="flex items-baseline gap-2 mb-4">
        <span class="text-[10px] uppercase tracking-wider text-muted font-semibold shrink-0">You asked</span>
        <span class="text-[13.5px] font-600">${esc(SHOWN.q)}</span>
      </div>
      <div class="prose-body text-[15px]">${renderMarkdown(m.content)}</div>
      ${note?`<p class="text-[11px] text-muted mt-2">${esc(note)}</p>`:""}
      ${cites.length?`
        <div class="text-[11px] uppercase tracking-wider text-muted font-semibold mt-6 mb-2">Moments</div>
        <div class="grid sm:grid-cols-2 xl:grid-cols-3 gap-3">${cites.map((c,i)=>momentCard(c,i)).join("")}</div>`:""}
      <button id="askAgain" class="mt-7 text-[12.5px] text-muted hover:text-ink underline decoration-line">
        Ask something else
      </button>
    </div>`;
  $$("#answer .source, #answer .cite").forEach(el=>
    el.onclick=()=>openMoment(cites,+el.dataset.n));
  $("#askAgain").onclick=()=>{
    clearAnswer();
    renderStarters(); qEl.focus();
  };
}

/* ---------- polling ----------
   Runs while anything is in flight, wherever you are, so every page's status is
   live. The ONE automatic navigation lives here: the first video to become
   searchable pulls you from Processing to Ask. It won't yank you out of Set up
   (you may still be adding) or interrupt Ask. */
function schedulePoll(){
  if(POLL){ clearInterval(POLL); POLL=null; }
  if(!CUR || !working().length) return;
  POLL=setInterval(async()=>{
    if(!CUR) return;
    const before=CUR.videos.map(v=>v.status+v.progress).join();
    const hadReady=ready().length;
    await refresh();
    if(CUR.videos.map(v=>v.status+v.progress).join()!==before && STEP===2 && !hadReady && ready().length){
      setStep(3);
    }
    if(!working().length){
      clearInterval(POLL); POLL=null;
      refreshSessions();
    }
  }, 2500);
}

/* ---------- adding videos (into THIS session) ----------
   Registering starts processing immediately. It deliberately does NOT navigate:
   jumping to Processing on the first file is what hid the upload rows for files
   2..n and made multi-file uploads look broken. */
async function register(body){
  const st=$("#ingestStatus");
  try{
    await apiJSON("/api/videos",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify(Object.assign({session_id:CUR.id}, body))});
    await refresh();
    schedulePoll();
    st.textContent="Processing started — add more, or press Next.";
    refreshSessions();
  }catch(e){ st.textContent="Couldn't add that: "+e.message; }
}

$("#ytBtn").onclick=async()=>{
  const u=$("#ytUrl").value.trim(); if(!u || !CUR) return;
  $("#ytBtn").disabled=true; $("#ytUrl").value="";
  await register({url:u});
  $("#ytBtn").disabled=false; $("#ytUrl").focus();   // ready for the next link
};
$("#ytUrl").addEventListener("keydown",e=>{ if(e.key==="Enter"){ e.preventDefault(); $("#ytBtn").click(); }});

$("#upFile").onchange=e=>{ uploadAll([...e.target.files]); e.target.value=""; };
const drop=$("#drop");
["dragenter","dragover"].forEach(ev=>drop.addEventListener(ev,e=>{
  e.preventDefault(); drop.classList.add("border-coral"); }));
["dragleave","drop"].forEach(ev=>drop.addEventListener(ev,e=>{
  e.preventDefault(); drop.classList.remove("border-coral"); }));
drop.addEventListener("drop",e=>{
  const files=[...(e.dataTransfer?.files||[])].filter(f=>f.type.startsWith("video/"));
  if(files.length) uploadAll(files);
});

/* One row per file with real byte progress. Each file registers — and so starts
   processing — as soon as ITS bytes land, rather than waiting for the batch. */
async function uploadAll(files){
  if(!CUR) return;
  UPLOADING+=files.length; renderStep1Foot();
  for(const f of files){
    if(f.size > MAXMB*1024*1024){
      $("#ingestStatus").textContent=`“${f.name}” is over the ${MAXMB} MB limit.`;
      UPLOADING--; renderStep1Foot();
      continue;
    }
    const id="up"+Math.random().toString(36).slice(2,8);
    $("#upList").insertAdjacentHTML("beforeend",
      `<div id="${id}" class="text-[11.5px]">
         <div class="flex gap-2"><span class="truncate flex-1">${esc(f.name)}</span><span class="tabular-nums" data-pct>0%</span></div>
         <div class="h-1 bg-paper2 rounded-full mt-1 overflow-hidden"><div class="h-full bg-coral rounded-full" style="width:0" data-bar></div></div>
       </div>`);
    const row=$("#"+id);
    try{
      // Hash small-enough files up front so an identical re-upload skips the
      // upload AND the re-embed (crypto.subtle needs the whole file in memory).
      const sha = f.size <= 300*1024*1024 ? await sha256Hex(f) : null;
      const p=await apiJSON("/api/videos/presign",{method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({filename:f.name, content_type:f.type||"video/mp4", size:f.size, sha256:sha})});
      if(p.mode==="exists"){
        // identical content already indexed — link the existing video, no upload
        await apiJSON(`/api/sessions/${encodeURIComponent(CUR.id)}/videos`,{method:"POST",
          headers:{"Content-Type":"application/json"}, body:JSON.stringify({video_id:p.video_id})});
        row.remove();
        $("#ingestStatus").textContent="Already indexed — added instantly.";
        await refresh(); refreshSessions();
        continue;
      }
      await putWithProgress(p.url, p.headers, f, pct=>{
        row.querySelector("[data-bar]").style.width=Math.round(pct*100)+"%";
        row.querySelector("[data-pct]").textContent=Math.round(pct*100)+"%";
      });
      await register({video_id:p.video_id, key:p.key, title:f.name.replace(/\.[^.]+$/,"")});
      row.remove();
    }catch(e){
      row.querySelector("[data-pct]").textContent="failed";
      row.querySelector("[data-pct]").className="text-coral2";
      $("#ingestStatus").textContent="Upload failed: "+e.message;
    }finally{
      UPLOADING--; renderStep1Foot();
    }
  }
}

/* ---------- placeholder ---------- */
function ghostIdle(){ return !qEl.value && document.activeElement!==qEl; }
function ghostSync(){ $("#ghost").classList.toggle("hide", !ghostIdle()); }

boot();
