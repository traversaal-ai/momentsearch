/* /app — ONE workspace, one page.
 *
 * Left rail (permanent): add videos on top → your videos in the middle → the
 * ask button at the bottom. Right pane: whichever of three things your videos
 * currently justify —
 *
 *     add         nothing indexed and nothing in flight  → "add a video"
 *     processing  something indexing (or all of it failed) → the stage track
 *     ask         at least one video searchable            → ask + answer
 *
 * NO SESSIONS IN THE UI. Sessions implied a memory this app doesn't have: every
 * question is retrieved and answered on its own, and history is never fed to the
 * model. So the folder-of-videos concept is gone from the surface. One session
 * still exists in the database because that is what carries the streaming stage
 * events and stores each answer with its citations (POST
 * /api/sessions/{id}/ask_stream); `boot()` keeps exactly one and pulls every
 * video the account owns into it, so nothing you indexed can become invisible.
 *
 * THE CHECKBOXES ARE THE SCOPE. All searchable videos are checked by default;
 * unchecking one leaves it out of the next question without deleting anything.
 * The selection persists in localStorage, because losing a curated scope on
 * reload is worse than remembering it.
 *
 * ONE ANSWER ON SCREEN. Asking replaces the last answer; there is no history
 * strip. The server still stores every turn (and never feeds prior turns to the
 * model), so nothing is lost — it just isn't surfaced as chips that looked like
 * a thread the model could follow.
 *
 * Single-user, so nothing gates this page (see ui/common.js).
 */
let WS=null;                     // the one session: {id, videos, messages, …}
let POLL=null, MAXMB=2048;
let OFF=new Set();               // video ids explicitly EXCLUDED from search
let DELETING=new Set();          // ids with a delete in flight (survives repaints)
let SHOWN=null;                  // the answer on screen ({q, message}) or null
let UPLOADING=0;
let GD=null;                     // Drive import config from /api/config, or null when off

wireModal();
$("#avatar").textContent=USER_NAME.trim()[0].toUpperCase();
$("#whoName").textContent=USER_NAME;

const vids    = ()=> WS ? WS.videos : [];
const ready   = ()=> vids().filter(v=>v.status==="indexed");
const working = ()=> vids().filter(v=>INFLIGHT.includes(v.status));
const failed  = ()=> vids().filter(v=>v.status==="failed");
const chosen  = ()=> ready().filter(v=>!OFF.has(v.id));

/* Which pane the right side shows. Pure function of the videos — see the module
   note. `failed but nothing ready` stays on processing because that is where the
   error and its retry live; bouncing to "add a video" would hide the reason. */
function view(){
  if(ready().length) return "ask";
  if(vids().length)  return "processing";
  return "add";
}
function render(){
  const v=view();
  document.body.dataset.view=v;
  renderVideoList();
  if(v==="processing") renderPipeline();
  if(v==="ask"){ renderAskHead(); renderStarters(); renderIndexingStrip(); }
  $("#sampleHatch").classList.toggle("hidden", vids().length>0);
}

/* ---------- excluded-video memory ---------- */
const OFF_KEY="ms_excluded_videos";
function loadOff(){
  try{ OFF=new Set(JSON.parse(localStorage.getItem(OFF_KEY)||"[]")); }catch{ OFF=new Set(); }
}
function saveOff(){
  try{ localStorage.setItem(OFF_KEY, JSON.stringify([...OFF])); }catch{}
}

/* ---------- speaker-recognition preference ----------
   Sticky per browser, like the excluded-video scope above. It describes the
   video you add NEXT, so it has to survive a reload: otherwise you set it,
   come back, and quietly add the next video with the wrong setting. It is a
   user preference and NOT a library setting — restoring it never re-indexes
   anything, and never touches a video that is already queued or indexed. */
const DIA_KEY="ms_speaker_recognition";
function loadDiarizePref(){
  const chk=$("#diarizeChk");
  if(!chk || chk.disabled) return;          // no key -> stays off, nothing to restore
  try{ chk.checked = localStorage.getItem(DIA_KEY)==="1"; }catch{}
}
function saveDiarizePref(){
  const chk=$("#diarizeChk");
  if(!chk) return;
  try{ localStorage.setItem(DIA_KEY, chk.checked?"1":"0"); }catch{}
}

/* ---------- the upload size cap ----------
   MAX_UPLOAD_MB on the server (2048 by default ≈ a 90-minute video at 720p).
   The server enforces it three times — presign, direct upload, and register's
   HEAD verify — so the checks below exist only to fail FAST and locally, before
   the browser spends minutes pushing bytes that get rejected on arrival.

   Both the number and the rejection carry the EXACT figure. "File too large" is
   the version you can't act on: without knowing the cap you can't tell whether
   to trim the video or give up, and without knowing the file's own size you
   can't tell if you're 20 MB over or 2 GB over. */
function limitText(){
  const gb = MAXMB/1024;
  return MAXMB >= 1024
    ? `${MAXMB} MB (${Number.isInteger(gb) ? gb : gb.toFixed(1)} GB)`
    : `${MAXMB} MB`;
}
const overLimit = bytes => bytes > MAXMB*1024*1024;
const asMB = bytes => Math.round(bytes/(1024*1024)).toLocaleString();
const tooBig = (name, bytes) =>
  `“${name}” is ${asMB(bytes)} MB, over the ${MAXMB} MB limit.`;

/* Said in two places, because the empty-state card and the add panel are each
   somebody's first sight of the uploader. Runs now with the built-in fallback
   so the figure is never blank, then again once /api/config reports the
   server's real MAX_UPLOAD_MB (which is the one actually enforced). */
function renderLimit(){
  const drop=$("#upLimit"), card=$("#dropLimit");
  if(drop) drop.textContent=`Up to ${limitText()} each, about a 90-minute video at 720p`;
  if(card) card.textContent=`Up to ${limitText()} each.`;
}
renderLimit();

/* ---------- which model is answering, and can we diarize ---------- */
(async function loadConfig(){
  try{
    const c=await apiJSON("/api/config"); const b=$("#llmBadge");
    MAXMB=c.max_upload_mb||MAXMB;
    renderLimit();
    // Speaker recognition is Gemini-only: disable the box and say why, rather
    // than letting it look available and silently do nothing.
    const dchk=$("#diarizeChk"), dhint=$("#diarizeHint");
    if(dchk && !c.diarize_available){
      dchk.disabled=true; dchk.checked=false;
      if(dhint) dhint.classList.remove("hidden");
      $("#diarizeRow").classList.add("opacity-60","cursor-not-allowed");
    }
    // Restore the sticky preference only AFTER the availability check above: a
    // switch that is disabled for a missing key must stay off no matter what
    // the last session preferred, or it would read as on and do nothing.
    loadDiarizePref();
    if(dchk) dchk.addEventListener("change", saveDiarizePref);
    // The button is always on screen; this only decides whether clicking opens
    // Google's picker or explains what's missing (see its handler).
    if(c.gdrive && c.gdrive.enabled) GD=c.gdrive;
    if(c.llm_configured){
      b.textContent="LLM: "+(c.llm_model||c.llm_provider);
      b.className="md:ml-auto text-xs px-3 py-1 rounded-full border border-[#cfe8d6] text-[#1f7a43] hidden sm:inline";
    } else {
      b.textContent="No LLM, moments only";
      b.className="md:ml-auto text-xs px-3 py-1 rounded-full border border-[#e7c46a] text-[#8a6d1a] bg-[#fbf2d8] hidden sm:inline";
    }
    renderSetup(c.setup);
  }catch{ $("#llmBadge").textContent="API offline"; }
})();

/* ---------- setup status: what keys are still missing ----------
   Draws the header pill (green/amber/coral dot + popover checklist) and, only for
   BLOCKING gaps, the slim bar under the header. One data source: /api/config.setup
   (src/setup_check.py). Non-blocking gaps (no LLM, no Gemini) just tint the dot —
   they never drop the bar, so the app doesn't nag about optional features. */
function renderSetup(s){
  const wrap=$("#setupWrap"); if(!wrap||!s) return;
  const issues=s.issues||[];
  const blocking=issues.filter(i=>i.level==="blocking");
  const degraded=issues.filter(i=>i.level==="degraded");
  wrap.classList.remove("hidden");

  const dot=$("#setupDot"), txt=$("#setupPillText");
  if(blocking.length){ dot.className="w-2 h-2 rounded-full bg-coral"; txt.textContent="Setup"; }
  else if(degraded.length){ dot.className="w-2 h-2 rounded-full bg-[#e0a12a]"; txt.textContent="Setup"; }
  else { dot.className="w-2 h-2 rounded-full bg-[#2f9e57]"; txt.textContent="Ready"; }

  $("#setupPop").innerHTML = issues.length
    ? `<div class="text-[10.5px] uppercase tracking-wide text-muted font-600 mb-1.5">Finish setup</div>`
      + issues.map(i=>{
          const blk=i.level==="blocking";
          const envs=i.env.map(e=>`<code class="bg-paper2 rounded px-1 py-0.5 text-[10.5px]">${esc(e)}</code>`).join(" ");
          return `<div class="py-1.5 border-b border-line last:border-0">
            <div class="flex items-center gap-1.5 text-[12px] font-600 ${blk?"text-coral2":"text-[#8a6d1a]"}">
              <span aria-hidden="true">${blk?"●":"○"}</span>${esc(i.feature)}</div>
            <div class="text-[11px] text-muted mt-0.5 leading-snug">${esc(i.fix)}</div>
            <div class="mt-1 flex flex-wrap gap-1">${envs}</div>
          </div>`;
        }).join("")
      + `<div class="text-[10.5px] text-muted mt-2">Set these in your <code class="bg-paper2 rounded px-1">.env</code>, then restart.</div>`
    : `<div class="text-[12px] text-[#1f7a43] font-600 flex items-center gap-1.5"><span aria-hidden="true">✓</span>All keys set. Every feature is on.</div>`;

  const bar=$("#setupBar");
  if(blocking.length){
    $("#setupBarMsg").textContent = blocking.length===1
      ? `${blocking[0].feature}: set ${blocking[0].env.join(" + ")} to fix it`
      : `${blocking.length} things still need setup to unlock everything`;
    // Always show while a blocking gap exists. ✕ only clears it for THIS view — it
    // returns on the next load, on purpose: a real "ingest is off" problem must not
    // be permanently dismissable, or you'd hide it once and forget the missing key.
    // The always-on pill (above) is the quiet persistent home; this is the nudge.
    bar.classList.remove("hidden");
  } else {
    bar.classList.add("hidden");
  }
}

// Pill opens the checklist; click-away and Esc close it.
(function wireSetup(){
  const pill=$("#setupPill"), pop=$("#setupPop");
  if(!pill||!pop) return;
  const open=()=>{ pop.classList.remove("hidden"); pill.setAttribute("aria-expanded","true"); };
  const close=()=>{ pop.classList.add("hidden"); pill.setAttribute("aria-expanded","false"); };
  pill.onclick=e=>{ e.stopPropagation(); pop.classList.contains("hidden")?open():close(); };
  document.addEventListener("click", e=>{ if(!pop.contains(e.target)&&e.target!==pill) close(); });
  document.addEventListener("keydown", e=>{ if(e.key==="Escape") close(); });
  const fix=$("#setupBarFix"); if(fix) fix.onclick=e=>{ e.stopPropagation(); open(); };
  const x=$("#setupBarClose");
  // Clear it for now only — no persistence, so it's back on the next load while the
  // gap remains (the pill stays visible the whole time regardless).
  if(x) x.onclick=()=>{ $("#setupBar").classList.add("hidden"); };
})();

/* ---------- boot ----------
   Find or create the one workspace session, then make sure every video the
   account owns is a member of it. The reconcile matters: videos added before
   this UI existed live in other sessions, and without it they would silently
   vanish from a workspace that shows only one session's videos. */
async function boot(){
  const list=(await apiJSON("/api/sessions")).sessions||[];
  let target=list.find(s=>s.kind!=="demo");
  if(!target){
    target=await apiJSON("/api/sessions",{method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({title:"Workspace"})});
  }
  loadOff();
  await openWorkspace(target.id);
  schedulePoll();
}

async function openWorkspace(id){
  WS=await apiJSON("/api/sessions/"+encodeURIComponent(id));
  render();
}

/* NOTHING is ever added to the workspace automatically — not the sample talk,
   not videos found in older sessions. The only way a video appears here is you
   adding it (a URL, a file, or an identical re-upload that reuses an existing
   index). The sample corpus lives on /demo and stays there.

   Re-read the workspace and repaint. Never navigates: the view is derived, so a
   video finishing can change the pane, but nothing else moves. */
async function refresh(){
  if(!WS) return;
  const fresh=await apiJSON("/api/sessions/"+encodeURIComponent(WS.id));
  WS.videos=fresh.videos; WS.messages=fresh.messages;
  render();
}

/* ---------- the video rail ---------- */
function renderVideoList(){
  const box=$("#vidList"), all=vids();
  const r=ready(), w=working(), f=failed();
  $("#vidCount").textContent = all.length
    ? `${r.length} searchable${chosen().length!==r.length?` · ${chosen().length} selected`:""}${w.length?` · ${w.length} indexing`:""}`
    : "none yet";
  $("#sideCount").textContent = all.length ? `Videos · ${chosen().length}/${r.length}` : "Videos";

  if(!all.length){
    box.innerHTML=`<p class="text-[12px] text-muted px-1 py-2">No videos yet. Add one above.</p>`;
    return;
  }
  box.innerHTML=[...r,...w,...f].map(videoRow).join("");

  box.querySelectorAll(".vidsel").forEach(c=>c.onchange=()=>{
    if(c.checked) OFF.delete(c.dataset.sel); else OFF.add(c.dataset.sel);
    saveOff(); renderVideoList(); renderAskHead();
  });
  wireRowButtons(box);
}

/* The stamped receipt for one video: what it was ACTUALLY queued with, not what
   the switch currently says. Every row renders it, both states, so a mixed list
   is readable without opening anything — that is the whole point of stamping.
   Inert by design: a record, never a control (see .spk in app.css). */
function spkBadge(v){
  return v.diarize
    ? `<span class="spk spk-on">Speakers on</span>`
    : `<span class="spk spk-off">Speakers off</span>`;
}

function videoRow(v){
  const b=statusBadge(v);
  // A delete in flight owns the row: no checkbox to toggle, no second ✕ to press,
  // and it SAYS what is happening. Deleting is slow (one storage round trip per
  // frame), so silence here is what made it look broken.
  if(DELETING.has(v.id)){
    return `<div class="vidRow bg-card border border-coral/40 rounded-xl p-2 flex gap-2 items-center opacity-70">
      <span class="w-4 flex justify-center shrink-0"><span class="dotPulse"></span></span>
      <div class="min-w-0 flex-1">
        <div class="text-[12px] font-600 leading-snug line-clamp-1 text-ink">${esc(v.title||v.id)}</div>
        <div class="text-[10.5px] text-coral2 mt-0.5">Deleting: removing frames and search index…</div>
      </div>
    </div>`;
  }
  const on = v.status==="indexed" && !OFF.has(v.id);
  const busy=INFLIGHT.includes(v.status);
  const pct=v.progress ? Math.round(v.progress*100) : null;
  const yid=v.id.startsWith("yt_") ? v.id.slice(3) : null;
  // YouTube -> its CDN still (instant, free). Upload -> frame 0 from the API once
  // indexed (v.thumbnail). Upload still processing has no frame yet -> a plain box,
  // not an empty gap.
  const thumbSrc = yid ? `https://img.youtube.com/vi/${esc(yid)}/default.jpg` : (v.thumbnail || null);
  const thumb = thumbSrc
    ? `<img loading="lazy" src="${esc(thumbSrc)}" class="w-11 h-7 object-cover rounded border border-line shrink-0" onerror="this.style.opacity=0">`
    : `<div class="w-11 h-7 rounded border border-line bg-paper2 shrink-0 flex items-center justify-center">${
        busy ? `<span class="dotPulse"></span>` : `<span class="text-muted text-[9px] leading-none">🎞</span>`
      }</div>`;
  // Only a searchable video gets a checkbox — there is nothing to include yet
  // for one that's still indexing, and a dead control invites a wrong guess.
  const check = v.status==="indexed"
    ? `<input type="checkbox" class="vidsel accent-coral w-4 h-4 shrink-0 cursor-pointer" data-sel="${esc(v.id)}" ${on?"checked":""}
         title="Include this video in your questions (unchecking doesn't delete it)">`
    : `<span class="w-4 shrink-0 text-center text-[11px] ${b.c}">${b.icon}</span>`;
  // A bar ONLY when the worker reports a real number. No percentage means no
  // bar: the status line under the title already names the stage, and the
  // sweeping placeholder that used to fill the gap was motion standing in for
  // information we don't have.
  const rail = busy && pct!==null
    ? `<div class="h-[3px] bg-paper2 rounded-sm mt-1.5 overflow-hidden">
         <div class="h-full bg-coral rounded-sm transition-[width] duration-500" style="width:${pct}%"></div>
       </div>` : "";
  return `<div class="vidRow group relative bg-card border rounded-xl p-2 flex gap-2 items-center ${
      on ? "border-coral/40" : "border-line"
    }" data-off="${v.status==="indexed"&&!on?1:0}">
    ${check}
    ${thumb}
    <div class="min-w-0 flex-1">
      <div class="text-[12px] font-600 leading-snug line-clamp-2 text-ink">${esc(v.title||v.id)}</div>
      <div class="text-[10.5px] ${b.c} mt-0.5 flex items-center gap-1.5 min-w-0">
        <span class="truncate">${esc(b.label)}${pct!==null&&busy?` · ${pct}%`:""}</span>
        ${spkBadge(v)}
      </div>
      ${rail}
      ${v.status==="indexed" && v.transcript_note
        ? `<div class="text-[10px] text-[#8a6d1a] mt-1 leading-snug line-clamp-2" title="${esc(v.transcript_note)}">⚠ ${esc(v.transcript_note)}</div>`
        : ""}
    </div>
    <div class="flex flex-col gap-0.5 shrink-0">
      ${v.status==="failed"?`<button class="text-muted hover:text-coral2 text-[11px]" data-retry="${esc(v.id)}" title="Retry">↻</button>`:""}
      ${v.is_sample?"":`<button class="w-5 h-5 rounded flex items-center justify-center text-muted hover:text-white hover:bg-coral2 text-[11px] transition"
         data-del="${esc(v.id)}" data-title="${esc(v.title||v.id)}"
         title="Delete this video, removes its frames and search index">✕</button>`}
    </div>
  </div>`;
}

function wireRowButtons(box){
  box.querySelectorAll("[data-retry]").forEach(b=>b.onclick=async()=>{
    await api("/api/videos/"+encodeURIComponent(b.dataset.retry)+"/retry",{method:"POST"});
    await refresh(); schedulePoll();
  });
  box.querySelectorAll("[data-del]").forEach(b=>b.onclick=async()=>{
    const id=b.dataset.del;
    if(DELETING.has(id)) return;                    // already going
    if(!confirm(`Delete “${b.dataset.title}”?\n\nThis removes its frames and search index for good. This can't be undone.`)) return;
    // Held in state, not just on the button: the list repaints (polling, another
    // video finishing) and a re-render used to wipe the "…" — which is exactly
    // why a delete that was still running looked like it had done nothing.
    DELETING.add(id);
    renderVideoList();
    try{
      await apiJSON("/api/videos/"+encodeURIComponent(id),{method:"DELETE"});
      OFF.delete(id); saveOff();
    }catch(e){
      alert("Couldn't delete that: "+e.message);
    }finally{
      DELETING.delete(id);
      await refresh();                              // the row goes when it's really gone
    }
  });
}

$("#selAll").onclick =()=>{ OFF.clear(); saveOff(); renderVideoList(); renderAskHead(); };
$("#selNone").onclick=()=>{ ready().forEach(v=>OFF.add(v.id)); saveOff(); renderVideoList(); renderAskHead(); };

/* ---------- processing ---------- */
const STAGES=[
  {nm:"queued",  has:["pending","queued"]},
  {nm:"fetch",   has:["fetching"]},
  {nm:"frames",  has:["sampling"]},
  {nm:"embed",   has:["embedding"]},
];
const stageIndex = s => { const i=STAGES.findIndex(x=>x.has.includes(s)); return i<0?STAGES.length:i; };

/* One card per video: the four-stage track, or the error and its retry. Shared by
   the full processing page and the compact strip on the ask page, so a video's
   progress reads identically wherever you happen to be looking. */
function pipelineCards(list, pad="p-4"){
  return list.map(v=>{
    const cur=stageIndex(v.status), done=v.status==="indexed"||v.status==="skipped";
    const pct=v.progress ? Math.round(v.progress*100) : null;
    const track=STAGES.map((s,i)=>{
      const on = done||i<cur ? "done" : (i===cur ? "now" : "ahead");
      const indet = on==="now" && pct===null ? " data-indet" : "";
      const style = on==="now" && pct!==null ? ` style="--p:${pct}%"` : "";
      return `<span class="stage" data-on="${on}"${indet}${style}>
        <span class="bar"><i></i></span><span class="nm">${s.nm}</span></span>`;
    }).join("");
    const b=statusBadge(v);
    return `<div class="bg-card border border-line rounded-2xl ${pad}">
      <div class="flex items-center gap-2">
        <div class="text-[13px] font-600 leading-snug line-clamp-1 min-w-0 flex-1">${esc(v.title||v.id)} ${spkBadge(v)}</div>
        <div class="text-[11.5px] ${b.c} shrink-0">${b.icon} ${esc(b.label)}${pct!==null&&!done?` · ${pct}%`:""}</div>
      </div>
      ${v.status==="failed"
        ? `<div class="mt-2.5 text-[12.5px] text-coral2 leading-relaxed">${esc(v.error||"Indexing failed.")}</div>
           <button class="mt-2 text-[12px] font-semibold text-coral2 hover:text-coral underline decoration-line" data-retry="${esc(v.id)}">Try again</button>`
        : `<div class="flex gap-2 mt-2.5">${track}</div>`}
    </div>`;
  }).join("");
}

function renderPipeline(){
  const w=working(), f=failed();
  $("#procSub").textContent = w.length
    ? "Sampling frames, embedding them and pulling the transcript. You can leave this page. It keeps going."
    : f.length ? "Nothing finished indexing. Here's what happened."
    : "Nothing is being processed right now.";
  $("#pipeline").innerHTML=pipelineCards(vids());
  wireRowButtons($("#pipeline"));
  $("#procFoot").innerHTML = w.length
    ? `<p class="text-[12.5px] text-muted">This page turns into the question box the moment the first video is ready.</p>`
    : "";
}

/* The ask page's own progress. Anything in flight (or freshly failed) shows here,
   because once one video is searchable the pane never leaves "ask" — pulling you
   away mid-question to show a progress bar would be worse than not showing it. */
function renderIndexingStrip(){
  const strip=$("#indexingStrip"); if(!strip) return;
  const w=working(), f=failed();
  const show=w.length || f.length;
  strip.classList.toggle("hidden", !show);
  if(!show){ strip.open=false; return; }

  const named=[...w,...f];
  if(w.length){
    // Name the actual stage of the furthest-along one, so the line says something
    // real rather than a generic "processing".
    const lead=w.reduce((a,v)=>stageIndex(v.status)>=stageIndex(a.status)?v:a, w[0]);
    const pct=lead.progress ? ` ${Math.round(lead.progress*100)}%` : "";
    const stage=(STAGES[stageIndex(lead.status)]||{nm:"finishing"}).nm;
    $("#indexingLine").textContent =
      `${w.length} video${w.length===1?"":"s"} indexing: ${stage}${pct}` +
      (f.length ? ` · ${f.length} failed` : "");
  }else{
    $("#indexingLine").textContent = `${f.length} video${f.length===1?"":"s"} failed to index`;
  }
  $("#askPipeline").innerHTML=pipelineCards(named,"p-3");
  wireRowButtons($("#askPipeline"));
}

/* ---------- ask ---------- */
/* YouTube titles carry a channel tail after a pipe that only eats the line, so
   trim there first and then to length. Used by the ask box's placeholder. */
function short(t,max=34){
  let s=String(t||"").split(/\s+[|·—]\s+/)[0].trim();
  return s.length>max ? s.slice(0,max).replace(/\s+\S*$/,"")+"…" : s;
}

/* The note under the box only speaks up when asking would go nowhere. Narrating
   the normal case ("Searching all 1 video · 1 still indexing") repeated the
   rail's own checkboxes and the indexing strip right above it, so it was noise. */
function renderAskHead(){
  const r=ready().length, c=chosen();
  // The headline is fixed in app.html and stays fixed — nothing to render here.
  // no trailing "…" on a named one: the name may already end in an ellipsis
  $("#ghost").textContent = c.length===1
    ? `Ask about “${short(c[0].title||c[0].id,26)}”`
    : "Ask about your videos…";
  $("#scopeNote").textContent =
    !r ? "Nothing searchable yet"
      : !c.length ? "No videos checked. Check one in the rail to search"
      : "";
}

function renderStarters(){
  const box=$("#starters");
  if(SHOWN || !chosen().length){ box.innerHTML=""; return; }
  // The first one works on ANY video, so a first click can't come back empty.
  const S=["What is this video about?","Show me a diagram","What's on the busiest slide?"];
  box.innerHTML=S.map(s=>`<span class="chip">${esc(s)}</span>`).join("");
  $$("#starters .chip").forEach(c=>c.onclick=()=>{ qEl.value=c.textContent; ghostSync(); send(); });
}

/* Stage names, in plain language about YOUR question. The server emits these as
   each stage actually begins, so the line you're reading is the work in flight. */
const STAGE_WORDS={
  embedding: "Understanding your question",
  searching: "Searching what's on screen and what's said",
  ranking:   "Picking the strongest moments",
  reading:   "Opening those moments",
  answering: "Writing the answer with citations",
};
let ASK_STAGES=[];

/* The question, echoed the instant you press enter and left in place through the
   wait, the answer and any error — identical markup in all three, so nothing
   shifts under you. */
function askedBlock(q){
  return `<div class="flex items-baseline gap-2 mb-4">
    <span class="text-[10px] uppercase tracking-wider text-muted font-semibold shrink-0">You asked</span>
    <span class="text-[14.5px] font-600 display">${esc(q)}</span>
  </div>`;
}

function renderStages(done){
  const box=$("#stages"); if(!box) return;
  box.innerHTML=ASK_STAGES.map((s,i)=>{
    const busy = i===ASK_STAGES.length-1 && !done;
    return `<div class="flex items-center gap-2 ${busy?"":"opacity-60"}">
      <span class="w-4 flex justify-center shrink-0">${busy?'<span class="dotPulse"></span>':'<span class="text-[#1f7a43] text-[11px]">✓</span>'}</span>
      <span class="text-[12.5px] ${busy?"text-ink font-600":"text-muted"}">${esc(s.label)}</span>
      ${s.detail?`<span class="text-[11px] text-muted truncate">· ${esc(s.detail)}</span>`:""}
      ${s.ms!=null?`<span class="text-[11px] text-muted ml-auto tabular-nums shrink-0">${(s.ms/1000).toFixed(1)}s</span>`:""}
    </div>`;
  }).join("");
}

function clearAnswer(){
  SHOWN=null;
  $("#answer").innerHTML="";
  $("#askHead").classList.remove("hidden");
  document.body.dataset.ans="0";
  askTop();
}

/* The pane scrolls as one, so its scroll position now OUTLIVES a content swap.
   Every swap has to put it back at the top, or a new answer arrives already
   scrolled past — the old nested scroller reset itself for free. */
function askTop(){ const v=$("#viewAsk"); if(v) v.scrollTop=0; }

/* Ask over SSE so the stage list is live. fetch + a stream reader, not
   EventSource, because the question travels in a POST body. Falls back to the
   plain endpoint if streaming is unavailable — progress display must never be
   the thing that breaks asking. */
async function askStreaming(q){
  const body=JSON.stringify({question:q, video_ids:chosen().map(v=>v.id)});
  let r=null;
  try{
    r=await fetch(`/api/sessions/${encodeURIComponent(WS.id)}/ask_stream`,
      {method:"POST",headers:{"Content-Type":"application/json"},body});
  }catch{ r=null; }
  if(!r || !r.ok || !r.body){
    return apiJSON(`/api/sessions/${encodeURIComponent(WS.id)}/ask`,
      {method:"POST",headers:{"Content-Type":"application/json"},body});
  }
  const reader=r.body.getReader(), dec=new TextDecoder();
  let buf="", started=0, out=null, err=null;
  for(;;){
    const {value,done}=await reader.read();
    if(done) break;
    buf+=dec.decode(value,{stream:true});
    const frames=buf.split("\n\n"); buf=frames.pop();
    for(const f of frames){
      const line=f.split("\n").find(l=>l.startsWith("data:"));
      if(!line) continue;
      let ev; try{ ev=JSON.parse(line.slice(5).trim()); }catch{ continue; }
      if(ev.type==="stage"){
        const now=performance.now();
        if(ASK_STAGES.length) ASK_STAGES[ASK_STAGES.length-1].ms=now-started;
        started=now;
        ASK_STAGES.push({label:STAGE_WORDS[ev.stage]||ev.stage, detail:ev.detail||"", ms:null});
        renderStages(false);
      }else if(ev.type==="done"){
        if(ASK_STAGES.length) ASK_STAGES[ASK_STAGES.length-1].ms=performance.now()-started;
        renderStages(true); out=ev;
      }else if(ev.type==="error"){ err=ev.detail||"The server couldn't answer that."; }
    }
  }
  if(err) throw new Error(err);
  if(!out) throw new Error("The answer stream ended before an answer arrived.");
  return out;
}

const qEl=$("#q");
function autogrow(){ qEl.style.height="auto"; qEl.style.height=Math.min(qEl.scrollHeight,150)+"px"; }
qEl.addEventListener("input",()=>{ autogrow(); ghostSync(); });
qEl.addEventListener("focus",ghostSync);
qEl.addEventListener("blur",ghostSync);
qEl.addEventListener("keydown",e=>{ if(e.key==="Enter"&&!e.shiftKey){ e.preventDefault(); send(); }});
$("#go").onclick=send;

let _sending=false;
async function send(){
  const q=qEl.value.trim();
  if(!q || !WS || _sending) return;
  if(!chosen().length){ renderAskHead(); return; }
  _sending=true;
  qEl.value=""; autogrow(); ghostSync();
  $("#go").disabled=true; $("#go").classList.add("busy");
  $("#askHead").classList.add("hidden");
  $("#starters").innerHTML="";
  document.body.dataset.ans="1";
  ASK_STAGES=[];
  $("#answer").innerHTML=`
    <div class="ans max-w-3xl mx-auto pt-2">
      ${askedBlock(q)}
      <div id="stages" class="bg-card border border-line rounded-2xl p-4 space-y-2.5 mb-5"></div>
      <div class="space-y-3">
        <div class="shimmer h-4 w-2/3 rounded"></div>
        <div class="shimmer h-4 w-full rounded"></div>
        <div class="shimmer h-4 w-4/6 rounded"></div>
      </div>
    </div>`;
  askTop();
  try{
    const d=await askStreaming(q);
    SHOWN={q, message:d.message};
    renderAnswer();
    refresh();                      // picks up the stored turn
  }catch(e){
    SHOWN=null;
    $("#answer").innerHTML=`<div class="ans max-w-3xl mx-auto pt-2">
      ${askedBlock(q)}
      <div class="bg-[#fbf2d8] border border-[#e7c46a] rounded-2xl p-4">
        <div class="text-[13px] font-600 text-[#8a6d1a] mb-1">Couldn't answer that</div>
        <p class="text-[13px] text-[#6b5a1a] leading-relaxed">${esc(e.message)}</p>
      </div></div>`;
    $("#askHead").classList.remove("hidden");
  }finally{
    _sending=false;
    $("#go").disabled=false; $("#go").classList.remove("busy");
  }
}

function renderAnswer(){
  if(!SHOWN){ clearAnswer(); return; }
  document.body.dataset.ans="1";
  $("#askHead").classList.add("hidden");
  const m=SHOWN.message, cites=m.citations||[], note=(m.meta||{}).note;
  $("#answer").innerHTML=`
    <div class="ans max-w-3xl mx-auto pt-2">
      ${askedBlock(SHOWN.q)}
      <div class="prose-body text-[15px]">${renderMarkdown(m.content)}</div>
      ${note?`<p class="text-[11px] text-muted mt-2">${esc(note)}</p>`:""}
      ${cites.length?`
        <div class="text-[11px] uppercase tracking-wider text-muted font-semibold mt-6 mb-2">Moments</div>
        <div class="grid sm:grid-cols-2 xl:grid-cols-3 gap-3">${cites.map((c,i)=>momentCard(c,i)).join("")}</div>`:""}
      <button id="askAgain" class="mt-7 text-[12.5px] text-muted hover:text-ink underline decoration-line">Ask something else</button>
    </div>`;
  $$("#answer .source, #answer .cite").forEach(el=>
    el.onclick=()=>openMoment(cites,+el.dataset.n));
  $("#askAgain").onclick=()=>{ clearAnswer(); renderStarters(); qEl.focus(); };
}

/* ---------- polling ---------- */
function schedulePoll(){
  if(POLL){ clearInterval(POLL); POLL=null; }
  if(!WS || !working().length) return;
  POLL=setInterval(async()=>{
    if(!WS) return;
    const before=vids().map(v=>v.status+v.progress).join();
    await refresh();
    if(vids().map(v=>v.status+v.progress).join()===before) return;
    if(!working().length){ clearInterval(POLL); POLL=null; $("#ingestStatus").textContent=""; }
  }, 2500);
}

/* ---------- adding videos ---------- */
async function register(body){
  const st=$("#ingestStatus");
  // Applies to whatever you add next — the checkbox in the rail. The server
  // rejects it with "Gemini key is missing" if the key isn't set.
  const speaker_recognition=!!($("#diarizeChk") && $("#diarizeChk").checked);
  try{
    await apiJSON("/api/videos",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify(Object.assign({session_id:WS.id, speaker_recognition}, body))});
    st.textContent="Indexing started.";
    await refresh();
    schedulePoll();
  }catch(e){ st.textContent="Couldn't add that: "+e.message; }
}

$("#ytBtn").onclick=async()=>{
  const u=$("#ytUrl").value.trim(); if(!u || !WS) return;
  $("#ytBtn").disabled=true; $("#ytUrl").value="";
  await register({url:u});
  $("#ytBtn").disabled=false; $("#ytUrl").focus();      // ready for the next link
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

/* ---------- Google Drive import ----------
   Deliberately NOT a server-side integration. The user consents in Google's own
   popup, picks in Google's own file browser, and THIS PAGE downloads the bytes
   and hands them to uploadAll() — the same path a dropped file takes. So:
     · no refresh token is stored anywhere (an app with no sign-in must not hold
       a key to someone's whole Drive — see the README's auth warning)
     · the scope is drive.file, i.e. only the files picked here, which is also
       the scope that needs no Google verification or security assessment
     · the server needs no new endpoint and no new source type — an import
       arrives as an ordinary upload, with the same dedupe and size checks
   The cost of that simplicity: the file travels Drive → this browser → storage,
   so importing is no faster than uploading the file by hand. */
const GD_SCOPE="https://www.googleapis.com/auth/drive.file";
let GD_TOKEN=null;   // access token for this tab only; ~1h, re-requested silently

/* Google's scripts load on FIRST CLICK, not on page load — a workspace whose
   owner never touches Drive makes no request to Google at all. */
function loadScript(src){
  return new Promise((resolve,reject)=>{
    const have=[...document.scripts].find(s=>s.src===src);
    if(have){ have.dataset.ok ? resolve() : have.addEventListener("load",()=>resolve()); return; }
    const s=document.createElement("script");
    s.src=src; s.async=true;
    s.onload=()=>{ s.dataset.ok="1"; resolve(); };
    s.onerror=()=>reject(new Error("Couldn't reach Google. Check the connection."));
    document.head.appendChild(s);
  });
}

/* The consent popup. Google remembers the grant, so the second time this runs
   it returns silently and the user goes straight to the file list. */
async function gdriveToken(){
  if(GD_TOKEN) return GD_TOKEN;
  await loadScript("https://accounts.google.com/gsi/client");
  return new Promise((resolve,reject)=>{
    const client=google.accounts.oauth2.initTokenClient({
      client_id:GD.client_id, scope:GD_SCOPE,
      callback:r=>{
        if(r && r.access_token){ GD_TOKEN=r.access_token; resolve(r.access_token); }
        else reject(new Error("Google didn't grant access."));
      },
      error_callback:e=>reject(new Error(
        (e && (e.type==="popup_closed"||e.type==="popup_failed_to_open"))
          ? "Authorization window closed." : "Authorization failed.")),
    });
    client.requestAccessToken();
  });
}

/* Google's file browser. Resolves with the picked docs, or [] on cancel. */
async function gdrivePick(token){
  await loadScript("https://apis.google.com/js/api.js");
  await new Promise(res=>gapi.load("picker",res));
  return new Promise(resolve=>{
    const view=new google.picker.DocsView(google.picker.ViewId.DOCS_VIDEOS)
      .setIncludeFolders(true).setSelectFolderEnabled(false);
    new google.picker.PickerBuilder()
      .setOAuthToken(token)
      .setDeveloperKey(GD.api_key)
      // App id = the Cloud project number, which is the prefix of the OAuth
      // client id. Picker needs it to keep drive.file grants attached to this
      // app, and deriving it here saves a third env var that can drift.
      .setAppId(String(GD.client_id).split("-")[0])
      .addView(view)
      .enableFeature(google.picker.Feature.MULTISELECT_ENABLED)
      .setCallback(d=>{
        if(d.action===google.picker.Action.PICKED) resolve(d.docs||[]);
        else if(d.action===google.picker.Action.CANCEL) resolve([]);
      })
      .build().setVisible(true);
  });
}

/* Download one picked file into a File object the upload path already accepts. */
async function gdriveDownload(doc, token){
  const url="https://www.googleapis.com/drive/v3/files/"+
            encodeURIComponent(doc.id)+"?alt=media&supportsAllDrives=true";
  const r=await fetch(url,{headers:{Authorization:"Bearer "+token}});
  if(!r.ok){
    // These are permanent, not retryable: a clear sentence beats a status code.
    if(r.status===403) throw new Error("Drive refused the download (no permission, or its download quota is used up).");
    if(r.status===404) throw new Error("That file is no longer in Drive.");
    if(r.status===401){ GD_TOKEN=null; throw new Error("Google access expired. Click Import again."); }
    throw new Error("Drive download failed ("+r.status+").");
  }
  const blob=await r.blob();
  return new File([blob], doc.name||"video.mp4",
                  {type:doc.mimeType||blob.type||"video/mp4"});
}

$("#gdriveBtn").onclick=async()=>{
  if(!WS) return;
  // Never fail silently: if this button is reachable without configuration, say
  // what's missing instead of swallowing the click.
  if(!GD){
    $("#ingestStatus").textContent=
      "Google Drive import isn't configured. Set GDRIVE_CLIENT_ID and GDRIVE_API_KEY in .env (see .env.example), then restart.";
    return;
  }
  const btn=$("#gdriveBtn"), label=$("#gdriveLabel"), st=$("#ingestStatus");
  const said=label.textContent;
  btn.disabled=true; btn.classList.add("opacity-60");
  try{
    const token=await gdriveToken();
    label.textContent="Choose a video…";
    const docs=await gdrivePick(token);
    for(const d of docs){
      const size=Number(d.sizeBytes||0);
      // Google-native formats (Docs/Slides/…) have no bytes to fetch, and the
      // size check happens BEFORE the download — Drive tells us the size up
      // front, so an oversized file costs nothing instead of a wasted transfer.
      if(d.mimeType && !d.mimeType.startsWith("video/")){
        st.textContent=`“${d.name}” isn't a video file.`; continue;
      }
      if(overLimit(size)){
        st.textContent=tooBig(d.name, size); continue;
      }
      label.textContent="Fetching from Drive…";
      st.textContent=`Fetching “${d.name}” from Drive…`;
      try{
        const file=await gdriveDownload(d, token);
        st.textContent="";
        await uploadAll([file]);          // from here it IS a normal upload
      }catch(e){ st.textContent=e.message; }
    }
  }catch(e){ st.textContent=e.message; }
  finally{ btn.disabled=false; btn.classList.remove("opacity-60"); label.textContent=said; }
};

/* One row per file with real byte progress. Each file registers — and so starts
   indexing — as soon as ITS bytes land, not when the batch finishes. */
async function uploadAll(files){
  if(!WS) return;
  UPLOADING+=files.length;
  for(const f of files){
    if(overLimit(f.size)){
      $("#ingestStatus").textContent=tooBig(f.name, f.size);
      UPLOADING--; continue;
    }
    const id="up"+Math.random().toString(36).slice(2,8);
    $("#upList").insertAdjacentHTML("beforeend",
      `<div id="${id}" class="text-[11px]">
         <div class="flex gap-2"><span class="truncate flex-1">${esc(f.name)}</span><span class="tabular-nums" data-pct>0%</span></div>
         <div class="h-1 bg-paper2 rounded-full mt-1 overflow-hidden"><div class="h-full bg-coral rounded-full" style="width:0" data-bar></div></div>
       </div>`);
    const row=$("#"+id);
    try{
      // Hash small-enough files up front so an identical re-upload skips both the
      // upload and the re-embed.
      const sha = f.size <= 300*1024*1024 ? await sha256Hex(f) : null;
      const p=await apiJSON("/api/videos/presign",{method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({filename:f.name, content_type:f.type||"video/mp4", size:f.size, sha256:sha})});
      if(p.mode==="exists"){
        await apiJSON(`/api/sessions/${encodeURIComponent(WS.id)}/videos`,{method:"POST",
          headers:{"Content-Type":"application/json"}, body:JSON.stringify({video_id:p.video_id})});
        row.remove();
        $("#ingestStatus").textContent="Already indexed, added instantly.";
        await refresh();
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
    }finally{ UPLOADING--; }
  }
}

/* ---------- empty-state shortcuts ---------- */
$("#focusYt").onclick  =()=>{ $("#addBox").open=true; openDrawerOnPhone(); $("#ytUrl").focus(); };
$("#focusDrop").onclick=()=>{ $("#addBox").open=true; openDrawerOnPhone(); $("#upFile").click(); };

/* ---------- phone drawer ---------- */
function openDrawerOnPhone(){
  if(window.matchMedia("(max-width:767px)").matches) document.body.dataset.side="open";
}
$("#sideToggle").onclick=()=>{
  document.body.dataset.side = document.body.dataset.side==="open" ? "closed" : "open";
};

/* ---------- placeholder ---------- */
function ghostIdle(){ return !qEl.value && document.activeElement!==qEl; }
function ghostSync(){ $("#ghost").classList.toggle("hide", !ghostIdle()); }

boot();
